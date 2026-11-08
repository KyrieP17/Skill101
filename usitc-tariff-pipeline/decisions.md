# Research Decisions

This is an append-only human-readable log of prior research boundaries and interpretation decisions. Code does not parse it. It is not configuration, a scope manifest, an approval artifact, or evidence that a scope was approved. Executable product coverage lives in versioned scope rows and their validated manifests.

Do not rewrite earlier entries. If a decision changes, append a dated entry that identifies the decision it supersedes.

## 2026-11-05

### D-001 — Laptop scope intends to exclude tablets, but does not yet execute that boundary

- **Decision:** The intended laptop concept excludes tablets.
- **Current implementation:** The existing HTS6 `847130` baseline cannot by itself demonstrate that tablets are excluded.
- **Required follow-up:** Review finer HTS8/HTS10 lines and the applicable historical HTS editions before encoding this exclusion in a versioned scope.
- **Current reporting rule:** Treat tablet exclusion as an unresolved coverage limitation. Do not state that the current baseline already excludes tablets, and do not treat this note as scope approval.

### D-002 — Country of origin is not value-added origin

- DataWeb country/area of origin does not identify where all components or value added came from.
- A gaining origin may be consistent with sourcing reallocation, but it does not prove a direct factory move or the removal of Chinese intermediate inputs.

### D-003 — Realized duty is not the statutory tariff rate

- `calculated duties / customs value` is a realized-duty measure.
- Statutory treatment requires verified HTS8/HTS10, origin, effective date, legal status, and exclusion/suspension mapping. Missing mapping remains unknown, not zero.

### D-004 — Mixed quantity units permit value analysis only

- Do not aggregate or compare incompatible physical quantity units.
- When included lines or product-periods mix units, continue customs-value and share analysis if otherwise valid, but disable combined quantity and unit-value claims.

## 2026-11-06

用户的判断，引号里是原话；来源是 2026-11-06 的过夜 prompt、2026-11-05 的《tariff-skill-修改清单》，以及更早一次会话里的口头判断。下次做相近产品先查这里，不再重复问。

### D-005 — 换产品只写 scope 和 study

- "换产品：只新建 scope 和 study，SKILL.md 和 Python 都不用改。"（修改清单，2026-11-05）
- "新研究 = 新 scope + 新 study；laptop 是示例。"（修改清单第 11 条）

### D-006 — 批准只由用户做，连续性没核实就标出来

- "批准由人做，连续性没核实就标出来"；agent "不可以：批准任何 scope"。（过夜 prompt）
- 落实：`scope-approve` 只在交互终端运行，由用户输入 `scope_sha256` 前 8 位；连续性未核实时还要输入 `UNVERIFIED`，报告里一直显示"连续性未核实"。（2026-11-07 起也可以在对话里确认，见 D-021；"只有用户批准"不变。）

### D-007 — 新产品先做 HTS6 粗口径

- 用户说具体编码"我很难知道具体的"。先给 HTS6 粗口径看个大概，再问要不要细到 HTS8/10；细化是新的 scope 版本。（更早一次会话，记于 2026-11-06）

### D-008 — 笔记本研究要问平板

- "8471.30 也包含平板电脑，HTS6 层面分不开。研究笔记本时要在 intake 里问清楚，需要的话到 HTS10 层面处理。"（修改清单第 8 条；另见 D-001）

### D-009 — 分析范围和交付

- 分析要覆盖"value、quantity、unit value、全部来源地的份额和流入流出、World 核对、HHI、同期 YTD、事件窗口、realized duty"；"单位不可比时只停掉 quantity 相关结论"。（过夜 prompt；同 D-004）
- "每项分析一张图，每张图配一份 chart-data CSV；标题、单位、产品名都从 scope/study 读取"。（过夜 prompt）
- 每次研究交付三样："数据集"、"报告（Markdown 或 HTML）：图加文字，每个结论标证据标签，并注明来源表"、"5–8 条要点"。（过夜 prompt）

### D-010 — 数据和凭证

