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

## 试用

在 Windows 11、Python 3.11 以上测试过；Mac 和 Linux 没测过，路径和命令要自己换（例如 `.venv/bin/python`）。

需要准备：

- Python 3.11 或更高版本。
- 一个能读 skill 的 agent，例如 Claude Code。
- 你自己的 USITC DataWeb 账号和 API token（在 DataWeb 网站登录后生成，半年过期）。仓库里没有数据，样例也要用你的 token 重新取数。

**1. 装管线**（PowerShell）

```powershell
git clone https://github.com/KyrieP17/Skill101.git
cd Skill101\usitc-tariff-pipeline
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
.\.venv\Scripts\python run.py status
```

`status` 应该列出三个已批准的 scope：`smartphones/v1`、`lithium-ion/v1`、`lithium-ion/v2`。

**2. 接上 skill**

把 `skills/tariff-sourcing-research` 整个目录放到 agent 读 skill 的位置（Claude Code：`~/.claude/skills/` 或项目里的 `.claude/skills/`）。在放好的那份目录里，把 `config.example.json` 复制成 `config.json`，`project_root` 填 `usitc-tariff-pipeline` 的绝对路径。

**3. 先跑一个样例**（scope 已批准，不用再批）

```powershell
.\.venv\Scripts\python run.py plan --study config\studies\smartphones-2022-2026.toml
```

plan 的哈希应该是 `2ca86ee6e514`，和 `samples/` 里手机报告是同一批查询。取数时在你自己的终端里输入 token，不要贴进聊天、代码或截图：

```powershell
$secure = Read-Host 'DataWeb token' -AsSecureString
$env:DATAWEB_TOKEN = [Net.NetworkCredential]::new('', $secure).Password
.\.venv\Scripts\python run.py acquire --plan outputs\runs\smartphones-2022-2026\plan-2ca86ee6e514\query_plan.json
Remove-Item Env:DATAWEB_TOKEN
.\.venv\Scripts\python run.py all --study config\studies\smartphones-2022-2026.toml
```

DataWeb 会更新、修订数据，取到的数可能和样例不完全一样。最终的 PDF 报告要 agent 先写 `brief.md`，再跑 `run.py report-pdf --study ...`（先过 `brief-check`）。

**4. 做你自己的产品**

在 agent 里直接说要研究什么，例如"美国从哪些地方进口 HTS 8504.40 的电源，关税前后怎么变"。agent 先起草 HTS6 范围给你看；**只有你本人确认后**才会批准范围、取数。

**已知限制**

- 关税库只覆盖 8517.13（手机）、8471.30（笔记本）、8507.60（锂电池）。别的产品要新建时间线，`registry-check` 只能对照库里已有的措施。
- 时间线的行都是 draft（未经用户核对）。标"待核对"的行缺一手来源或缺一项，`notes` 写了缺什么；关税库里也有两条措施和三条覆盖记录待核对（见 `usitc-tariff-pipeline/decisions.md` D-026、D-027）。
- 报告只给数据、背景和异常，不给原因（D-019）。
- 文档和报告目前是中文。

问题和建议请开 GitHub issue。

## 不在仓库里的东西

- DataWeb token：只在你自己的终端里输入，不写进任何文件。
- 原始数据和分析输出（`data/`、`outputs/`）：用管线按 study 重新取数、重跑。
- HTS 档案 PDF（`scopes/*/*/history/*.pdf`）：公开文件，下载地址和 SHA-256 在同目录 `SOURCES.md`。

## 许可证

MIT，见 [LICENSE](LICENSE)。数据来自 USITC DataWeb，关税来源是 Federal Register 和 HTS 档案，都是美国政府公开信息；使用 DataWeb 请遵守它自己的条款。
