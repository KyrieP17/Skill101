# scopes/

One folder per product definition, one subfolder per version. **A new study = a new scope + a new study file** (study files live in `config/studies/`); `laptops`, `lithium-ion` and `smartphones` are examples, not templates.

```text
scopes/<scope_id>/v<k>/
  candidates*.csv, *.raw.json  candidate output of hts_scope_resolver.py; status lists them separately, not as scope files
  reviewed.csv                 the scope being drafted and reviewed (review_status = candidate or reviewed)
  approval-card*.json          approval card written by scope-validate: approves nothing, never changed once written
  history/, hts-history.json   optional: HTS continuity evidence across editions
  scope.csv                    the approved scope written by scope-approve (with reviewer, approved_at_utc)
  manifest.json                frozen record written by scope-approve: scope_sha256, record_sha256, file hashes, approval method, continuity status
```

Rules:

- The columns are those of the skill's `assets/product_scope_template.csv`; the validation rules are defined in one place, `src/usitc_pipeline/scope.py`.
- Only the user's own approval counts: either they run `scope-approve` in an interactive terminal and type the first 8 characters of `scope_sha256`, or they read the card in chat and reply with approval, after which the agent runs the command with `--chat-confirmation "<the user's exact words>" --confirm <first 8>` (the manifest records the method and the words; the report says "已批准（对话确认）", approved in chat). The agent never writes `approved`, a reviewer, an approval time or a manifest by hand. Approval is a research decision, not a customs classification ruling.
- A scope can be approved without HTS continuity evidence, but only explicitly: the card says "连续性未核实" (continuity not verified), terminal approval requires typing `UNVERIFIED`, chat approval must state it on the card and in the question, and every report shows it afterwards.
- Cards are immutable. If evidence arrives later, write a new card (e.g. `approval-card-2.json`).
- After approval, never edit `scope.csv` or delete `manifest.json` to start over. Any change to codes, dates, units or inclusion goes into `v<k+1>`. `plan` and `acquire --plan` refuse an edited CSV.
- A code change takes several rows: the old code ends on its last day, the new one starts on its first, each with the HTS edition it was reviewed against.
- Snapshots downloaded with a scope go to `data/raw/dataweb/scopes/<scope_id>/v<k>/`, with `scope_sha256` in their metadata.

Which versions are approved: `python run.py status`. `laptops/v1/scope.csv` is an early candidate draft that is no longer used; the laptop study uses `laptops/v2`.
