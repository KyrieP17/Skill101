# Skill101 — tariff-sourcing-research

An agent skill plus its Python pipeline for U.S. import sourcing research from USITC DataWeb: which origins supply an HTS product, how that changed around tariff dates, and what the data cannot say.

## Contents

| Path | What it is |
|---|---|
| `skills/tariff-sourcing-research/` | the skill: `SKILL.md`, references, assets, scripts (scope resolver, current-HTS check, pipeline wrapper), evals, tests |
| `usitc-tariff-pipeline/` | the pipeline: `src/usitc_pipeline`, tests, config (studies, tariff timelines, tariff registry), scopes (approved product definitions), `decisions.md` |
| `usitc-tariff-pipeline/config/tariff_registry/` | tariff registry: chapter 99 measures whose dates, headings and coverage were read from the Federal Register and HTS archive, each with its source and SHA-256; `run.py registry-check --study ...` compares a timeline with it (D-026) |
| `samples/` | two final reports (PDF): smartphones (HTS 8517.13, 2022–2026) and lithium-ion batteries (HTS 8507.60 down to HTS10, 2017–2026) |

## How it runs

The skill is instructions plus scripts; it does nothing by itself. Your agent (for example Claude Code) reads `SKILL.md` and runs the pipeline's Python **on your own machine**, so you need:

- an agent that can run commands on your computer;
- Python 3.11 or later on that computer (the agent can set up the virtual environment and install the packages for you);
- your own USITC DataWeb account and API token, generated on the DataWeb site. It never goes into the chat: the download is one command you run in your own terminal, typing the token at a hidden prompt (the agent can write that command for you);
- your approval of each product scope. Only you can approve it; the agent drafts it and asks.

Everything else (finding the codes, planning queries, analysis, charts, checks, the report) the agent runs for you.

## Try it

Tested on Windows 11 with Python 3.11+. Not tested on macOS or Linux; adjust paths and commands there (for example `.venv/bin/python`).

**1. Install the pipeline** (PowerShell; or ask your agent to do steps 1 and 2)

```powershell
git clone https://github.com/KyrieP17/Skill101.git
cd Skill101\usitc-tariff-pipeline
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
.\.venv\Scripts\python run.py status
```

`status` should list three approved scopes: `smartphones/v1`, `lithium-ion/v1` and `lithium-ion/v2`.

**2. Connect the skill**

Put the whole `skills/tariff-sourcing-research` folder where your agent reads skills (Claude Code: `~/.claude/skills/` or `.claude/skills/` inside a project). In that copy, copy `config.example.json` to `config.json` and set `project_root` to the absolute path of `usitc-tariff-pipeline`.

**3. Run a sample first** (its scope is already approved)

```powershell
.\.venv\Scripts\python run.py plan --study config\studies\smartphones-2022-2026.toml
```

The plan hash should be `2ca86ee6e514`, the same queries as the smartphone report in `samples/`. Type the token in your own terminal when downloading; never paste it into a chat, code or a screenshot:

```powershell
$secure = Read-Host 'DataWeb token' -AsSecureString
$env:DATAWEB_TOKEN = [Net.NetworkCredential]::new('', $secure).Password
.\.venv\Scripts\python run.py acquire --plan outputs\runs\smartphones-2022-2026\plan-2ca86ee6e514\query_plan.json
Remove-Item Env:DATAWEB_TOKEN
.\.venv\Scripts\python run.py all --study config\studies\smartphones-2022-2026.toml
```

DataWeb updates and revises its data, so your numbers may differ a little from the sample. The final PDF needs the agent to write `brief.md` first, then `run.py report-pdf --study ...` (which runs `brief-check` first).

**4. Study your own product**

Tell your agent what you want, for example "Where does the U.S. import HTS 8504.40 power supplies from, and how did that change around the tariffs?" The agent drafts an HTS6 scope and shows it to you; it approves the scope and downloads data **only after you confirm**.

## Known limits

- The tariff registry covers only 8517.13 (smartphones), 8471.30 (laptops) and 8507.60 (lithium-ion batteries). Other products need a new timeline, and `registry-check` can only compare against measures already in the registry.
- Timeline rows are all `draft` (not checked by the user). Rows marked 待核对 (to verify) lack a primary source or one item of it; `notes` says what. The registry also has two measures and three coverage rows still to verify (`usitc-tariff-pipeline/decisions.md` D-026, D-027).
- Reports give data, background and anomalies, never causes (D-019).
- The reports, `decisions.md`, the design notes, and the `notes` columns of the timelines and registry are in Chinese. The READMEs, the skill's instructions and the code comments are in English.

Questions and suggestions: open a GitHub issue.

## Not in this repository

- The DataWeb token: typed only in your own terminal, never written to a file.
- Raw data and analysis outputs (`data/`, `outputs/`): download and rerun them per study with the pipeline.
- HTS archive PDFs (`scopes/*/*/history/*.pdf`): public files; their URLs and SHA-256 are in `SOURCES.md` in the same folder.

## License

MIT, see [LICENSE](LICENSE). The data comes from USITC DataWeb and the tariff sources from the Federal Register and the HTS archive, all public U.S. government information; follow DataWeb's own terms when you use it.
