# USITC DataWeb 关税与供应链研究 Skill：任务需求说明

> 2026-11-06 更新：批准已改为用户本人在交互终端运行 `scope-approve`，不再有 external-approval 工件。现行流程以 README 第 2 节和 skill 的 references 为准；本文里关于外部审批的描述已过时，保留作设计记录。

> 版本：v2.0（实现后基线）  
> 日期：2026-11-05  
> 定位：把目前的一次性研究，封装成一套可复用、可审计、可扩展的产品级贸易研究能力。

## 0. 30 秒摘要

| 问题 | 结论 |
|---|---|
| 要封装什么？ | 一个负责识别、scope review 和研究编排的 Skill，加一条负责取数、计算、QA 和制图的确定性 pipeline。 |
| 最先做什么？ | 先把产品语言解析成可审阅的 HTS6/8/10 scope，再取 DataWeb 数据；不把第一个搜索结果当答案。 |
| API 如何处理？ | 代码和 Skill 共享；DataWeb 账号与 token 由每个运行者自行注册、在 acquisition 时通过环境变量注入。 |
| 已经有什么？ | 已有长期 HTS2、多产品 HTS6、laptop 月度事件路径、可审计 pipeline、Skill 骨架、golden regression 和离线新品类 E2E。 |
| 还缺什么？ | 真实历史 HTS catalog、带一手来源的 statutory tariff-treatment map，以及需要做因果判断时的识别设计。 |

## 1. 一句话任务

建立一个以 **HTS 产品范围为入口**、以 **USITC DataWeb 为核心数据源** 的研究 Skill：用户说明产品、时间、国家/地区和研究问题后，系统完成候选识别与 review card，并在外部审批边界停下；可信工件就绪后，通用 workflow 再完成取数、质量检查、分析、制图和结果交付。

## 2. 为什么要做

目前的研究已经能回答具体问题，但仍偏“一问一答”：换一个产品时，往往需要重新确认代码、修改配置、运行脚本并解释口径。要解决的不是再做一份 notebook，而是把已经验证过的方法变成可重复执行的研究流程。

核心问题有四个：

1. 产品名称不能直接、稳定地等同于某个 HTS code。
2. DataWeb API token 属于每位使用者，不能跟项目代码或 Skill 一起共享。
3. 数值变化、来源地重配和关税因果不能混为一谈。
4. 换产品、换时期、换国家后，结果仍需保持相同的数据口径和验证标准。

## 3. 希望封装成什么样

不是做一个很长的 `SKILL.md`，也不是再建一个与现有能力重叠的 `hts-lookup` Skill，而是采用三层结构：

```text
用户研究请求
    ↓
tariff-sourcing-research Skill
  - 判断是否触发
  - 识别或验证 HTS scope
  - 在歧义处收集一次必要澄清，并停在外部审批边界
  - 编排 pipeline 并约束研究口径
    ↓
可复用 pipeline
  - DataWeb / FRED 数据获取
  - 原始快照、标准化、计算、校验、制图
  - 输出表、图、chart data、findings 和 run manifest
    ↓
研究交付
  - 图表与关键发现
  - 证据边界、限制和可复现记录
```

职责边界：

| 层级 | 负责什么 |
|---|---|
| Skill | 路由、提问、scope review、审批边界、流程顺序、证据边界、交付格式；不得自批 |
| Pipeline / scripts | API 请求、缓存、清洗、确定性计算、单位检查、验证、文件输出 |
| 人工 | 产品定义有实质歧义时做选择；审核高影响结论 |

## 4. 用户最少需要提供的输入

必需输入：

- 产品名称或已知 HTS code；
- 研究时间段；
- 关注的进口来源国/地区；
- 想回答的问题，例如份额转移、数量变化、关税暴露或事件前后变化。

可选输入：