- token "不打印、不落盘"，只给已批准的 scope 取数。（过夜 prompt）
- 不"删除或覆盖已有快照和输出（新结果写进新目录）"。（过夜 prompt）
- "不能用观察到的进口额来决定某个代码要不要放进 scope，否则样本会依赖于结果。"（修改清单第 8 条）

### 定了不做的

- 不做约束性的海关归类、税额计算或裁定：scope 是研究口径。（修改清单第 9 条；skill description）
- 不做宏观关税评论、关税新闻、运费物流：skill 只管"产品级别的美国进口来源与关税研究"。（修改清单第 9、12 条）
- gated 研究不复用没有 payload hash 的旧快照；它们只能进标注的 replay。（修改清单第 8 条）
- 不用观察到的进口额挑编码（D-010）。
- 不在 Word、PPT 或 JS builder 里重算指标；报告只读验证过的输出。（skill 职责分工）
- 没有识别设计不下因果结论；时间上吻合只是描述。
- 不加用不到的抽象："在保证完整的前提下尽量简洁：删掉重复规则和死代码，不加用不到的抽象"。（过夜 prompt）

### 待确认（2026-11-06 夜里 agent 按保守做法先定的）

没人能问时按保守做法继续，你确认或改掉即可。每条是"现在怎么做 → 可以改成什么"。

1. **laptop 验收**：用标注的 legacy replay 跑（旧快照有 response 哈希，但没有 payload hash；scope 未批准）→ 你批准 `laptops/v2` 并给 token 后，跑一次正式 gated run 再对比。
2. **两个 scope 的审核状态**：`laptops/v2` 和 `lithium-ion/v1` 的行由 agent 标成 `reviewed`，审批卡片都写"连续性未核实"→ 你看卡片后批准、退回，或先补历年 HTS 证据。
3. **laptop study 指向 v2**：`config/studies/laptops-2017-2026.toml` 改指 `scopes/laptops/v2/manifest.json`（v1 的 `scope.csv` 是候选稿，占着批准输出的文件名）。
4. **legacy baseline 保留**：`research.toml` 基线、`--legacy-baseline`、`import-legacy` 只作历史复现保留（清单允许"保留或者干脆删掉"）；`all --legacy-baseline` 改成从不取数 → 可以整个删掉。
5. **批准方式**：只能在 PowerShell 或 cmd 里批准（Git Bash 加 `winpty`）。这是流程边界，不是签名，证明不了是谁敲的键盘 → 要更强的话需要签名或外部审批系统。
6. **部分年度**：最后一个年度期只有月度数据到 12 月、或取数日期 ≥ 次年 3 月 1 日才算全年；否则年度对比跳过，只做 YTD。
7. **highlight 上限 6 个**，其余并入 "Other reported origins"，每个来源地固定配色。
8. **单位价值图**隐藏价值占比 < 1% 的来源地-月份，避免小样本噪声。
9. **语言**：报告正文中文，图里的文字英文。
10. **单编码 scope 不画 02**（scope 内各编码的重分配）。
11. **要点排序**：realized duty 排在 unit value 前面。
12. **编码的名称**：`product_reallocation.csv` 里每个编码的 `product` 列用 DataWeb 的原描述；图和报告的产品名用 scope 的 `product_name`。
13. **data_through 推不出来**时记 null 并报错，不猜。
14. **schema 升级**：scope 4、审批卡片 2、plan 2；旧格式不再兼容（现在也没有已批准的旧 manifest）。
15. **回归范围**：清单写"11 张表和 chart CSV 要逐字节一致"，新流程没有一一对应的 11 个文件。现在逐字节比 `monthly_panel.csv`、`03_event_path.csv`、`03_event_path.events.csv`，事件数值比到小数点后 10 位；其余表不属于 laptop scope 或结构变了（见 NIGHT_LOG）。
16. **审批卡片不可变**：证据后补时另写新卡片（如 `approval-card-2.json`）。
17. **删了两个过时的测试夹具**（`tests/fixtures/lithium/approval-card.json`、`external-approval.json`，备份在 scratchpad）。
18. **单位标签来源**：合并 measure 表时，数量单位取数量表的标签（今晚测试发现的 bug，已修）。
19. **锂电池的数量单位**：HTS6 850760 的 `No.` 把手机电芯和车用电池包算成一样的"一个"，pipeline 会照常算数量份额和单位价值（单位一致）。study 说明里写了"只用 value 和份额" → 可以把 scope 的 `quantity_unit` 留空、换成 HTS10，或另做 kg 口径，让数量结论停掉。改 scope 会换掉 `scope_sha256`，所以今晚没动。
20. **eval 触发率**要真的调用模型（属于对外发送），今晚没跑；runner 能接一个执行命令，你同意后再跑。
21. **wrapper 暴露 scope-approve**（按清单第 6 条）；agent 的 shell 跑它会被拒，测试覆盖。
22. **删了 skill 的 `scripts/validate_scope.py`**（和 wrapper 重复）；wrapper 不再透传 `import-legacy`，要用就在项目里直接 `python run.py import-legacy`。
23. **记忆过时**：Claude 记忆里的 `usitc-pipeline-python-env.md` 还写着"项目没有 .venv"，现在已经有了；今晚不能改 Codex 以外的东西 → 你同意后更新。
24. **两份锂电池历史 HTS PDF** 下不下载（交接时留下的问题）：不下载就保持"连续性未核实"。
25. **D 盘旧 outputs** 是"全部补上"还是"只重跑"（交接时留下的问题）：今晚两样都没做，旧 outputs 没动。

