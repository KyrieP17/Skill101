# USITC DataWeb 可复用 Skill 与 Workflow 设计

> 2026-11-06 更新：批准已改为用户本人在交互终端运行 `scope-approve`，不再有 external-approval 工件。现行流程以 README 第 2 节和 skill 的 references 为准；本文里关于外部审批的描述已过时，保留作设计记录。

## 目标

建立一套可复用的产品级美国进口研究框架。用户只需要用普通语言说明产品、时间和研究问题，系统负责：

```text
识别研究需求
→ 查找 HTS 候选代码
→ 审核产品范围和历史连续性
→ 在外部审批边界确认并冻结研究口径
→ 生成 DataWeb 查询
→ 取数、验证和分析
→ 输出图表与报告证据
```

这是一套研究口径确认流程，不是具有法律效力的海关商品归类服务。

## 谁负责什么

| 角色 | 责任 |
|---|---|
| 用户或分析师 | 描述产品和研究问题；在存在实质歧义时批准研究范围；决定哪些推断可以用于汇报 |
| Skill | 判断是否应该触发、选择工作模式、执行研究 gate、决定读取哪些 reference 或运行哪些 script |
| Python workflow | 完成 HTS 检索、scope 验证、DataWeb 取数、清洗、计算、QA、图表和 manifest |
| DataWeb token | 由实际运行者个人提供；只在 acquisition 阶段注入，不写入 Skill、Notebook、Git、日志或 manifest |

用户不需要提前知道 HTS code，也不需要自己写技术规则。

## 用户最少需要提供什么

```text
产品：
时间范围：
重点国家或地区：
主要研究问题：
希望输出：
```

示例：

```text
产品：锂离子电池
时间范围：2017–2026
重点来源地：China、Vietnam、South Korea、World
主要问题：关税变化后中国的进口份额去了哪里，quantity 是否同步变化？
希望输出：验证后的数据表、三张图和一份 short report
```

如果商品归类确实依赖缺失信息，系统一次性询问会改变税号选择的属性，例如：

- 一次电池还是二次电池；
- lithium-ion 还是其他 chemistry；
- cell、assembled battery/pack 还是 parts；
- 整机还是零部件；
- 主要用途、材质、形态和关键规格。

## 哪些说法应该触发 Skill

不要只堆关键词，最好使用真实的完整问题。

- “帮我查 lithium-ion batteries 对应的 HTS code。”
- “用 DataWeb 看这个产品从中国转去了哪里。”
- “比较中国、越南和墨西哥的美国进口份额。”
- “研究关税前后 value、quantity 和 unit value 怎么变化。”
- “检查这个 HTS code 在 2017–2026 是否连续。”
- “用已经验证的数据解释 laptop 的中国份额下降去了哪里。”
- “把产品级关税研究做成图表和 short report。”

有用的概念包括：

```text
USITC DataWeb
HTS code / 税号
U.S. imports for consumption
country or area of origin
中国份额下降
sourcing reallocation
value / quantity / unit value / import share
calculated duties / tariff treatment
policy effective date
HTS6 / HTS8 / HTS10
EDA / chart / report / presentation
```

## 哪些说法不应该触发 Skill

- “泛泛讲讲美国提高关税对全球通胀的影响。”
- “总结一下今天的国际贸易新闻。”
- “给我具有法律效力的海关归类决定。”
- “分析美国出口，但不使用现在的进口研究框架。”
- “解释一下什么是自由贸易。”

## 建议使用的 Skill Description

```yaml
---
name: tariff-sourcing-research
description: Resolve product descriptions into reviewable HTS scopes, then run, extend, validate, or explain reproducible USITC DataWeb research on U.S. import structure, sourcing reallocation, product-level value and quantity, tariff timing, and report-ready evidence. Use for product-level HTS discovery or validation, DataWeb collection, tariff-sourcing analysis, and related reports; do not use for legal customs classification, generic macro commentary, or unrelated trade policy.
---
```

Description 只负责说明：

1. 这个 Skill 做什么；
2. 什么请求应该触发；
3. 哪些相邻请求不应该触发。

