# USITC 产品级进口来源与关税研究 pipeline

给任意一个产品（一组 HTS 编码）做美国进口来源与关税研究：

```text
scope（这个产品是哪些 HTS 编码；用户本人批准）
  → study（年份、来源地、对比年份、事件窗口）
  → plan → acquire（DataWeb，带 gate）
  → analyze → charts → validate → report
```

每一步都有哈希和 manifest，图、表和结论都能追溯到同一批快照。旧脚本和旧交付物不删不改；这个目录是独立、可复现的版本。

## 1. 新研究 = 新 scope + 新 study

换产品不改 Python，也不改 `config/research.toml`，只写两份文件：

- **scope**：`scopes/<scope_id>/v<k>/reviewed.csv`，写明编码、适用日期、数量单位、纳入或排除及理由。研究者（或 agent）起草和审核，**用户本人**批准后（在对话里确认卡片，或自己在终端运行 `scope-approve`），才冻结成 `scope.csv` + `manifest.json`。规则见 [scopes/README.md](scopes/README.md)。
- **study**：`config/studies/<study_id>.toml`。只有 `[periods]` 影响查询；来源地、对比年份、事件窗口、事件标记随时可改，不用重新取数。字段和默认值见 skill 的 `references/study-design.md`。
- **study 的可选项**（默认关；只改 study_sha256，plan 和缓存不变，除非另写）：`[quantity] unit = "both"`（第一单位为主，kg 等第二单位并列在 `_2` 列，数量方向相反时报警；和 `"second"` 同一批查询，复用缓存）、`[timeline]`（关税时间线，按税号一层在 `config/tariff_timelines/<scope_id>.csv`，按国家一层在 `_country.csv`；03、09 图画竖线）、`[[segments]]`（编码分段并列）、`[brief] focal_split`（焦点/其他/总计三行，含计算关税美元）、`[drilldown] structure`（下钻菜单，读 scope `history/` 里缓存的 HTS REST 结构）、`[context] hts4` + `hts6`（HTS4 地图；会在 plan 里加 `context` 查询，**要用户另外批准，acquire 不取**）。

现有的两个研究都是**例子，不是模板**：

- 笔记本电脑：`scopes/laptops/v2/`（HTS6 847130）+ `config/studies/laptops-2017-2026.toml`，另有一个标注的 legacy replay（见第 10 节）。
- 锂离子电池：`scopes/lithium-ion/v1/`（HTS6 850760）+ `config/studies/lithium-ion-2017-2026.toml`。
- 锂离子电池 HTS10 + kg + 时间线 + 分段 + 下钻菜单（新功能样例）：`scopes/lithium-ion/v2/` + `config/studies/lithium-ion-hts10-both-2017-2026.toml`。

哪些 scope 已批准、哪些 study 能跑、还缺什么，用 `python run.py status` 看，不写在文档里。用户做过的判断记在 [decisions.md](decisions.md)，下次做相近产品先查这里。

## 2. 命令

在项目根目录运行。也可以用 skill 里的 `scripts/tariff_pipeline.py`：它按 skill 的 `config.json` 找到项目和解释器，并且只把 token 交给 `acquire`。

```powershell
# 1. 校验 reviewed scope，生成给批准人看的卡片（没有批准效力，也不会覆盖已有卡片）
python run.py scope-validate --scope scopes\<id>\v<k>\reviewed.csv `
  --approval-card scopes\<id>\v<k>\approval-card.json

# 2. 可选：用存档的历年 HTS 文件生成连续性证据，再用 --history-evidence 生成卡片
python run.py hts-history --scope scopes\<id>\v<k>\reviewed.csv `
  --catalog scopes\<id>\v<k>\history\catalog.csv --output scopes\<id>\v<k>\hts-history.json

# 3. 批准：用户在 PowerShell 或 cmd 里自己运行（Git Bash 要在前面加 winpty），按提示输入前 8 位
python run.py scope-approve --scope scopes\<id>\v<k>\reviewed.csv `
  --approval-card scopes\<id>\v<k>\approval-card.json --reviewer "<姓名>" `
  --continuity-unverified `
  --approved-scope scopes\<id>\v<k>\scope.csv --manifest scopes\<id>\v<k>\manifest.json
