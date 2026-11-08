# Operations

Read this before running a pipeline command, and when one fails.

## Setup

- `scripts/tariff_pipeline.py` finds the project from `--project-root`, else `TARIFF_PIPELINE_ROOT`, else `project_root` in the skill's `config.json`. If none is set or the path is wrong it stops: ask the user for the project folder (and interpreter), then write `config.json`. Never guess a path.
- Interpreter: `--python`, else `python` in `config.json` (relative to the project), else the project's `.venv`, else the one running the wrapper. `status`, `scope-validate`, `hts-history`, `scope-approve`, `plan` and the gate in `acquire` need only Python 3.11+; downloading needs `requests`; `analyze`, `charts` and `report` need pandas and matplotlib. A new environment: `python -m venv .venv`, then `.venv\Scripts\python -m pip install -r requirements-lock.txt` in the project.
- `--dry-run` prints the resolved command and whether the token would be forwarded, never the token itself.

## Commands

The wrapper runs inside the project root, so project-relative paths work as written. `python` below stands for a real Python 3.11+ (the project's `.venv\Scripts\python.exe` works); on Windows a bare `python` may be the Store stub.

```powershell
python scripts\tariff_pipeline.py status                    # scopes, approvals, studies, runs, limits
python scripts\tariff_pipeline.py scope-validate ...         # the approval card: scope-resolution §7
python scripts\tariff_pipeline.py hts-history ...            # continuity evidence: scope-resolution §6
python scripts\tariff_pipeline.py scope-approve ... --chat-confirmation "<user's words>" --confirm <8>   # only after the user approves the card in chat (scope-resolution §7)
python scripts\tariff_pipeline.py plan --study config\studies\<study_id>.toml
python scripts\hts_current_check.py --scope <project>\scopes\<id>\v<k>\scope.csv --output <...>.json
python scripts\tariff_pipeline.py acquire --plan outputs\runs\<study_id>\plan-<h12>\query_plan.json
python scripts\tariff_pipeline.py all --study config\studies\<study_id>.toml   # analyze, charts, validate, report
python scripts\tariff_pipeline.py analyze --study ...        # also charts, validate, report one at a time
python scripts\tariff_pipeline.py report-pdf --study config\studies\<study_id>.toml   # after brief.md: final_report.pdf
python scripts\tariff_pipeline.py test                       # the project's unit tests
```

- `plan` prints the codes queried per period, the job count, `jobs_already_cached` and whether a token is needed. The same inputs give the same `plan_sha256` and directory on any machine, so it is safe to repeat.
- `acquire --plan` re-verifies the approved scope, rebuilds the plan and refuses if anything changed. Only this command receives `DATAWEB_TOKEN`; with every job cached it needs none.
- `all` never downloads.
- `--replay config\replays\<id>.toml` (instead of `--study`) re-analyzes old snapshots listed with their response hashes. Its outputs go to `outputs/replays/`, every page says 回放结果，不是已批准研究, and no gated command accepts them.

## Directory contract

```text
scopes/<scope_id>/v<k>/candidates*.csv, *.raw.json    resolver output; status lists it apart from the scope
scopes/<scope_id>/v<k>/reviewed.csv                   agent drafts and reviews
scopes/<scope_id>/v<k>/approval-card*.json             scope-validate; no approval effect; never overwritten
scopes/<scope_id>/v<k>/history/, hts-history.json      continuity evidence
scopes/<scope_id>/v<k>/scope.csv, manifest.json        written only by scope-approve
config/studies/<study_id>.toml                         the study
config/policy_events.csv                               dated event markers
outputs/runs/<study_id>/plan-<h12>/                    query_plan.json, acquisition.json
data/raw/dataweb/scopes/<scope_id>/v<k>/               immutable snapshots + .meta.json provenance
outputs/runs/<study_id>/plan-<h12>/analysis-<h12>/     processed/, tables/, charts/, manifests, findings.json, report.md, key_points.md, brief.md, final_report.pdf
```

Nothing is overwritten: a refresh archives the old snapshot under its checksum, and a changed study writes a new `analysis-<h12>` directory.

## Failure handling

| Symptom | Do |
|---|---|
| Setup error from the wrapper | Ask the user, write `config.json` |
| No token | Do not acquire. Use cached outputs if they answer the question; otherwise report the blocker. `acquire --plan` stops before any download |
| Gate refusal: plan, scope CSV or manifest changed, years outside the scope, manifest not written by `scope-approve` | Fix the input, re-run `plan`, or create a new scope version. Never hand-edit `query_plan.json`, `scope.csv`, `manifest.json` or `acquisition.json` |
| `scope-approve` refuses "interactive terminal" | It ran from an agent shell without `--chat-confirmation`: after the user approves the card in chat, pass their words and `--confirm`; or the user runs it in PowerShell or cmd (Git Bash: `winpty`) |
| `scope-approve` says "requires terminal approval" | The project set `allow_chat_confirmation = false`: give the user the command to run in their own terminal |
| `--confirm does not match` | The card changed or the prefix is wrong: show the current card and ask again |
| `hts_current_check.py` exits 1 | Stop, show the findings; a real change means a new scope version |
| DataWeb error or truncated response | Stop acquisition; never analyze a partial response |
| An analysis or chart listed as skipped | Not an error: report the skip reason where that result would be |
| `validate` fails | No deliverable until the failing check is fixed, or scoped out in writing |

## Extending safely

- New product or code set: a new scope version plus a new study, never an edit of an approved scope.
- New years: edit `[periods]`, re-plan, acquire; cached payloads are reused.
- Origins, comparison, windows, events: edit the study only (see [study-design](study-design.md)).
- A new event marker: add a row to `config/policy_events.csv` (`event_date,event_id,event_label,event_type,status,scope,notes`) with its status (implemented, announced_not_imposed, ...).
- A new metric, measure or rule: change the pipeline with a unit test. Report and slide builders only format validated outputs.