- finished good、component 或 part；
- 材料、化学体系、用途、功率/尺寸等会影响归类的属性；
- 指定 HTS6、HTS8 或 HTS10 粒度；
- 希望输出报告、PPT 图、CSV，或仅做快速诊断。

如果产品描述足以唯一定位，系统不应重复提问；如果会改变统计范围，则一次性列出最少的澄清问题。

## 5. 标准 Workflow

1. **Resolve or validate scope**：产品描述映射到可审阅的 HTS6/8/10 候选；若用户直接给 code，则先验证有效期和描述。
2. **Review**：列出 include / exclude rationale、finished good / parts 边界、单位和历史连续性风险。
3. **External approval and freeze**：Agent 只生成绑定 reviewed scope 的 approval card；`scope-approve` 只消费同时绑定 `scope_sha256` 与 card SHA-256 的外部工件，并明确这是流程约束而非密码学身份认证。
4. **Plan acquisition**：根据 scope、时期、国家/地区和频率生成 DataWeb query plan。
5. **Acquire and cache**：每位使用者通过环境变量提供自己的 token；保存不可变原始快照和请求元数据。
6. **Normalize and analyze**：统一字段、检查单位，计算进口额、份额、数量、来源集中度、事件窗口和关税暴露。
7. **Validate**：独立 World 总额核对、YTD 同月比较、数量单位检查、图表与 chart data 一致性检查。
8. **Deliver**：输出表、图、关键发现、限制、数据截至时间和完整 run manifest。

## 6. 功能需求

### A. HTS scope 入口

- 支持“产品名称 → HTS 候选”和“已知 code → 有效性验证”两条路径。
- 默认以 HTS6 作为跨国、跨期产品分析主干；HTS8/10 用于美国细分税则和政策匹配。
- 不允许把关键词搜索第一条直接当成最终代码。
- 对电池、零部件、材料等高歧义品类，必须区分用途、形态、化学体系和整件/零件。
- 对历史研究检查 code 的生效日期、失效日期和描述变更；不能默认 2026 code 可直接回填到 1989。
- 经批准的 scope 必须可版本化、可哈希、可复核。

### B. API 与凭证

- 每位使用者自行注册 DataWeb 账号并生成 token。
- token 只从环境变量读取，不写入代码、配置、notebook、manifest、日志或 Skill。
- public HTS lookup 与 DataWeb credential 分开；scope 识别阶段原则上不依赖 DataWeb token。
- 无 token 时仍可检查已有快照、既有结果和 scope 文件，但不得伪装成已刷新数据。

### C. 数据获取与缓存

- 支持年度和月度查询，以及 HTS2 → HTS6 → HTS8/10 的由浅到深分析。
- 查询计划需记录 reporter、trade flow、imports for consumption、period、HTS level、origins、value/quantity 字段。
- 原始 API 响应只新增、不静默覆盖；相同 query 可复用缓存。
- 每个快照保留请求参数、抓取时间、响应哈希、scope/plan hash；analysis manifest 分开记录 `query_through` 与独立 World 推导的 `data_through`。

### D. 分析模块

- 长期总体结构：HTS2 进口结构和来源地变化。
- 产品级重配：HTS6 的中国份额变化、承接来源地、HHI 和总需求变化。
- 数量分析：仅在单位一致且语义可比时计算 physical quantity。
- 事件研究：围绕关税生效、豁免/暂停、疫情等事件构建连续月度窗口。
- 政策匹配：在 HTS8/10 层面连接 statutory tariff treatment，并与 realized duty 分开呈现。
- 需求与国内替代检查：区分来源地重配、美国总需求收缩和可能的本土生产替代。
- 可选外部 proxy：FRED 仅作为行业级背景指标，不能冒充产品级国内产量。

### E. 输出

每次正式运行至少输出：

- scope manifest；
- query plan 与原始快照清单；
- 标准化数据表；
- 主要结果表；
- 图及对应 chart data；
- findings / limitations；
- run manifest 与 validation status。