## 2026-11-06（看过夜结果之后）

### D-011 — skill 出宏观 report，数量先只用第一单位

- "先按No来呗，如果后面要下钻到具体分析再说，我觉得一个skill主要是能输出一个宏观的report把，具体的问题还是要具体研究去分析的"；"我们目前取到第一个就够了我觉得"。
- 落实：report 以进口额（customs value，报关进口额，不是销售额）和份额为主；数量只取第一单位（`CONS_FIR_UNIT_QUANT`），不加第二单位。

### D-012 — 锂电池的数量保留，加说明

- "这个锂电池怎么处理就保留加说明，后续我们能做到了再用第二维度做优化"。
- 落实：study 新增可选字段 `quantity_note`，report 把它印在表头，并跟在每条数量和单位价值结论后面；`config/studies/lithium-ion-2017-2026.toml` 已填。
- 以后的优化：8507.60 的三个 HTS10 行（.0010 车用、.0030 储能、.0090 其他）第一单位都是 `No.`，第二单位都是 kg（官方 HTS，2026-11-06 查）。要用 kg，先确认 DataWeb 第二数量的 measure，再改代码。

### 待确认的更新

- 第 6 条（部分年度）：确认，"第六条这个没办法，数据客观更新的我们没办法改变"。3 月 1 日等的是 12 月数据发布；每年 6 月的修订靠重新取数，旧快照留档。
- 第 19 条：按 D-012 处理，scope 不改。
- D-001 的后续"到 HTS8/10 找能排除平板的行"做不到：官方 HTS（2026-11-06 查）8471.30 下只有 8471.30.01.00 一行，平板和笔记本在任何层级都分不开。笔记本口径要不要改名为"笔记本 + 平板"（开 `laptops/v3`）还没定。

### D-013 — 第二计量单位：所有产品都记录，先不分析（取代 D-012）

- "我是觉得就是你这个不需要给锂电池留吧，你应该给所有的都留个后门，就是你assess看有没有第二计量单位，有的话就晚点再分析"。
- 落实：
  - scope 新增可选列 `second_unit`：解析 scope 时看每个纳入编码下的 HTS10 行，全都列了同一个第二单位就填上，否则留空。没填的 scope 哈希不变。
  - 审批卡片和报告都写"第二计量单位 kg，未取，留待后续分析"；数量分析仍只用第一单位（D-011）。
  - 取数前跑的 `hts_current_check.py` 也比对第二单位：scope 没记或记得不对，报 `second_unit_changed`。
  - D-012 的 study 字段 `quantity_note` 撤掉了，锂电池 study 恢复原样。
  - 现有两个 scope：笔记本 8471.30.01.00 只有 `No.`，`laptops/v2` 不变；锂电池 850760 下三个 HTS10 行都是 `No.` 加 kg，`lithium-ion/v1/reviewed.csv` 补了 kg，scope_sha256 变为 `f15bccfc…`。旧卡片 `approval-card.json` 不再能用（保留），新卡片是 `approval-card-2.json`。

