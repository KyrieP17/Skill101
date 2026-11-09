# Skill101 — tariff-sourcing-research

An agent skill plus its Python pipeline for U.S. import sourcing research from USITC DataWeb: which origins supply an HTS product, how that changed around tariff dates, and what the data cannot say.

一个 agent skill 和它背后的 Python 管线：从 USITC DataWeb 取美国进口数据，回答"某个 HTS 产品从哪些来源地进口、关税前后怎么变"，并写清这份数据回答不了什么。

## 目录

| 路径 | 内容 |
|---|---|
| `skills/tariff-sourcing-research/` | skill 本体：`SKILL.md`、references、assets、scripts（scope 解析、现行 HTS 核对、管线包装器）、evals、tests |
| `usitc-tariff-pipeline/` | 管线项目：`src/usitc_pipeline`、tests、config（study、关税时间线、关税库）、scopes（已批准的产品范围）、`decisions.md` |
| `usitc-tariff-pipeline/config/tariff_registry/` | 关税库：第 99 章加征措施，日期、税号和覆盖范围都从 Federal Register 和 HTS 档案原文核对，带来源和 SHA-256；`run.py registry-check --study ...` 拿时间线对照它（D-026） |
| `samples/` | 两份最终报告 PDF：智能手机（HTS 8517.13，2022–2026）和锂离子电池（HTS 8507.60 到 HTS10，2017–2026） |

## 开始

1. 在 `usitc-tariff-pipeline/` 里建虚拟环境并装依赖（见该目录 `README.md`）。
2. 把 `skills/tariff-sourcing-research/config.example.json` 复制成 `config.json`，`project_root` 填管线目录的绝对路径。
3. 把 skill 目录放到你的 agent 能读到的 skills 位置。

## 不在仓库里的东西

- DataWeb token：只在你自己的终端里输入，不写进任何文件。
- 原始数据和分析输出（`data/`、`outputs/`）：用管线按 study 重新取数、重跑。
- HTS 档案 PDF（`scopes/*/*/history/*.pdf`）：公开文件，下载地址和 SHA-256 在同目录 `SOURCES.md`。

关税时间线里标"待核对"的行没有查过一手来源；关税库里也有两条措施和三条覆盖记录标着待核对，`notes` 写了缺什么。报告只给数据、背景和异常，不给原因（见 `usitc-tariff-pipeline/decisions.md` D-019）。
