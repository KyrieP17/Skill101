# Skill101 — tariff-sourcing-research

An agent skill plus its Python pipeline for U.S. import sourcing research from USITC DataWeb: which origins supply an HTS product, how that changed around tariff dates, and what the data cannot say.

## Contents

| Path | What it is |
|---|---|
| `skills/tariff-sourcing-research/` | the skill: `SKILL.md`, references, assets, scripts (scope resolver, current-HTS check, pipeline wrapper), evals, tests |
| `usitc-tariff-pipeline/` | the pipeline: `src/usitc_pipeline`, tests, config (studies, tariff timelines, tariff registry), scopes (approved product definitions), `decisions.md` |
| `usitc-tariff-pipeline/config/tariff_registry/` | tariff registry: chapter 99 measures whose dates, headings and coverage were read from the Federal Register and HTS archive, each with its source and SHA-256; `run.py registry-check --study ...` compares a timeline with it (D-026) |
| `samples/` | two final reports (PDF): smartphones (HTS 8517.13, 2022–2026; English) and memory integrated circuits (HTS 8542.32 with HTS10 product mix, 2017–2026; English) |

## How it runs

The skill is instructions plus scripts; it does nothing by itself. Your agent (for example Claude Code) reads `SKILL.md` and runs the pipeline's Python **on your own machine**, so you need:

- an agent that can run commands on your computer;
- Python 3.11 or later on that computer (the agent can set up the virtual environment and install the packages for you);
- your own USITC DataWeb account and API token, generated on the DataWeb site. It never goes into the chat: the download is one command you run in your own terminal, typing the token at a hidden prompt (the agent can write that command for you);
- your approval of each product scope. Only you can approve it; the agent drafts it and asks.

Everything else (finding the codes, planning queries, analysis, charts, checks, the report) the agent runs for you.

The pipeline supports Windows, macOS and Linux with Python 3.11+.

## Try it

**1. Install the pipeline** (Windows PowerShell; or ask your agent to do steps 1 and 2)

```powershell
git clone https://github.com/KyrieP17/Skill101.git
cd Skill101\usitc-tariff-pipeline
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
.\.venv\Scripts\python run.py status
```

`status` lists approved scopes, scopes still awaiting approval, and study gates. The repository will gain more scopes over time, so installation success is not tied to a fixed count. For example, `laptops/v2` showing `gate closed` / `cannot plan` means that its approval or complete study is still missing, not that the installation is broken.

macOS / Linux (bash or zsh):

```bash
git clone https://github.com/KyrieP17/Skill101.git
cd Skill101/usitc-tariff-pipeline
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-lock.txt
.venv/bin/python run.py status
```

**2. Connect the skill**

Put the whole `skills/tariff-sourcing-research` folder where your agent reads skills (Claude Code: `~/.claude/skills/` or `.claude/skills/` inside a project). In that copy, copy `config.example.json` to `config.json` and set `project_root` to the absolute path of `usitc-tariff-pipeline`. The example does not hard-code an interpreter: the wrapper detects `.venv/Scripts/python.exe` on Windows or `.venv/bin/python` on macOS/Linux. Add the optional `python` key only when using an environment elsewhere.

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

macOS / Linux (bash or zsh; input is hidden):

```bash
.venv/bin/python run.py plan --study config/studies/smartphones-2022-2026.toml
printf 'DataWeb token: '
IFS= read -rs DATAWEB_TOKEN
printf '\n'
export DATAWEB_TOKEN
.venv/bin/python run.py acquire --plan outputs/runs/smartphones-2022-2026/plan-2ca86ee6e514/query_plan.json
unset DATAWEB_TOKEN
.venv/bin/python run.py all --study config/studies/smartphones-2022-2026.toml
```

DataWeb updates and revises its data, so your numbers may differ a little from the sample. Everything defaults to English. Only report prose and the generated appendix switch to Chinese when the conversation or request is Chinese; charts and HTS category labels remain English. The final PDF needs the agent to write `brief.md` first, then `run.py report-pdf --study ... --lang en|zh` (which runs `brief-check` first).

**4. Study your own product**

Tell your agent what you want, for example "Where does the U.S. import HTS 8504.40 power supplies from, and how did that change around the tariffs?" The agent drafts an HTS6 scope and shows it to you; it approves the scope and downloads data **only after you confirm**.

## Known limits

- The tariff registry is not a universal rate database that automatically applies every country-wide policy to every product. A measure enters a product tariff chart only when its date, origin, HTS coverage, legal lifecycle and primary sources are verified. Missing product coverage fails closed.
- Timeline `review_status` remains an internal research field. A genuinely missing source, effective date, coverage rule or lifecycle rule stays unresolved; DataWeb realized duty never substitutes for statutory treatment.
- An English final PDF needs an English `brief.md` plus explicit `report-pdf --lang en`. Some internal analysis artifacts still use Chinese templates and should not be treated as an English client report.
- `.gitattributes` preserves the bytes of files bound by SHA-256. On macOS/Linux, do not convert line endings across the repository: that creates a large meaningless diff and invalidates bound hashes.
- Reports give data, background and anomalies, never causes (D-019).
- Reports, `decisions.md`, design notes, and some timeline/registry notes remain in Chinese. The READMEs, skill instructions and code comments are in English.

Questions and suggestions: open a GitHub issue.

## Not in this repository

- The DataWeb token: typed only in your own terminal, never written to a file.
- Raw data and analysis outputs (`data/`, `outputs/`): download and rerun them per study with the pipeline.
- HTS archive PDFs (`scopes/*/*/history/*.pdf`): public files; their URLs and SHA-256 are in `SOURCES.md` in the same folder.

## License

MIT, see [LICENSE](LICENSE). The data comes from USITC DataWeb and the tariff sources from the Federal Register and the HTS archive, all public U.S. government information; follow DataWeb's own terms when you use it.