### D-014 — 笔记本口径就是"笔记本 + 平板"

- "对，那就没招啊，就是要这么写，没办法具体的去说其他的"。
- 依据：官方 HTS（2026-11-06 查）8471.30 下只有一个 HTS8 行（8471.30.01）和一个 HTS10 行（8471.30.01.00），平板在任何层级都和笔记本在一起。D-001 想在 HTS10 排除平板，做不到，到此结案。
- 落实：`laptops/v2` 的产品名改为 "Portable computers (laptops and tablets)"，纳入理由同步改写。产品名不进 `scope_sha256`，哈希不变（批准时仍输入 `189d3445`），但旧卡片 `approval-card.json` 不再能用（保留），新卡片是 `approval-card-2.json`。study 标题改为 "Where did China's U.S. laptop and tablet import share go?"。回放重跑到 `analysis-92d9a66ca282`，数据表和图表数据与夜里那份逐字节相同，只有名称变了。

### D-015 — 连续性先不核实

- "我们先不验证"（指下载历年 HTS PDF 核实编码连续性）。
- 落实：两个 scope 都按"连续性未核实"批准（`--continuity-unverified`，批准时输入 `UNVERIFIED`），报告里一直标出来。以后要核实，再下载历年 HTS 版本，写新卡片。

### D-016 — 锂电池第一份宏观简报；DataWeb 单次上限改为 20,000 行

- "我们先根据锂电池这个概念你跑个宏观的，我的expectation是能大概输出一个2页的report……可以供具体下钻的选项或者要研究的时段"。用户 2026-11-06 批准 `lithium-ion/v1`（连续性未核实）。
- 第一次 acquire 被 DataWeb 拒绝：现在单次查询最多 20,000 行。`config/research.toml` 的 `[dataweb] max_records` 从 100000 改为 20000，plan 哈希随之变为 `1b92ca3d8488`（22 个查询全部下载成功）。改之前生成的 `plan-e84165cb042e` 没有数据，保留未删。
- 交付：`outputs/runs/lithium-ion-2017-2026/plan-1b92ca3d8488/analysis-4286be55233b/`，`report.md` 之外新增两页简报 `brief.md`（按 deliverables.md 的 "Two-page macro brief"）。
- 已知小问题：study 没有事件窗口时，`03_event_path.png` 的脚注仍写着事件线的说明，但图上没有事件线。

### D-017 — 锂电池 HTS10 下钻（lithium-ion v2）

- 用户选了 "HTS10 split"，同意下载 HTS 存档第 85 章 PDF（2017、2022、2024、2025、2026 Basic，加 2026 Rev5、Rev10、Rev11，共 8 份，哈希见 `scopes/lithium-ion/v2/history/SOURCES.md`）。用户 2026-11-06 批准 v2（连续性未核实）。
- 发现：2017 到 2026 Basic，8507.60 下只有 .10（车用）和 .20（其他）；.20 在 2026 年拆成 .30（带外壳储能系统，统计注释 2）和 .90（其他）。存档只能确定生效日在 2026-01-16 到 2026-04-08 之间，scope 按这个范围写；DataWeb 数据显示 .20 最后出现在 2026-01，.30/.90 从 2026-02 开始。所以储能没有 2026 年以前的单独序列。
- 管线修复（第一次遇到年中拆分暴露的三个问题，109 个测试通过）：
  1. `derive_data_through`：DataWeb 对当月不在用的编码返回空白（未发布月份才是 0）。在完整查询的年份里，新编码开始前、旧编码停用后的空白不再算缺数据，否则 data_through 会停在 2025-12。
  2. 这些不在用的编码月份不进 panel，覆盖审计也只在编码查询且在用的月份里找缺口。
  3. `build_product_reallocation` 和 `quantity_unit_status` 用同一规则：数量为 0 的行（HTS10 里大量没贸易、没单位标签的来源地）不影响单位可比性。
