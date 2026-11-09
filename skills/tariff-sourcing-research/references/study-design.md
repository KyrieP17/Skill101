# Study Design

Read this when writing or extending a study: years, origins, comparison, event windows, and which DataWeb data a study can use.

A scope says which HTS lines are the product; a study says how to look at them. One approved scope can serve several studies. Copy [the study template](../assets/study_template.toml) to `<project>/config/studies/<study_id>.toml`.

## Fields and defaults

Use these defaults unless the user or `decisions.md` says otherwise, and tell the user which defaults you used. Every one can change later without touching the scope.

| Field | Meaning | Default |
|---|---|---|
| `[study] study_id` | lowercase slug; names the run directory | `<scope_id>-<first year>-<last year>` |
| `title` | the question in one line; report and chart titles use it | the user's question |
| `scope_manifest` | project-relative path to the approved `manifest.json` | the latest approved version of the scope |
| `[periods] annual` | `{ start, end }` | the longest window the approved scope covers, ending at the last complete year |
| `[periods] monthly` | `{ start, end }` | only when the question needs event timing or the current year; it also gives the same-period YTD |
| `[origins] focal` | the origin whose loss or gain is studied | `"China"` |
| `highlight` | up to 6 origins with their own color; the rest fold into "Other reported origins" | the user's list; otherwise the largest origins in the last complete year: run `all` with `highlight = []`, fill it, run `all` again (a new analysis directory) |
| `[comparison]` | `base_year`, `compare_year` | the last two complete years |
| `[[event_windows]]` | `id`, `label`, `start`, `end` (`YYYY-MM`) | none, unless the user names a policy date: then one window before it and one or more after |
| `[events] ids` | chart markers from `config/policy_events.csv` | none, unless the user names an event |
| `[quantity] unit` | `"first"`, `"second"` or `"both"`: both keeps the first unit in the main columns, puts the second (e.g. kg) beside it in `_2` columns and adds `quantity_check` | `"both"` when the scope records a second unit; otherwise leave the table out |
| `[timeline] file`, `country` | the scope's tariff timeline CSV and the shared per-origin layer `config/tariff_timelines/_country.csv` | on for a new study; the agent writes only `review_status = "draft"` |
| `[tariff_snapshot]` | non-binding **HTS-based tariff treatment snapshot / HTS法定关税处理快照** for explicit `entry_date`, `hts_release_id`, `hts_release_date`, `origins`, `rate_column`, `preference_claim` and `entry_type`; special-case booleans cover Chapter 98, quota/TRQ, AD/CVD, firm-specific rates and a binding-classification request | off; turn it on only when the question asks which product lines face which treatment, and bind it to the matching archived HTS structure |
| `[product_mix] period` | for an HTS6 scope, plan same-year HTS10 all-origin and independent-World queries from the hash-bound archived structure, then divide each HTS10 World customs value by the separately queried parent HTS6 World value | off; enable only after the user chooses the extra DataWeb download and select a complete annual period whose HTS release sidecar is effective in that year |
| `[[segments]]` | `label`, `codes` (in the scope, each code in one segment) → `segment_series` | none; use it to join HTS10 lines split mid-study |
| `[brief] focal_split` | the focal / other / total table with calculated duty in USD (the brief's key numbers) | `true` |
| `[decomposition] enabled`, `windows` | change decomposition → `share_decomposition`: every origin's share change split into its own value and all other origins, its value change split into quantity and unit value, and the timeline rows dated in the window; for consecutive full years, consecutive months, the YTD comparison and each `windows` month pair (`[["YYYY-MM", "YYYY-MM"], ...]`) | `enabled = true` for a new study; add `windows` after the first run, from `window_candidates` (or the turns of chart 03's focal share path) |
| `[decomposition] mix` | splits the unit-value part into price within each code (`value_change_from_price_usd`) and the shift between codes (`value_change_from_mix_usd`); a code on one side only keeps its own price; both blank when they do not add up | `true` when the scope has two or more codes |
| `[[derived]]` | numbers the brief needs that no table holds (a quarter, January–August, a monthly mean, the lowest month) → `calculations`: `id`, `label`, `origin` (an origin, `World`, `Other origins` = World minus focal, `Other reported origins`), `measure` (`value`, `quantity`, `duty`, `share`, `quantity_share`, `unit_value`, `realized_duty`, and `_2` second-unit forms), `stat` (`total` default, `mean`, `min`, `max`), `window` and optional `base` (`"YYYY-MM"`, `"YYYY"` or `[first, last]`, same kind); each row has its formula and the sums it used | none; add one for every number in the brief that is not already in a table |
| `[anomalies] enabled` | rule flags → `anomaly_flags`: new or near-zero origins, realized duty below the HTS8 base rate or below half of base + a verified `coverage=applies` timeline rate, unit value ≥1.5× (or ≤1/1.5) the scope's, a surge before an applicable timeline date, count against weight; each row has the numbers, the threshold and a question template; thresholds are constants in the pipeline's `flags` module | `true` for a new study |
| `[brief] candidates` | `mention_list` (what the brief must mention: origins whose share moved ≥2 pp or contributed ≥2 pp, the top 3 by value, a ≥1 pp realized-duty change for the focal origin, timeline rows without a verified primary source, and unverified continuity) and `window_candidates` (turning points of the focal monthly share path; one-month spikes and short moves that recur in the same calendar month in two other years are listed but not suggested) | `true` for a new study |
| `[drilldown] structure` | the HTS REST search for the scope's HTS6, saved unchanged in the scope's `history/` by `hts_scope_resolver.py --keyword <HTS6 with dot, e.g. 8517.13> --structure-output scopes/<id>/v<k>/history/hts_rest_search_<code>.json`, which prints URL, bytes and SHA-256 for a row in `history/SOURCES.md` → `drilldown_menu` | on for a first (HTS6) study |
| `[drilldown] release_metadata` | JSON sidecar with `schema_version`, `release_id`, `effective_date`, `structure_file`, the approved acquisition `source_url`, and the raw structure's `source_sha256` | required with `[tariff_snapshot]`; the sidecar hash also enters `study_sha256` |
| `[industry_context] mode` | `"off"` or `"light"`; light adds a separately labeled optional web-research item to `next_steps`, but does not browse or treat external context as DataWeb evidence | `"off"`; use `"light"` only when the user opts into industry-cycle context |
| `[context] hts4`, `hts6` | HTS4 map, removed from the brief (decisions.md D-025): the pipeline option is left in place, off, and never fetched by `acquire` | leave it out; do not turn it on unless the user asks again |