## 7. 必须守住的研究口径

- 默认使用 **imports for consumption**，除非研究明确需要 general imports。
- DataWeb 的 **country of origin 不等于全部增加值来源国**，不能据此直接宣称供应链完全离开某国。
- 进口额变化不等于数量变化；数量单位不同的 HTS 行不得直接汇总。
- 中国份额下降不自动等于越南或墨西哥承接，必须检查其他来源地、World 总额和集中度。
- 单年 YTD 只能与相同月份范围比较。
- World 总额应作为独立 API 汇总行核对，不能只把选定国家相加后称为 World。
- `calculated duties / customs value` 是 realized duty proxy，不等于法定税率或某一轮政策税率。
- 描述性时间序列和事件前后变化可以展示关联，但没有识别设计时不能写成关税导致的因果结论。
- 报告、PPT 和文档只消费已经验证的结果表和 chart data，不在展示层临时重算指标。

## 8. 我们之前已经完成的工作（简版）

### 研究与数据

- 已建立 `Config → raw snapshots → normalization → analysis → validation → charts` 的干净 pipeline。
- 原始基线已验证缓存包含 **24 份 DataWeb 原始快照**；旧 laptop 的四个稳定 CSV/JSON 产物由 golden regression 逐字节锁定。
- 当前 run manifest 为 `validate` 阶段，`validation.ok=true`；覆盖 **13,627 行、136 个 product-period**。
- 已完成 HTS2 长期结构（1989–2025）、8 个 HTS6 产品的 2024–2025 重配比较，以及 portable computers（HTS 847130）的 2017–2026 月度事件路径。
- 已将 FRED 行业 proxy 接入背景检查，但保留其不是产品级国内产量的限制。
- 当前 8 个产品包括 refrigeration equipment、portable computers、smartphones、headphones and earphones、computer monitors、color televisions、upholstered wooden seats 和 video game consoles。

### Skill v2

- 已创建 `tariff-sourcing-research` Skill，而不是另建一个重叠的 HTS Skill。
- 已有 `SKILL.md`、4 份 domain references、scope template、HTS resolver、scope validator、pipeline wrapper、behavior evals 和单元测试。
- 已验证 exact-first 的官方 HTS 查询路径；例如 portable automatic data processing machines 可定位到当前 HTS10 `8471.30.01.00`，并向上保留 HTS6 / HTS8。
- scope validator 负责 candidate/reviewed 校验与确定性 `scope_sha256`；外部审批消费、历史连续性与 manifest 复验由 pipeline 硬门槛负责。
- 已覆盖 clear product、ambiguous battery、known code/history、read-only existing result、generic macro non-trigger 和 legal-classification non-trigger 等行为场景。
- Skill 入口已压缩为约 60 行，并配有 8 条 Gotchas、16 个 behavior contracts 和可执行评分 runner。

### 已有文档

- 已有详细的 `HTS_DataWeb_Skill_Workflow_Design.md`，负责解释触发规则、scope 流程、历史连续性和工程设计。
- 本文档负责记录“最终要做成什么、必须具备什么、如何验收”，两者用途不同。

## 9. 当前已完成与仍需人工投入

已完成：scope/approval/history gate 已硬接入 plan 与 acquisition；`scope_sha256`、`plan_sha256`、`study_sha256` 与精确 snapshot provenance 已贯穿；study-aware analyze/charts/validate 已按两个 hash 隔离；独立 World、YTD、top gainer、quantity unit 与 `data_through` 回归已覆盖；laptop golden 与离线新品类 E2E 已纳入测试。

仍需投入：