- 交付：`outputs/runs/lithium-ion-hts10-2017-2026/plan-e16c12e360f4/analysis-60a58ae3b517/`（report.md、brief.md）。HTS10 各行加总和 HTS6 一致。

### D-018 — 第二计量单位（kg）改为 study 可选，锂电池 HTS10 按 kg 复核

- 用户说"做 kg"。2026-11-06 用一个编码一年的探针确认 DataWeb 的度量名是 `CONS_SEC_UNIT_QUANT`：返回单独一张 "Second Unit of Quantity" 表，单位 "kilograms"。
- 设计：只有写了 `[quantity] unit = "second"` 的 study 才多要这张表；数量、数量份额、单位价值改用第二单位。scope 没给所有 include 行登记同一个 `second_unit` 时，plan 拒绝。默认（first）不进 study、plan、payload、快照元数据的哈希，所以已有的笔记本、锂电池 v1/v2 的 plan、缓存和哈希都没变。`trade_tidy.csv` 同时保留两种单位（`quantity_2`、`quantity_2_unit`）。报告表头写"数量口径"，第二单位的发现改为 Measured。图表脚注里的 "first units" 改成不指定单位的说法。项目测试 115 个（新增 6 个）、skill 测试 32 个通过；skill 的 study-design、deliverables、scope-resolution 三处说明已更新。
- 运行：study `config/studies/lithium-ion-hts10-kg-2017-2026.toml`，plan `3c68d14c94c9`，22 个查询全部新下载（token 只在命令里内联，没写盘）。验证通过、没有警告，data_through 和按"个"计的版本相同（年度 2025、月度 2026-09）。
- 发现：确认 D-017 简报的洞察 2（非车用"单价上涨"是每个变重：每个 0.60 → 3.51 kg，每公斤 $26.2 → $19.2）和洞察 3（车用每个 1.2–39.7 kg，按 kg 计 2025 年进口 −34.7%，不是 +216.7%）。新增：非车用里 China 重量份额没降（83.1% → 84.3%），每公斤价格降 30.8%；车用里 China 重量 −66.5%，每公斤价格不变。
- 更正 D-017 简报：China 车用份额从 2024-05 就开始下降（金额 86.6% → 71.4%），不是"下半年"。用户同意后改了旧简报的洞察 1 和下钻表里的时段（改前的版本备份在 scratchpad），并在洞察 1 末尾注明了更正。
- 交付：`outputs/runs/lithium-ion-hts10-kg-2017-2026/plan-3c68d14c94c9/analysis-4b54385381c8/`（report.md、brief.md）。

## 2026-11-07

### D-019 — 读者是要 general 底稿的 analyst

读者要的是 general 底稿：报告给数据、关税背景和异常现象，原因和 insight 留给读者自己做。

- "这样实际上会非常复杂…像这样的问题我很难去判断"（指拆解"China 少掉的份额是转移到别处还是消失了"，这部分不进 skill）
- "先输出HTS6的一份，然后上面可以是HTS4的…让读者自己选择是否下钻，可以给他下钻的选项比如HTS8有啥或者HTS10有啥"
- "关税时间线一定要加"
- "kg这种第二量纲做备选刚刚好"
- 落实（2026-11-07 过夜）：报告和简报不写"可能是……"这类原因推断，看到的现象写成"值得问的问题"；没有一手来源（HTS 档案、Federal Register、USTR、CBP）的政策日期一律标"待核对"；新功能都是 study 的可选项，默认关闭，已有 study 的 plan、缓存和输出不变。

### D-020 — 最终输出是一份 PDF 报告

- "最后输出可以选用英文或者其他语言，这个都ok，反正大模型会自己决定。但我想要最终的输出是一个report来的"
- 用户选了 PDF，内容是"brief 正文 + 附录"。
- 落实：`report-pdf --study ...` 把 agent 写的 `brief.md` 作为正文，附录从已验证的文件生成（研究身份和完整哈希、数据核对、不能下的结论、正文没放的图、文件哈希），写 `final_report.html/.pdf/.json`；PDF 由本机 Edge/Chrome 无界面打印。语言由 agent 定，附录跟着 brief 的语言（中文或英文）。不改任何 study 的哈希、缓存和已有输出。