详细 API、数据字段、分析规则和案例不放进 description。

## 主 Workflow

```text
Product request
    ↓
Resolve HTS candidates
    ↓
Review ambiguity and historical continuity
    ↓
External approval and frozen scope
    ↓
Build DataWeb query plan
    ↓
Acquire immutable raw snapshots
    ↓
Normalize and validate
    ↓
Analyze value, quantity, source share and duties
    ↓
Generate chart-data, charts, findings and run manifest
```

## Resolve、Review 和 Approve

### Resolve

Resolver 调用 USITC 官方公开 HTS Search，保留：

```text
search query
official displayed code
normalized numeric code
HTS2 / HTS4 / HTS6 / HTS8 / HTS10
official description
quantity unit
general / special / other rate fields
retrieval timestamp
source URL
chapter 98 / 99 flag
```

Resolve 阶段只生成候选，不批准代码、不修改研究配置，也不调用 DataWeb。

规则：

- 不自动选择关键词搜索的第一条；
- 先用精确短语或关键技术属性，再逐步扩大搜索；
- 不根据进口金额大小决定某个代码是否属于研究范围；
- HTS6 用于产品族分析；
- HTS8 用于美国 tariff-treatment line；
- HTS10 用于最细统计、部分税目和排除项；
- 没有标准的 HTS5 研究层级；
- Resolve 不需要 DataWeb token。

### Review

需要检查：

1. 描述是否覆盖目标产品；
2. 整机和 parts/accessories 是否混在一起；
3. 相邻的 `other` 类别和可能的排除项；
4. quantity unit；
5. 研究年份内的代码有效性；
6. 代码拆分、合并、新增、删除、描述变化和单位变化；
7. 是否误把 Chapter 98/99 临时条款当成普通产品税号。

### External approval boundary

Agent 只能把所有行推进到 `reviewed`，并生成没有批准效力的 approval card。它不得填写 `approved`、`reviewer`、`approved_at`，不得创建 external approval artifact，也不得代表用户调用 `scope-approve`。

外部工件至少绑定：

```text
scope_sha256
approval_card_sha256
decision = approved
reviewer
approved_at_utc
external_process = true
procedural_boundary
```

`scope-approve` 消费工件后，批准后的 scope 与 manifest 至少记录：

```text
scope_id
scope_version
product_id
product_name
HTS release
HTS level and code
include / exclude decision
rationale
valid_from / valid_to
review status
reviewer
approved_at
source URL
```

Validator 在 review 阶段即生成稳定的 `scope_sha256`；外部工件必须批准同一个 hash 和同一张 card。代码只能证明文件绑定，不能在没有数字签名/身份系统时证明 reviewer 身份、权限或意图。代码、有效期或 include/exclude 决定变化时，创建新版本，不覆盖旧 scope。

## 工程任务 1：历史 HTS 连续性

当前 HTS Search 只能确认现行候选，不能直接证明同一代码适用于 2017–2026 或 1989–2026。

历史模块应自动完成：

```text
已确认的产品概念
→ 查询相应年份 HTS archive
→ 按年份匹配代码、描述和单位
→ 标记 split / merge / creation / deletion / unit change
→ 对模糊映射进行人工审核
→ 生成 code × validity period 表
```

目标输出：

| 有效期 | HTS code | 关系 | Quantity unit | 状态 |
|---|---|---|---|---|
| 2017–2021 | code A | predecessor | No. | reviewed |
| 2022–2024 | code B | description changed | No. | reviewed |
| 2025–2026 | code C | current | No. | reviewed |

连续性没有通过之前，可以报告“当前候选代码”，但不能声称整个历史窗口代表完全相同的产品定义。

## 工程任务 2：把 Scope Manifest 接入 Pipeline

新产品不能只靠修改 `research.toml` 就直接取数。

```text
reviewed scope CSV + official history evidence
→ approval card + external approval artifact
→ scope-approve
→ approved scope manifest + scope_sha256
→ query plan derived from approved codes
→ DataWeb acquisition
→ raw snapshot metadata
→ final run_manifest.json
```

