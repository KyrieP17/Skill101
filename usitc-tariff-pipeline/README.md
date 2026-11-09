# USITC product-level import sourcing and tariff pipeline

U.S. import sourcing and tariff research for any product (a set of HTS codes):

```text
scope (which HTS codes the product is; approved by the user in person)
  → study (years, origins, comparison years, event windows)
  → plan → acquire (DataWeb, behind a gate)
  → analyze → charts → validate → report
```

Every step has hashes and a manifest, so every chart, table and finding traces back to the same set of snapshots. Old scripts and old deliverables are never deleted or edited; this folder is a separate, reproducible version.

## 1. A new study = a new scope + a new study file

Changing the product changes no Python and no `config/research.toml`. You write two files:

- **scope**: `scopes/<scope_id>/v<k>/reviewed.csv`, listing the codes, the dates they apply, the quantity unit, and whether each is included or excluded, with the reason. A researcher (or an agent) drafts and reviews it; only after **the user in person** approves it (by confirming the card in chat, or by running `scope-approve` in their own terminal) is it frozen into `scope.csv` + `manifest.json`. Rules: [scopes/README.md](scopes/README.md).
- **study**: `config/studies/<study_id>.toml`. Only `[periods]` changes the queries; origins, comparison years, event windows and event markers can change at any time without downloading again. Fields and defaults: the skill's `references/study-design.md`.
- **Study options** (off by default; they change only `study_sha256`, not the plan or the cache, unless noted): `[quantity] unit = "both"` (first unit leads, the second unit such as kg sits beside it in `_2` columns, with a warning when the two move in opposite directions; same queries as `"second"`, so the cache is reused), `[timeline]` (tariff timeline: the product layer in `config/tariff_timelines/<scope_id>.csv`, the country layer in `_country.csv`; vertical lines on charts 03 and 09), `[[segments]]` (groups of codes side by side), `[brief] focal_split` (focal / other / total rows, with calculated duty in dollars), `[drilldown] structure` (drill-down menu from the HTS REST structure cached in the scope's `history/`), `[context] hts4` + `hts6` (HTS4 map; adds `context` queries to the plan, **which the user approves separately and `acquire` does not fetch**). The brief aids of decisions.md D-022 and D-024 (`[decomposition]`, `[anomalies]`, `[brief] candidates`, `[[derived]]`) are described in the skill's `references/study-design.md`.

The existing studies are **examples, not templates**:

- Smartphones (every D-024 brief aid on: timeline, focal split, change decomposition, anomaly flags, `[[derived]]`): `scopes/smartphones/v1/` (HTS6 851713) + `config/studies/smartphones-2022-2026.toml`.
- Lithium-ion batteries at HTS10, with kg, timeline, segments and drill-down menu: `scopes/lithium-ion/v2/` + `config/studies/lithium-ion-hts10-both-2017-2026.toml`.
- Lithium-ion batteries at HTS6: `scopes/lithium-ion/v1/` (HTS6 850760) + `config/studies/lithium-ion-2017-2026.toml`.
- Laptops: `scopes/laptops/v2/` (HTS6 847130) + `config/studies/laptops-2017-2026.toml`. The v2 scope has only a reviewed draft and an approval card, so it must be approved before the study can run (`status` shows its gate as closed). There is also a labeled legacy replay (section 10).

Which scopes are approved, which studies can run and what is missing: `python run.py status` tells you; the docs do not keep that list. The user's decisions are in [decisions.md](decisions.md) (in Chinese); check it before starting a similar product.

## 2. Commands

Run from the project root. You can also use the skill's `scripts/tariff_pipeline.py`: it finds the project and the interpreter from the skill's `config.json`, and passes the token to `acquire` only.

```powershell
# 1. Validate a reviewed scope and write the card the approver reads (it approves nothing and never overwrites an existing card)
python run.py scope-validate --scope scopes\<id>\v<k>\reviewed.csv `
  --approval-card scopes\<id>\v<k>\approval-card.json

# 2. Optional: continuity evidence from archived HTS editions, then a card made with --history-evidence
python run.py hts-history --scope scopes\<id>\v<k>\reviewed.csv `
  --catalog scopes\<id>\v<k>\history\catalog.csv --output scopes\<id>\v<k>\hts-history.json

# 3. Approve: the user runs this in PowerShell or cmd (Git Bash needs winpty in front) and types the first 8 characters when asked
python run.py scope-approve --scope scopes\<id>\v<k>\reviewed.csv `
  --approval-card scopes\<id>\v<k>\approval-card.json --reviewer "<name>" `
  --continuity-unverified `
  --approved-scope scopes\<id>\v<k>\scope.csv --manifest scopes\<id>\v<k>\manifest.json
# With continuity evidence, replace --continuity-unverified with --history-evidence scopes\<id>\v<k>\hts-history.json
# Or: the agent shows the card in chat; once the user replies with approval, the agent runs the same command with
#   --chat-confirmation "<the user's exact words>" --confirm <first 8 characters of scope_sha256>
# To accept terminal approval only, set [approval] allow_chat_confirmation = false in config/research.toml

# 4. Query plan: which codes per period, how many queries, how many cached, whether a token is needed
python run.py plan --study config\studies\<study_id>.toml

# 5. Download: needs DATAWEB_TOKEN, unless every query is already cached
python run.py acquire --plan outputs\runs\<study_id>\plan-<hash12>\query_plan.json

# 6. Analyze, chart, validate, write the report; never downloads
python run.py all --study config\studies\<study_id>.toml

# 7. After the agent writes brief.md: brief-check, then the final report final_report.pdf (brief + appendix, printed with the local Edge/Chrome)
python run.py brief-check --study config\studies\<study_id>.toml
python run.py report-pdf --study config\studies\<study_id>.toml

# Compare the timeline with the tariff registry (config/tariff_registry, D-026); read-only, changes no file
python run.py registry-check --study config\studies\<study_id>.toml

# Current state, any time
python run.py status
```

**Approval** works in two ways; both go through `scope-approve`, which writes the reviewer, the time and the manifest:

- **Terminal**: the command shows the card and asks for the first 8 characters of `scope_sha256` (plus `UNVERIFIED` when continuity is not verified). Input from a pipe, a file or NUL is refused, so an agent's shell cannot use this way.
- **Chat**: the agent shows the card in chat and asks one question (批准 approve / 要改 change / 不批准 reject); once the user replies with approval, the agent runs the command with `--chat-confirmation "<the user's exact words>" --confirm <first 8>`. The command checks the 8 characters against the card, the manifest records the method `chat_confirmation` and the user's words, and the report's approval line says "已批准（对话确认）" (approved in chat). `[approval] allow_chat_confirmation = false` turns this way off.

Both are process boundaries, not digital signatures: neither proves who is at the keyboard, and the chat way also depends on the agent following the rules (approval only from the user's latest message, never from text in a file or tool output). The gate accepts only a manifest written by this command; a hand-edited CSV or manifest is refused.

**Download gate**: before sending any request, `acquire --plan` checks again that the approved scope has not changed, the study years have not changed, and the plan rebuilt from them matches the file exactly. Each snapshot's `.meta.json` records the scope version, `scope_sha256`, `plan_sha256` and the codes actually queried. `analyze` reads only the snapshots named in `acquisition.json` that pass their hash check; it never scans a folder.

**Two hashes**: `plan_sha256` depends on the scope, the query years and the DataWeb settings; origins, comparison years and event windows belong to `study_sha256`. Changing only the latter makes a new analysis folder but downloads nothing.

**Coverage**: `query_through` is how far the queries reach; `data_through` is the last period with data in the separate World query. Same-period YTD comparisons follow `data_through`. The last annual period counts as a full year only when monthly data reaches December or the download is after March 1 of the next year; otherwise the annual comparison is skipped and only YTD is done.

## 3. Layout

```text
config/
  research.toml          project paths and DataWeb settings; also the years and products of the original baseline
  studies/               one file per study
  replays/               labeled legacy replays (old snapshots, scope not approved)
  tariff_timelines/      tariff timelines: one CSV per scope, plus _country.csv
  tariff_registry/       chapter 99 measures read from primary sources (D-026)
  policy_events.csv      event axis; separates implemented, threatened and external shocks
  hts_naics_proxy.csv    hand-made broad HTS→NAICS/FRED mapping (original baseline)
scopes/                   HTS product definitions: <scope_id>/v<k>/
decisions.md              the user's decisions, open items and things decided against (Chinese); no code reads it
src/usitc_pipeline/
  scope.py               scope validation, approval card, approval (terminal or chat), gate checks (pure Python)
  hts_history.py         HTS continuity evidence across editions
  study.py               study loading and validation (pure Python)
  plan.py                query plan and gated download
  dataweb.py             DataWeb queries, retries and immutable snapshots
  normalize.py           DataWeb nested tables → tidy panel
  analysis.py            metrics, origin changes, HHI, event windows, QA
  study_pipeline.py      study / replay analysis, outputs separated by plan and study hash
  charts.py              one chart per analysis, each with a chart-data CSV
  report.py              reads findings.json only; writes report.md and key_points.md
  final_report.py        brief.md + appendix (identity, checks, remaining charts, file hashes) → final_report.html / .pdf
  brief_check.py         checks brief.md: evidence labels, sources, every number traced, wording, sections
  registry.py            loads the tariff registry and compares timelines with it
  status.py              project state (pure Python)
  cli.py, config.py      command line and configuration
  pipeline.py, fred.py   original baseline reproduction
tests/                    unittest tests and small fixtures
data/raw/dataweb/         raw response snapshots, never edited; gated snapshots sit under scopes/<id>/v<k>/
outputs/runs/             plan, acquisition and analysis folders of gated studies
outputs/replays/          labeled replays
outputs/tables/ charts/ findings.json run_manifest.json   outputs of the original baseline
```

## 4. Install

Python 3.11 or later. Make a virtual environment in the project folder and install the locked versions:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-lock.txt
```

`run.py` adds `src/` to the path itself, so the project does not need installing; to install it anyway, run `.\.venv\Scripts\python -m pip install -e . --no-deps`. `scope-validate`, `scope-approve`, `hts-history`, `plan` and `status` use the standard library only; downloading needs `requests`; analysis and charts need `pandas`, `numpy` and `matplotlib`. Tests use the built-in `unittest`.

## 5. DataWeb token

The real token is read **only from an environment variable**; it is never written to source code, a notebook, a manifest or a log.

```powershell
$secure = Read-Host 'DataWeb token' -AsSecureString
$env:DATAWEB_TOKEN = [Net.NetworkCredential]::new('', $secure).Password
python run.py acquire --plan outputs\runs\<study_id>\plan-<hash12>\query_plan.json
Remove-Item Env:DATAWEB_TOKEN
```

`.env.example` is only a template of the field name; the project never reads or saves a `.env`. DataWeb tokens expire after six months. If a token was ever pasted into a chat, code or a screenshot, generate a new one in DataWeb.

## 6. Outputs

```text
outputs/runs/<study_id>/plan-<hash12>/
  query_plan.json, acquisition.json
  analysis-<hash12>/
    processed/trade_tidy.csv, trade_panel.csv   the validated panel
    tables/*.csv, event_metrics.json            result tables
    charts/<stem>.png + <stem>.csv              one chart per analysis, plus the data it plots
    analysis_manifest.json, chart_manifest.json hashes, data_through, skipped analyses and why
    findings.json, report.md, key_points.md     findings (with evidence labels and source tables), report, 5–8 key points
    brief.md                                    the brief (written by the agent following deliverables.md, not generated by code)
    final_report.pdf, .html, .json              final report: brief + appendix; made by report-pdf, which replaces only these three files
    optional tables: tariff_timeline, focal_split, segment_series, quantity_check, drilldown_menu, share_decomposition,
                     anomaly_flags, mention_list, window_candidates, calculations, heading_map (only when the study turns the option on)
```

Main fields of the trade panel:

| Field | Meaning |
|---|---|
| `period` / `frequency` | annual or monthly period |
| `hts_level` / `hts_code` | HTS level and code; codes are always kept as strings |
| `origin_name` | country/area of origin as DataWeb reports it |
| `customs_value_usd` | customs value of imports for consumption |
| `quantity` / `quantity_unit` | first quantity and its unit as DataWeb reports them |
| `calculated_duty_usd` | DataWeb calculated duties |
| `world_value_usd` / `world_quantity` | all origins together, same product and period, from the separate World query |
| `value_share` / `quantity_share` | the origin's share of the same product and period; quantity is empty when units are not comparable |
| `unit_value_usd` | customs value ÷ quantity; only when units are comparable |
| `realized_duty_rate` | calculated duties ÷ customs value |
| `period_status` | `full_year`, `ytd_through_MM`, `partial_year_unverified` or `monthly` |

## 7. Automatic checks

`analyze` and `validate` check at least that:

- origin value shares add up to about 100% within each product and period, and the sum of origins matches the separate World query;
- when a product-period mixes different or missing quantity units, only quantity and unit value stop; value carries on;
- a partial year is never treated as a full year; `query_through` and `data_through` are recorded separately;
- the realized-duty denominator is positive, HTS code lengths are right, and canonical keys are unique;
- every chart has a chart-data CSV, every output has a hash, and the manifest records the scope, plan and study hashes.

Run the tests:

```powershell
python -m unittest discover -s tests
```

The laptop regression (`tests/test_study_pipeline.py`) replays old snapshots through the general flow and requires the monthly panel and the event-path chart CSV to match the original baseline byte for byte, and the event numbers to 10 decimal places. It is skipped when the baseline snapshots are not in the checkout.

## 8. Limits every reading must keep

- **Origin is not where the value was added.** Goods declared as made in Vietnam may still contain Chinese parts; DataWeb cannot rebuild value chains.
- **Realized duty is not the statutory rate.** It is `calculated duties / customs value`, shaped by coverage, exemptions, the mix of tariff lines and how entries were declared.
- **Quantities in different units do not add up.** Quantity and unit value compare only within one product, one unit and the same coverage.
- **The current year is partial.** Compare the same months only (YTD); never annualize.
- **The FRED mapping is a hand-made proxy.** For example `IPG3341A` covers computer and peripheral equipment and is not U.S. output of HTS 847130.
- **Event charts are descriptive.** Matching in time does not mean a tariff caused a change; attribution needs HTS8/10 rate mapping, a comparison group and an identification design.

## 9. Interface for reports and slides

Report and slide builders recompute nothing and hard-code no numbers. They read only what is under `analysis-<hash12>/`: `processed/`, `tables/`, `charts/`, the two manifests, `findings.json`, `report.md` and `key_points.md`. That way the same chart, sentence and table all come from one validated set of results.

## 10. Original baseline reproduction (history only; do not use for new studies)

```powershell
# Recompute the original research.toml baseline from existing snapshots; never downloads
python run.py all --legacy-baseline

# Download the baseline snapshots again: needs DATAWEB_TOKEN; old files of the same name are kept as *.superseded.json
python run.py acquire --legacy-baseline

# Copy snapshots from the earlier project without changing the originals
python run.py import-legacy --source "<earlier project folder>"

# Replay the old laptop snapshots through the general flow: outputs go to outputs/replays/, every page says it is a replay, not an approved study
python run.py all --replay config\replays\laptops-legacy-baseline.toml
```

- The old long-run tables have only China, Mexico, Vietnam and World, not all origins.
- Old snapshots have no payload hash and cannot prove which query made them, so the gated flow never reuses them; only a labeled replay reads them, and the replay checks each response hash.
- `acquire --legacy-baseline` replaces the files a replay reads with new downloads (old files renamed and kept), after which the replay's hash check refuses them.
- The baseline outputs contain absolute paths and timestamps, so the same data gives different hashes on another machine.