1. **真实历史来源目录**：代码可验证 edition、有效期、描述、单位和源文件哈希，但具体产品仍需下载官方 USITC 历年文件并建立 catalog；测试只使用合成 fixture。
2. **法定 tariff treatment mapping**：HTS8/10 的政策适用、豁免、暂停和到期仍需独立、带来源的映射；realized duty 不能替代它。
3. **识别设计**：当前事件时间轴与来源地变化是描述性证据；因果结论仍需对照组、暴露差异和稳健性检验。
4. **外部信任边界**：本地 JSON 工件可证明哈希绑定，但没有数字签名时不能证明 reviewer 身份、权限或意图。

## 10. 目标目录结构

```text
<skill_root>\
├── SKILL.md
├── agents\openai.yaml
├── references\
├── scripts\
├── assets\
├── evals\
└── tests\

<project_root>\
├── config\
├── data\raw\
├── data\processed\
├── src\
├── notebooks\
├── outputs\
├── tests\
├── README.md
├── HTS_DataWeb_Skill_Workflow_Design.md
└── USITC_Tariff_Research_Skill_Task_Requirements.md
```

Skill 只保存通用规则与小型确定性工具；大型数据、原始快照和具体研究输出留在项目目录中。

## 11. 验收标准

满足以下条件才算完成：

- 用户用自然语言描述一个清晰产品时，系统能生成小规模、可解释的 HTS 候选，而不是泛搜索结果列表。
- 产品存在实质歧义时，系统会问一次紧凑的澄清问题；未批准前不启动新 DataWeb acquisition。
- 用户直接提供 code 时，系统会验证描述、层级、有效期和历史适用性。
- approved scope manifest 是 acquisition 的硬门槛，且 scope hash 贯穿所有后续产物。
- 不同使用者可以用自己的 token 运行同一套代码，任何输出中均不泄露 token。
- 换产品后仍能复用同一套取数、缓存、分析、验证和制图模块。
- 结果同时展示 value、share、quantity（可比时）、World demand 与来源地重配，避免单一指标叙事。
- 每张正式图都有可追溯 chart data；所有关键数字能回到结果表和原始快照。
- deterministic tests、behavior evals、pipeline validation 和一个新品类端到端示例全部通过。
- 输出明确区分 Fact、Inference、Assumption，并把 measurement、attribution 和 causality 分开。

## 12. 明确不做什么

- 不把 Skill 当成海关法律裁定工具；高风险归类仍需专业人员确认。
- 不做泛宏观新闻评论或与产品级 DataWeb 研究无关的任务。
- 不共享或保存用户的 DataWeb token。
- 不根据 country of origin 自动推断全部增加值来源。
- 不在单位不可比时拼接 quantity。
- 不因为时间点重合就宣称关税造成了观察到的变化。
- 不把 PPT 或报告当成新的计算引擎。

## 13. 实施优先级

### P0：投入真实研究使用

- 为第一个正式新品类建立官方历史 HTS catalog，并由外部 reviewer 产生审批工件；
- 用个人 DataWeb token 执行 plan acquisition，再跑 study-aware analyze/charts/validate。

### P1：增强政策解释

- 增加带来源的 HTS8/10 tariff-treatment mapping；
- 将 implemented、threatened、suspended、excluded、expired 与 unknown 分开编码。

### P2：加快交付

- 在已验证的 analysis run 上标准化 short report / slide 模板；
- 交付层只读取 hash-backed tables 与 chart-data，不重算指标。

## 14. 示例调用

```text
研究 portable computers 在 2017–2026 年间的美国进口来源变化。
先验证产品对应的 HTS scope；比较中国、越南、墨西哥和 World；
同时看进口额、份额和可比数量，并围绕 2018 tariff proposal、疫情和后续政策节点做月度事件图。
所有新增取数必须在我批准 scope 后进行，使用我本机的 DATAWEB_TOKEN，最后输出表、图和一页 quick report。
```

## 15. Definition of Done

最终不是“又完成了一次 laptop 分析”，而是：任何一个边界清楚的新产品，都可以沿着同一条可审计链路完成 **scope → approval → data → QA → insight → deliverable**，且另一位组员只需使用自己的 DataWeb token 就能复现。