出现以下情况时，acquisition 应拒绝运行：

- 缺少 scope manifest；
- approval card / external artifact / history evidence 缺失、hash 不符或被修改；
- included rows 混用不同 HTS level；
- 配置代码和批准代码不一致；
- 查询年份超出代码有效期；
- scope 文件和 hash 不一致。

Raw snapshot metadata 和最终 run manifest 应记录：

```json
{
  "scope_id": "laptops-v1",
  "scope_version": "1",
  "scope_sha256": "...",
  "hts_level": 10,
  "approved_codes": ["8471300100"]
}
```

Token 不得出现在这些文件中。

## 标准分析内容

Scope 批准后，统一计算：

- nominal customs value；
- first-unit quantity 和 quantity unit；
- 单位一致时的 unit value；
- country or area of origin share；
- 独立 World total reconciliation；
- calculated duties 和 realized-duty screening；
- matched-month YTD comparison；
- 来源地份额流入和流出；
- 美国总进口需求方向；
- 政策和外部冲击时间点；
- 在映射合理时使用宽口径国内生产 proxy。

Country or area of origin 不等于全部增加值来源。来源地份额上升是美国进口来源重分配的证据，不是某家中国工厂直接搬迁的证明。

## Skill 文件结构

```text
tariff-sourcing-research/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── hts-scope-resolution.md
│   ├── pipeline-operations.md
│   ├── evidence-rules.md
│   └── deliverables.md
├── scripts/
│   ├── hts_scope_resolver.py
│   ├── validate_scope.py
│   └── tariff_pipeline.py
├── assets/
│   └── product_scope_template.csv
├── evals/
│   └── behavior_cases.json
└── tests/
    └── test_hts_scope.py
```

## 验证方式

### Deterministic Tests

- 正确处理带点和不带点的 HTS code；
- 正确保留 HTS6、HTS8 和 HTS10 父级关系；
- Chapter 98/99 必须被标记，不能静默删除；
- 拒绝无效代码长度；
- 需要批准时拒绝 draft scope；
- 拒绝混合 included HTS levels；
- 相同 scope 产生相同 hash；
- 不读取、打印或写入 token。

### Behavioral Tests

- 清晰产品：portable computers；
- 模糊产品：没有 chemistry 或用途说明的 battery；
- 已知代码但时间很长：检查历史连续性；
- 只读解释：复用 validated outputs，不刷新；
- 负例：泛宏观关税评论；
- 边界：要求具有法律效力的海关归类。

测试重点是 Skill 是否改变了实际决策，而不是回答是否使用相同标题或句式。

## 当前状态（v2 实现后）

已经完成：

- Skill description 和 routing；
- 当前官方 HTS candidate resolver；
- scope CSV contract 和 validator；
- deterministic scope hash；
- product-scope template；
- approval card、外部工件消费与 manifest 复验；
- hash-backed 历史 HTS catalog/evidence 与 continuity gate；
- study-aware DataWeb 取数、分析、图表和验证 workflow；
- 单元测试、laptop golden、离线新品类 E2E 和 16 个行为 contract。

下一步：

1. 为正式产品下载官方历史 edition 文件并建立真实 catalog；
2. 接入带来源的 HTS8/10 statutory tariff-treatment mapping；
3. 在需要因果判断时加入对照组与识别设计；
4. 让 short report / slide 模板只消费验证后的 analysis run。

## 可直接使用的 Prompt

```text
Use the tariff-sourcing research workflow.

产品：锂离子电池
时间范围：2017–2026
重点来源地：China、Vietnam、South Korea、World
主要问题：关税变化后中国的进口份额去了哪里？分别分析 value、quantity、unit value 和美国总进口需求。
输出：先展示 reviewed HTS scope 和 approval card；由外部审批流程产生绑定工件后，再运行 DataWeb 分析并输出图表和 short report。
```

用户负责表达研究需求和通过 Agent 无法伪造的外部流程批准实质性口径。税号候选、历史证据整理、查询、计算、QA、provenance 和交付物生成由 workflow 负责。
