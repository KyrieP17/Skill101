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
| `[[segments]]` | `label`, `codes` (in the scope, each code in one segment) → `segment_series` | none; use it to join HTS10 lines split mid-study |
| `[brief] focal_split` | the focal / other / total table with calculated duty in USD (the brief's key numbers) | `true` |
| `[drilldown] structure` | the HTS REST search for the scope's HTS6, saved unchanged in the scope's `history/` by `hts_scope_resolver.py --keyword <HTS6 with dot, e.g. 8517.13> --structure-output scopes/<id>/v<k>/history/hts_rest_search_<code>.json`, which prints URL, bytes and SHA-256 for a row in `history/SOURCES.md` → `drilldown_menu` | on for a first (HTS6) study |
| `[context] hts4`, `hts6` | HTS4 map: extra queries outside the scope, each heading broken out by HTS6 (`hts6` lists every line of the headings), value and calculated duty only | off; the user approves these queries separately |

Highlights change only the display. Every origin stays in `origin_series.csv`, `origin_change.csv`, the shares and the HHI.

The loader refuses: unknown tables; non-slug ids; an absolute or outside-project `scope_manifest`; no period at all; years before 1989 or in the future; a highlight that repeats the focal origin, has duplicates or more than 6 names; `World` as an origin (it is always a separate query); only one comparison year, `base_year >= compare_year`, or years outside `annual`; event windows without `monthly`, outside it, with `start > end` or repeated ids; event ids missing from `policy_events.csv`.

Timeline CSV columns: `date`, `date_type` (`announced` / `effective`), `measure`, `rate_change`, `applies_to` (HTS8/HTS10 codes separated by `;`, blank = whole scope), `origins`, `source`, `source_url`, `review_status` (`draft` / `user_checked`), `notes`. Primary sources are hts.usitc.gov, federalregister.gov, ustr.gov and cbp.gov; any other row is shown as 待核对. The per-origin layer needs `origins` and no codes, and it applies to every study that names it: it cannot leave out a product's exemption from a country-wide measure, so write the exemption as a row in the product timeline (with its source), where the report shows it beside the country rows. A drill-down verdict uses only the product and its rates (decisions.md D-010): "和上一层相同，不用下钻", "税率或附加税在这一层分开，建议下钻" (different HTS8 base rates, or a timeline row covering only some lines at that level), or "只有统计拆分，按需".

## What needs a new plan or download

```text
plan_sha256  = approved scope_sha256 + [periods] + study id and path
study_sha256 = everything in the study file, plus the policy-event rows it names
```

- Origins, comparison, windows, events or title: only `study_sha256` changes. Re-run `analyze`, `charts`, `report`; nothing is downloaded.
- Periods or a new study file: re-run `plan`, then `acquire --plan`. Jobs whose payload hash matches a cached snapshot are reused; when every job is cached no token is needed.
- New or different codes: a new scope version, never an edit.
- `[timeline]`, `[[segments]]`, `[brief]`, `[drilldown]`: only `study_sha256` changes (it includes the timeline rows and the structure file's hash). `[quantity] unit = "both"` sends the same payloads as `"second"`, so it reuses that cache.
- `[context] hts4` adds a `context` section to the plan with `needs_user_approval`; `acquire` never fetches it, and the report says "HTS4 地图：未取数" until the user approves and the snapshots are cached.

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
| Tariff timeline, numbered lines on 03 and 09 (effective dark solid, announced light dashed) | `tariff_timeline` | `03_event_path.timeline.csv`, `09_realized_duty.timeline.csv` | no `[timeline]` |
| Focal / other / total with calculated duty | `focal_split` | none | no `[brief] focal_split` |
| Segments side by side (full years and YTD) | `segment_series` | none | no `[[segments]]` |
| First unit against second unit; flags opposite signs both ≥5% | `quantity_check` | none | unit is not `both` |
| Drill-down menu for HTS8 and HTS10 | `drilldown_menu` | none | no `[drilldown]` |
| HTS4 map: each HTS6 of the heading, value, YoY, focal share, realized duty, share of heading | `heading_map` | `11_heading_map` | no `[context] hts4`, or its queries not fetched |

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

- dutiable value, import charges, CIF value, landed duty-paid value: duty-base, freight and insurance questions;
- general imports (`GEN_*`): includes goods entering bonded warehouses and foreign-trade zones;
- breakouts by district, import program (claimed preference such as USMCA) or rate provision code.

Of the codes, `CONS_CUSTOMS_VALUE` and `CONS_FIR_UNIT_QUANT` are in USITC's API guide and `CONS_CALC_DUTY` is proven by the project's own snapshots (checked 2026-11-06). Confirm any other measure code against a DataWeb saved query before relying on it.

Facts that change interpretation (DataWeb FAQ, checked 2026-11-06):

- Census revises the prior three calendar years every June, so a refresh after June can change old numbers. A plan can combine snapshots downloaded at different times; `acquisition.json` lists each one.
- Monthly data reach DataWeb about three business days after the Census release.
- Country means the country of origin reported to CBP: not the shipping country, not value added. Transshipment is not identified.
- Census withholds some quantities without marking them, which is why DataWeb dropped its own unit values. Treat unit value as Derived; a sharp move may be an artifact.