Highlights change only the display. Every origin stays in `origin_series.csv`, `origin_change.csv`, the shares and the HHI.

The loader refuses: unknown tables; non-slug ids; an absolute or outside-project `scope_manifest`; no period at all; years before 1989 or in the future; a highlight that repeats the focal origin, has duplicates or more than 6 names; `World` as an origin (it is always a separate query); only one comparison year, `base_year >= compare_year`, or years outside `annual`; event windows without `monthly`, outside it, with `start > end` or repeated ids; event ids missing from `policy_events.csv`; an enabled `[tariff_snapshot]` without `[drilldown] structure and release_metadata, explicit nonfuture `entry_date`, `hts_release_id` and `hts_release_date` (release date no later than entry date), or at least one non-World origin; an `[industry_context] mode` other than `off` or `light`; decomposition `windows` without `enabled = true` or `monthly`, outside it, not `YYYY-MM`, with the first month not before the second, or repeated; `mix` without `enabled = true`; a `[[derived]]` entry with unknown keys, a non-slug or repeated `id`, no `measure` or `window`, an unknown measure or stat, a `_2` measure without `unit = "both"`, months mixed with years, a window or base outside `[periods]`, or a base of the other kind. `analyze` refuses a `[[derived]]` origin that is not in the data (with "did you mean").

Timeline CSV columns: `date`, `date_type` (`announced` / `effective`), `measure`, `rate_change`, `applies_to` (HTS8/HTS10 codes separated by `;`, blank = whole scope), `origins`, `source`, `source_url`, `review_status` (`draft` / `user_checked`), `notes`. Primary sources are hts.usitc.gov, federalregister.gov, ustr.gov and cbp.gov; any other row is shown as 待核对. The per-origin layer holds candidate country measures and needs `origins` with no codes. During `analyze`, every candidate row is checked against the registry for the study's codes: only a verified applicable match becomes `coverage=applies` and reaches the product timeline, tariff charts, decomposition and anomaly rules. MFN base-rate rows remain as `coverage=base_rate`; exemptions, not-covered, suspended, unverified and missing-coverage rows fail closed and stay out of those outputs. A drill-down verdict uses only these product-applicable rows and their rates (decisions.md D-010): "和上一层相同，不用下钻", "税率或附加税在这一层分开，建议下钻" (different HTS8 base rates, or an applicable timeline row covering only some lines at that level), or "只有统计拆分，按需".

The measures themselves live in the project's tariff registry, `config/tariff_registry/` (decisions.md D-026, D-029): `sources.csv` (Federal Register and HTS archive sources, URL and SHA-256), `measures.csv` (one row per dated legal change), `coverage.csv` (per measure and HTS6/8/10 or generic coverage: covered / exempt / not_covered / suspended, with evidence), and `treatment_rules.csv` (machine-readable `add` / `replace` / `exempt` / `end`, published rate text and type, `stack_group`, explicit `supersedes`, effective dates and source ids). Never parse a free-text change such as `+25% -> +50%` into lifecycle arithmetic. `covered` is normalized to `applies` only after the measure, lifecycle rule and most-specific coverage are verified; an exact HTS10 exclusion overrides a generic apply row, and absence of coverage never means applicable. Before writing or extending a timeline, run `registry-check --study <study>.toml`. It reports each row as `match`, `date_differs` or `not_in_registry`, plus missing and excluded measures; it never edits a timeline. The analysis manifest binds the exact registry and HTS-structure inputs by SHA-256; changing them requires a new `analyze` run but no new DataWeb acquisition.

## HTS-based tariff treatment snapshot

Call this the **HTS-based tariff treatment snapshot / HTS法定关税处理快照**, not a "statutory-rate calculator". The minimum identity is `origin × HTS8/HTS10 × entry_date × HTS release × rate_column/preference_claim × entry_type`. The explicit `hts_release_id` and `hts_release_date` must match the machine-readable release sidecar, whose `source_sha256` must equal the exact structure file; every approved scope line active on `entry_date` must name that one release. The sidecar records acquisition provenance and binds bytes to the approved scope—it does not make its search URL independent proof of a release. The release date must be no later than `entry_date`; a historical entry date requires the corresponding archived HTS structure and cannot be backcast from today's structure. The HTS8 line supplies the legal Column 1-General formula; every HTS10 statistical child is resolved separately against verified Chapter 99 coverage and lifecycle rules. Preserve published ad-valorem, specific and compound formulas as text; do not coerce `$0.05/kg + 3%` into a percentage and do not infer that two active rows stack unless their `stack_group` rules allow it.

V1 covers Column 1-General plus verified Chapter 99 layers for ordinary consumption entries. The result status is `Resolved` (one supported treatment), `Mixed` (verified child lines differ), `Conditional` (preference claim, Chapter 98, quota/TRQ, warehouse/FTZ or special entry, AD/CVD, firm-specific rate or binding-classification request needs separate facts) or `Unresolved` (missing/conflicting/unverified base rate, lifecycle, source or coverage). It is a non-binding research snapshot, never a CBP ruling or entry liquidation. A policy name such as "IEEPA China-wide additional duty — synthetic-opioid policy" identifies the legal authority; it does **not** say that the researched chips, boards or other goods contain or relate to fentanyl.

Example:

```toml
[tariff_snapshot]
entry_date = "2026-09-30"       # explicit as-of date; never silently use today
hts_release_id = "2026HTSRev19" # exact release id in the release sidecar
hts_release_date = "2026-08-01" # archived HTS structure in force by entry_date
origins = ["China"]             # defaults to the focal origin when omitted
rate_column = "general"         # V1 resolves Column 1-General
preference_claim = "none"
entry_type = "consumption"
# chapter_98 = false
# quota_or_trq = false
# ad_cvd = false
# firm_specific_rate = false
# requires_binding_classification = false
```

## Product hierarchy, proxies and follow-ups

- Keep `HTS6 industry scope → HTS8 legal rate line → HTS10 statistical product category`. Do not omit HTS8 even when one HTS8 contains every child. `tables/product_hierarchy_matrix.csv` lists every child, description, quantity unit, HTS8 Column 1-General formula, snapshot status/formula, classification release, continuity status and trade-data status.
- An HTS10 product-mix share exists only as `same-period HTS10 independent-World consumption-import customs value / independently queried parent HTS6 World customs value`. It is a U.S. import customs-value composition proxy, not market share, company share, value added or global capacity. An HTS6 cache alone cannot produce it: leave the share blank with an `unavailable_*` status and let `tables/next_steps.csv` request a new HTS10 DataWeb download. After approval, add `[product_mix] period = YYYY`; the plan keeps those two HTS10 jobs separate from ordinary scope jobs, binds their raw snapshots in the manifest, and refuses a structure sidecar from another year or HTS release. An archived HTS subset must preserve the official row order and blank-code `superior` headings, because those headings supply product families such as DRAM; if a heading is missing, the matrix keeps only the official leaf description and never guesses the family. In `product_hierarchy_matrix`, `hts10_description` is always the official English leaf; `hts10_category_group_en` is the nearest official superior heading or, for a direct leaf, that leaf as a singleton group; `hts10_display_label_en` is a derived compact reader label, never the canonical description. A single HTS8 can collapse visually into horizontal bars; several HTS8 lines stay grouped.
- `tables/proxy_notes.csv` accompanies every proxy with its source dataset/fields, formula, denominator, unit, what it represents, what it does not represent, data period, classification release and quality warning. At minimum this covers unit value, realized duty, reported origin and—when measured—the HTS10 import-value share.
- `tables/next_steps.csv` is deterministic and has at most three rows. It prioritizes an unresolved/conditional/mixed tariff snapshot, then missing HTS10 product mix, a realized-duty gap, unit-value/quantity anomalies and unverified HTS continuity. `industry_context.mode = "light"` may add a separately labeled optional web-research item; `off` never proposes one. The menu describes authorization/data needs and never performs a download or web search.

## What needs a new plan or download

```text
plan_sha256  = approved scope_sha256 + [periods] + study id and path
study_sha256 = everything in the study file, plus the policy-event rows it names
```

- Origins, comparison, windows, events or title: only `study_sha256` changes. Re-run `analyze`, `charts`, `report`; nothing is downloaded.
- Periods or a new study file: re-run `plan`, then `acquire --plan`. Jobs whose payload hash matches a cached snapshot are reused; when every job is cached no token is needed.
- New or different codes: a new scope version, never an edit.
- `[timeline]`, `[tariff_snapshot]`, `[[segments]]`, `[brief]`, `[drilldown]`, `[industry_context]`, `[decomposition]`, `[[derived]]`, `[anomalies]`: only `study_sha256` changes (it includes the timeline rows, explicit entry/release context, structure-file hash and release-sidecar hash). `[quantity] unit = "both"` sends the same payloads as `"second"`, so it reuses that cache.
- A tariff-registry change does not change the DataWeb plan. Re-run `analyze`, `charts` and the report; validation refuses an analysis bound to older registry hashes.
- Measuring HTS10 product shares from an HTS6 study is different: it needs new same-period HTS10 all-origin and independent-World DataWeb queries. Until they exist, `product_hierarchy_matrix` keeps the structure and tariff treatments but marks shares unavailable.
## Coverage dates

- `query_through` is what the plan asked for (`annual` end year, `monthly` December of its end year).
- `data_through` is the last period with a non-zero value in the independent World query. A requested but empty month is not coverage.
- YTD is automatic: with `data_through.monthly = Y-MM` and `Y-1` inside `periods.monthly`, months 01..MM of `Y-1` are compared with the same months of `Y`.
- DataWeb labels the current year's year-to-date sum as a plain year. The last annual period counts as a full year only if monthly data reach its December, or it was retrieved on or after 1 March of the next year. Otherwise it is `ytd_through_MM` or `partial_year_unverified`, and the annual comparison is skipped in favour of YTD.

## Analyses, tables and charts

Each analysis writes a table under `tables/` and one chart under `charts/` with a same-stem CSV of exactly what is plotted. Skips are written to `analysis_manifest.json` and `chart_manifest.json`, never silent.

| Analysis | Table | Chart | Skipped when |
|---|---|---|---|
| Scope totals: value, quantity, focal share | `scope_series` | `01_scope_trend` | never |
| Change by HTS code within the scope | `product_reallocation` | `02_product_reallocation` | no comparison, or fewer than two included codes |
| Monthly path, event windows and markers | `monthly_panel`, `event_metrics.json` | `03_event_path` | no `periods.monthly` |
| Origin shares (focal, highlights, Other) | `display_series` | `04_origin_shares` | never |
| Every origin: value, share and quantity change, inflow or outflow, contribution in pp | `origin_change` (`annual_*`) | `05_origin_change` | compare years not both full years |
| Same-period YTD, same columns | `origin_change` (`ytd_*`) | `06_ytd_comparison` | no monthly data or no prior year |
| Concentration: HHI, top origin, top-3 share | `scope_series` | `07_concentration` | never |
| Unit value | `display_series`, `scope_series` | `08_unit_value` | quantity not comparable |
| Realized duty (calculated duty / customs value) | `display_series`, `scope_series` | `09_realized_duty` | never |
| Origins summed against the World query | `world_check` | `10_world_check` | never |
| Product-applicable tariff timeline (`coverage=applies`, plus the separate MFN base rate), numbered lines on 03 and 09 (effective dark solid, announced light dashed) | `tariff_timeline` | `03_event_path.timeline.csv`, `09_realized_duty.timeline.csv` | no `[timeline]` |
| Focal / other / total with calculated duty | `focal_split` | none | no `[brief] focal_split` |
| Share change from the origin's own value vs all other origins; value change from quantity vs unit value (and price vs code mix with `mix`); timeline rows dated in each window (consecutive years and months, YTD, named month pairs) | `share_decomposition` | none | no `[decomposition] enabled` |
| Numbers the brief declares, with formula and sums | `calculations` | none | no `[[derived]]` |
| Rule flags for the brief's questions (section 4) | `anomaly_flags` | none | no `[anomalies] enabled`; a rule whose input is missing (timeline, drill-down menu, monthly data, second unit) is skipped under `anomaly_rules` in the manifest |
| Must-mention list; candidate decomposition windows | `mention_list`, `window_candidates` | none | no `[brief] candidates`; candidates also need `monthly` |
| Segments side by side (full years and YTD) | `segment_series` | none | no `[[segments]]` |
| First unit against second unit; flags opposite signs both ≥5% | `quantity_check` | none | unit is not `both` |
| Drill-down menu for HTS8 and HTS10 | `drilldown_menu` | none | no `[drilldown]` |
| HTS-based tariff treatment snapshot by origin and HTS child, with base formula, verified Chapter 99 layers, exclusions, status and unresolved/conditional reasons | `tariff_treatment_snapshot` | none | no `[tariff_snapshot]`; missing evidence is written as `Unresolved`, not skipped |
| HTS6 → HTS8 → HTS10 structure, tariff treatment and import composition | `product_hierarchy_matrix` | `12_product_mix` | structure absent; chart also needs measured same-period HTS10 values and the independent HTS6 World denominator |
| Provenance and limits for every reported proxy | `proxy_notes` | none | never; rows appear only for proxies the study uses |
| Deterministic follow-up menu, at most three actions | `next_steps` | none | never; a resolved study with no open item gets an explicit `no_action` row |

`origin_series` (every origin, every period) and `coverage_audit` (periods expected, observed, missing) back all of them. Quantity, quantity share and unit value are blank for a product-period whose units are not comparable; value results continue.

## DataWeb data

What every plan requests (fixed in the pipeline's `dataweb` module):

- **Flow**: U.S. imports for consumption (`CONS_*`), not general imports.
- **Measures**: `CONS_CUSTOMS_VALUE` (customs value, USD), `CONS_FIR_UNIT_QUANT` (first unit of quantity, in the schedule's unit), `CONS_CALC_DUTY` (calculated duties, USD).
- **Dimensions**: all reported origins broken out, plus an independent World query; the scope's codes at its HTS level, broken out; annual (up to 10 years and one code set per query) or monthly (one year per query); districts, import programs and rate provision codes aggregated.

**Second unit of quantity (opt-in per study).** A study with

```toml
[quantity]
unit = "second"
```

also requests `CONS_SEC_UNIT_QUANT` (DataWeb table "Second Unit of Quantity", e.g. "kilograms"; confirmed by a probe 2026-11-06), and quantity, quantity share and unit value then use that unit instead of the first. `plan` refuses it unless the scope records one `second_unit` for every included line. The payloads differ, so it is a new download; studies without the table keep their plans, caches and hashes. `processed/trade_tidy.csv` keeps both units (`quantity` / `quantity_2`). Use it when the first unit counts unlike things (a "No." that is a cell for one shipment and a whole pack for the next); keep the first-unit study next to it for comparison.

Also in DataWeb, not requested. Each needs a pipeline change with a test, so ask the user first:

- HTS10 product-mix queries for an HTS6 study: same-period all-origin and independent World rows, reconciled to the existing independent HTS6 World denominator;
- dutiable value, import charges, CIF value, landed duty-paid value: duty-base, freight and insurance questions;
- general imports (`GEN_*`): includes goods entering bonded warehouses and foreign-trade zones;
- breakouts by district, import program (claimed preference such as USMCA) or rate provision code.

Of the codes, `CONS_CUSTOMS_VALUE` and `CONS_FIR_UNIT_QUANT` are in USITC's API guide and `CONS_CALC_DUTY` is proven by the project's own snapshots (checked 2026-11-06). Confirm any other measure code against a DataWeb saved query before relying on it.

Facts that change interpretation (DataWeb FAQ, checked 2026-11-06):

- Census revises the prior three calendar years every June, so a refresh after June can change old numbers. A plan can combine snapshots downloaded at different times; `acquisition.json` lists each one.
- Monthly data reach DataWeb about three business days after the Census release.
- Country means the country of origin reported to CBP: not the shipping country, not value added. Transshipment is not identified.
- Census withholds some quantities without marking them, which is why DataWeb dropped its own unit values. Treat unit value as Derived; a sharp move may be an artifact.