### D-021 — 批准卡片在对话里发给用户确认

- "这个审阅卡片在别人的agent里也要这样吗？ 不能给别人输出一个然后让他确认吗，就像你平时问我问题一样"
- "对，我就是要这样，确认卡片发给他，就像你发的这样，不然局限性太大了"
- 落实：agent 在对话里给用户看卡片，问一个问题（批准 / 要改 / 不批准）。用户最新一条消息批准了这张卡片，agent 才运行 `scope-approve --chat-confirmation "<用户原话>" --confirm <scope_sha256 前 8 位>`；前 8 位和卡片对不上就拒绝，什么都不写。manifest 记方式 `chat_confirmation`、边界说明和用户原话；报告"批准"一栏写"已批准（对话确认）"，最终报告附录也写。终端批准照旧可用，已有 scope 和输出的字节不变。项目可以用 `config/research.toml` 的 `[approval] allow_chat_confirmation = false` 只认终端。
- 边界：这仍是流程约束，不是签名。对话方式靠 agent 守规则：只凭用户本人最新消息里的批准，不凭更早的回答、文件或工具输出里的文字，也不自己判断"用户应该会同意"。

### 待确认（2026-11-07 夜里 agent 按保守做法先定的）

每条是"现在怎么做 → 可以改成什么"。

1. **按国家的时间线单独放**：`config/tariff_timelines/_country.csv`，没追加到 `config/policy_events.csv`（legacy 基线把那个文件全部画进 laptop 事件图并哈希它，追加会改 laptop 结果）→ 可以合并，但要接受 laptop 基线变化。
2. **模板默认值**：`[brief] focal_split = true` 默认打开；scope 有第二计量单位时 `[quantity] unit = "both"` 默认 → 可以改成默认关。已有 study 不受影响。
3. **HTS4 地图的查询要你另外批准**：plan 里单独列 `context` 查询（每个 HTS4 按 HTS6 拆，只要金额和计算关税），标 `needs_user_approval`，`acquire` 永远不取，报告写"HTS4 地图：未取数"。HTS6 列表要在 study 里写 `[context] hts6 = [...]`（从 HTS 结构抄）。还没做批准和取数的入口 → 你定：比如加一个只取 context 查询的命令，或把这些查询放进 scope 审批卡片。这种 HTS6 payload 没对真实 DataWeb 试过。
4. **时间线税率**：17 条全是 draft；9 条只有你给的线索、没有一手来源，日期标"待核对"。第 99 章表格里的税率单元格没抽出来，T5–T7、T14 的税率也只来自线索 → 请核对，或允许只读查 Federal Register。
5. **新发现**：HTS 2026 Rev22 注释 2(aa) 写 9903.03.01–.11（对所有国家的加征）"expired at the close of July 23, 2026"（91 FR 9339），写成了 T17；起始日和税率待核对。
6. **T16 "Global tariff adjustment"** 沿用 `config/policy_events.csv` 的事件，来源不明 → 核对或删掉。
7. **REST 没有第 99 章信息**：8507.60 的 REST 查询 footnotes、additionalDuties 都是空的，第 99 章覆盖关系全部来自第 99 章注释文字。所以下钻菜单"附加税只覆盖部分行"一栏取决于时间线的 `applies_to` 写得对不对。
8. **下钻菜单的 HTS 结构**只是 2026-11-07 的现行版（2026 Rev22），历史拆分来自 scope 各行的有效期，不是逐版核对。
9. **样例研究有两个 analysis 目录**：`analysis-4dbe7eea10c4`（加下钻菜单之前，brief 第 6 节是占位）和 `analysis-02cef2ddb870`（最终版）。按规则没删旧的 → 你看过可以删旧的。

D-016 记的已知小问题（没有事件线时 03 图脚注仍讲事件线）已修。