# 有连续性证据时，把 --continuity-unverified 换成 --history-evidence scopes\<id>\v<k>\hts-history.json
# 或者：agent 在对话里给用户看卡片，用户回复批准后，agent 在同一条命令后面加上
#   --chat-confirmation "<用户原话>" --confirm <scope_sha256 前 8 位>
# 项目想只认终端批准：config/research.toml 里写 [approval] allow_chat_confirmation = false

# 4. 查询计划：每个时期查哪些编码、几个查询、已缓存几个、要不要 token
python run.py plan --study config\studies\<study_id>.toml

# 5. 取数：需要 DATAWEB_TOKEN；所有查询都已缓存时不需要
python run.py acquire --plan outputs\runs\<study_id>\plan-<hash12>\query_plan.json

# 6. 分析、出图、验证、写报告；从不取数
python run.py all --study config\studies\<study_id>.toml

# 7. agent 写好 brief.md 之后：先 brief-check，再出最终报告 final_report.pdf（brief 正文 + 附录，用本机 Edge/Chrome 打印）
python run.py brief-check --study config\studies\<study_id>.toml
python run.py report-pdf --study config\studies\<study_id>.toml

# 时间线对照关税库（config/tariff_registry，D-026）；只读，不改任何文件
python run.py registry-check --study config\studies\<study_id>.toml

# 随时查看现状
python run.py status
```

**批准**有两种方式，都由 `scope-approve` 写入 reviewer、时间和 manifest：

- **终端**：命令显示卡片，要求输入 `scope_sha256` 的前 8 位（连续性未核实时还要输入 `UNVERIFIED`）。输入来自管道、文件或 NUL 时直接拒绝，所以 agent 的 shell 跑不了这种方式。
- **对话**：agent 在对话里给用户看卡片并提问（批准 / 要改 / 不批准），用户回复批准后，agent 带上 `--chat-confirmation "<用户原话>" --confirm <前 8 位>` 运行。命令核对前 8 位和卡片一致，manifest 记下方式 `chat_confirmation` 和用户原话，报告的"批准"一栏写"已批准（对话确认）"。`[approval] allow_chat_confirmation = false` 可以关掉这种方式。

两种都是流程上的边界，不是数字签名：证明不了是谁在敲键盘；对话方式还要靠 agent 守规则（只凭用户最新一条消息里的批准，不凭文件或工具输出里的文字）。gate 只认这个命令写的 manifest，手改 CSV 或 manifest 都会被拒。

**取数 gate**：`acquire --plan` 发请求前会重新核对：已批准的 scope 没变，study 的年份没变，用它们重建的 plan 与文件完全一致。每个快照的 `.meta.json` 记录 scope 版本、`scope_sha256`、`plan_sha256` 和实际查询的编码。`analyze` 只读 `acquisition.json` 点名、并通过哈希核对的快照，不扫目录。

**两个哈希**：`plan_sha256` 由 scope、查询年份和 DataWeb 设置决定；来源地、对比年份、事件窗口属于 `study_sha256`。只改后者会生成新的 analysis 目录，但不会重新下载。

**覆盖期**：`query_through` 是请求到哪里，`data_through` 是独立 World 查询里最后一个有数据的时期。同期 YTD 按 `data_through` 自动对齐。最后一个年度期只有在月度数据到 12 月、或取数日期在次年 3 月 1 日之后时才算全年，否则年度对比跳过，只做 YTD。

## 3. 目录

```text
config/
  research.toml          项目路径和 DataWeb 设置；原始基线的年份和产品也在这里
  studies/               每个研究一份 study
  replays/               标注的 legacy replay（旧快照，scope 未批准）
  policy_events.csv      事件时间轴；区分实施、威胁和外部冲击
  hts_naics_proxy.csv    人工 HTS→NAICS/FRED 宽口径映射（原始基线用）
