# 关税库（tariff registry）

第 99 章加征措施的一手来源记录（decisions.md D-026）。时间线 CSV（`config/tariff_timelines/`）还是每个 study 自己的输入；这里是对照用的底账。

| 文件 | 一行是什么 |
|---|---|
| `sources.csv` | 一份读过的一手来源：Federal Register 文件（文档号、引用、发布日、签署日、网址、原文 SHA-256）或 HTS 档案文件（版本、网址、PDF 的 SHA-256） |
| `measures.csv` | 一次有日期的变化：日期、`date_type`（announced / signed / effective / ended）、第 99 章税号或区间、变化、来源地、`products`（all = 全部产品，除非 coverage 写了例外；list = 只覆盖 coverage 里的产品）、来源 id、HTS 注释、`status` |
| `coverage.csv` | 某条措施对某个 HTS6：covered / exempt / not_covered / suspended，证据是哪条 HTS 注释 |

规则：

- `status = verified`：日期、变化和税号都在所列来源里读到。`待核对`：有一项没读到，`notes` 写明是哪一项。
- `ended` 是最后适用的那一天（例如 9903.03.01 在 2026-07-23 日终到期）。
- 只收第 99 章措施；MFN 基础税率不在这里，所以时间线里的基础税率行在 `registry-check` 里总是 `not_in_registry`。
- 来源只能是 hts.usitc.gov、federalregister.gov、ustr.gov、cbp.gov；加载时会检查。新增来源要用户同意读那个网站。
- 第 99 章 PDF 的税号表格（税率格）抽不出文字，税率取自 FR 原文或 HTS 注释的文字。

用法（只读，从不改时间线）：

```powershell
python run.py registry-check                                  # 校验三张表，列出待核对
python run.py registry-check --study config\studies\<id>.toml # 这个 study 的时间线对照库
```

结果里：`match`（同日、来源地和税号对得上）、`date_differs`（税号和来源地对得上、日期不同）、`not_in_registry`、`missing_from_timeline`（库里有、能覆盖到本 scope 的 HTS6、时间线没有，含豁免）、`before_study_period`、`checked_not_covered`。改时间线会改 `study_sha256`，要新跑分析，先问用户。

本次建库（2026-11-08）读过的：27 份来源（25 份 FR、2 个 HTS 第 99 章版本：2025 Rev32、2026 Rev22），22 条措施，49 条覆盖记录（8517.13 手机、8471.30 笔记本、8507.60 锂电池）。