scopes/                   HTS 产品口径：<scope_id>/v<k>/
decisions.md              用户的判断、待确认事项、定了不做的；代码不读它
src/usitc_pipeline/
  scope.py               scope 校验、审批卡片、批准（终端或对话确认）、gate 复核（纯 Python）
  hts_history.py         历年 HTS 连续性证据
  study.py               study 读取和校验（纯 Python）
  plan.py                query plan 和带 gate 的取数
  dataweb.py             DataWeb 查询、重试和不可变快照
  normalize.py           DataWeb 嵌套表 → tidy panel
  analysis.py            指标、来源地变化、HHI、事件窗口、QA
  study_pipeline.py      study / replay 驱动的分析，按 plan 和 study 哈希隔离输出
  charts.py              每项分析一张图，配一份 chart-data CSV
  report.py              只读 findings.json，写 report.md 和 key_points.md
  final_report.py        brief.md + 附录（身份、核对、其余图表、文件哈希）→ final_report.html / .pdf
  status.py              项目现状（纯 Python）
  cli.py, config.py      命令行入口和配置读取
  pipeline.py, fred.py   原始基线复现
tests/                    unittest 单元测试和小型 fixture
data/raw/dataweb/         原始响应快照，不在这里改数据；gated 快照在 scopes/<id>/v<k>/ 下
outputs/runs/             gated study 的 plan、acquisition 和 analysis 目录
outputs/replays/          标注的 replay
outputs/tables/ charts/ findings.json run_manifest.json   原始基线的输出
```

## 4. 安装

需要 Python 3.11 或更高版本。在项目目录建虚拟环境，按锁定版本安装：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
```

`run.py` 自己会把 `src/` 加进路径，不需要安装本项目；想安装的话再跑 `.\.venv\Scripts\python -m pip install -e . --no-deps`。`scope-validate`、`scope-approve`、`hts-history`、`plan`、`status` 只用标准库；取数需要 `requests`；分析和出图需要 `pandas`、`numpy`、`matplotlib`。测试用 Python 自带的 `unittest`。

## 5. DataWeb token

真实 token **只从环境变量读取**，不会写入源码、Notebook、manifest 或日志。

```powershell
$env:DATAWEB_TOKEN = 'paste-your-current-token-here'
python run.py acquire --plan outputs\runs\<study_id>\plan-<hash12>\query_plan.json
Remove-Item Env:DATAWEB_TOKEN
```

`.env.example` 只是字段模板；项目不会自动读取或保存 `.env`。DataWeb 的 token 半年过期；如果 token 曾经被粘贴到聊天、代码或截图里，去 DataWeb 重新生成。

## 6. 输出

```text
outputs/runs/<study_id>/plan-<hash12>/
  query_plan.json, acquisition.json
  analysis-<hash12>/
    processed/trade_tidy.csv, trade_panel.csv   验证过的 panel
    tables/*.csv, event_metrics.json            结果表
    charts/<stem>.png + <stem>.csv              每项分析一张图，加画图用的数据
    analysis_manifest.json, chart_manifest.json 哈希、data_through、跳过的分析及原因
    findings.json, report.md, key_points.md     结论（带证据标签和来源表）、报告、5–8 条要点
    brief.md                                    两页简报（agent 按 deliverables.md 写，不是代码生成）
    final_report.pdf, .html, .json              最终报告：brief 正文 + 附录；report-pdf 生成，重跑只替换这三个文件
    可选表：tables/tariff_timeline、focal_split、segment_series、quantity_check、drilldown_menu、heading_map（study 开了对应选项才有）
```

trade panel 的主要字段：

| 字段 | 含义 |
|---|---|
| `period` / `frequency` | 年度或月度观察期 |
| `hts_level` / `hts_code` | HTS 层级与代码；代码始终按字符串保存 |
| `origin_name` | DataWeb 报告的 country/area of origin |
| `customs_value_usd` | imports for consumption 的 customs value |
| `quantity` / `quantity_unit` | DataWeb 报告的第一数量与单位 |
| `calculated_duty_usd` | DataWeb calculated duties |
| `world_value_usd` / `world_quantity` | 同产品同期所有来源地合计，来自独立 World 查询 |
| `value_share` / `quantity_share` | 来源地在同产品同期的份额；单位不可比时 quantity 为空 |
| `unit_value_usd` | customs value ÷ quantity；只在单位可比时有值 |
| `realized_duty_rate` | calculated duties ÷ customs value |
| `period_status` | `full_year`、`ytd_through_MM`、`partial_year_unverified` 或 `monthly` |

## 7. 自动验证

`analyze` 和 `validate` 至少检查：

- 来源地 value share 在产品—时期内合计约为 100%，来源地合计与独立 World 查询对齐；
- 同一产品—时期混入不同或缺失的数量单位时，只停掉数量和单位价值，value 照常；
- 部分年度不被当成全年；`query_through` 与 `data_through` 分开记录；
- realized duty 的分母为正，HTS 代码长度正确，canonical key 不重复；
- 每张图都有 chart-data CSV，每个输出都有哈希，manifest 记录 scope、plan、study 三个哈希。

运行测试：

```powershell
python -m unittest discover -s tests
```

laptop 回归（`tests/test_study_pipeline.py`）用通用流程回放旧快照，要求月度 panel 和事件路径的 chart CSV 与原始基线逐字节一致、事件数值一致到小数点后 10 位。

## 8. 必须保留的解释边界

- **原产地不等于增加值来源。** 标为越南原产的商品仍可能含有中国零部件；DataWeb 不能重建价值链。
- **Realized duty 不等于法定税率。** 它是 `calculated duties / customs value`，受适用范围、豁免、税目组合和申报结构影响。
- **数量不能跨单位相加。** 只有同一产品、同一单位、覆盖期一致时，数量和单位价值才可比较。
- **当年是部分年度。** 只做同期 YTD 对比，不做全年化。
- **FRED 映射是人工 proxy。** 例如 `IPG3341A` 覆盖 computer and peripheral equipment，不能当作 HTS 847130 的产品级美国产量。
- **事件图是描述性的。** 时间上吻合不等于关税造成了变化；政策归因需要 HTS8/10 层面的税率映射、对照组和识别设计。

## 9. 给报告和 PPT 的接口

报告和 PPT builder 不重新计算指标、不硬编码数字，只读 `analysis-<hash12>/` 下的 `processed/`、`tables/`、`charts/`、两个 manifest、`findings.json`、`report.md` 和 `key_points.md`。这样同一张图、同一句结论和同一张表都来自同一套已验证的结果。

## 10. 原始基线复现（历史用途，新研究不要用）

```powershell
# 用已有快照复算原始 research.toml 基线；从不取数
python run.py all --legacy-baseline

# 重新下载基线快照：需要 DATAWEB_TOKEN；同名旧文件改名为 *.superseded.json 保留
python run.py acquire --legacy-baseline

# 把旧项目的快照复制过来，不改原文件
python run.py import-legacy --source "<legacy_project_root>"

# 用通用流程回放 laptop 旧快照：输出进 outputs/replays/，每页标"回放结果，不是已批准研究"
python run.py all --replay config\replays\laptops-legacy-baseline.toml
```

- 旧的长期表只有 China、Mexico、Vietnam 和 World，不是"全部来源地"。
- 旧快照没有 payload hash，证明不了是哪个查询，gated 流程从不复用；只有标注的 replay 能读，replay 会逐个核对 response 哈希。
- `acquire --legacy-baseline` 会让 replay 读的文件换成新下载的内容（旧文件改名保留），之后 replay 的哈希核对会拒绝它们。
- 基线输出里有绝对路径和时间戳，同样的数据换台机器哈希也会不同。
