# Deliverables

Read this when the user asks for a report, deck, meeting brief or speaking notes.

## What a study run delivers

`all --study <study>.toml` fills one analysis directory (`outputs/runs/<study_id>/plan-<h12>/analysis-<h12>/`):

1. **Dataset**: `processed/trade_panel.csv` (the validated panel; `trade_tidy.csv` beside it), result tables in `tables/`, and `analysis_manifest.json` with the identity hashes, `data_through`, skip reasons and the SHA-256 of every output.
2. **Report**: `report.md`, with an identity table (scope and version, `scope_sha256`, `plan_sha256`, `study_sha256`, approval, HTS continuity 已核实/未核实, `data_through`), every chart, and text in which each conclusion carries its evidence label and source table. Charts are `charts/<stem>.png`, each with `charts/<stem>.csv` holding exactly what is plotted, listed in `chart_manifest.json`.
3. **Key points**: `key_points.md`, 5–8 points, each with its label and source. `findings.json` holds every finding behind both.
4. **Final report** (what the user receives): `final_report.pdf`, from `report-pdf --study <study>.toml` once `brief.md` is written. The body is `brief.md` as written; the appendix is generated from validated files: A. study identity with full hashes, B. data checks (panel validation, origins against World, code coverage), C. what the data cannot show, D. every chart the body does not embed, with its data CSV, E. files with SHA-256. `final_report.html` is the same page; `final_report.json` records the input hashes. Re-running replaces only these three files. The PDF is printed by local Edge or Chrome (`--browser` or `USITC_PDF_BROWSER` to choose one).

## Building anything else

- Build from those files only. If a number or chart is missing, extend the pipeline first ([operations](operations.md)); never calculate in Word, PowerPoint, Slides or a JavaScript builder.
- Keep each claim's evidence label and source table. Do not add explanations the findings do not state: write the observation as a question for the reader (D-019).

## Two-page macro brief

The default first deliverable for a new product. Write `brief.md` in the same analysis directory after `report.md`: about two printed pages, in whatever language you judge best for the reader (usually the user's; the appendix follows it, Chinese or English, `--lang` overrides). The reader is an analyst who wants a general data pack and makes their own insight (decisions.md D-019): give data, tariff background and anomalies, never reasons. Then run `report-pdf`: the brief is only the body of `final_report.pdf`, so start it with one `# Title` and embed charts with Markdown image syntax pointing at `charts/<stem>.png`; only charts in `chart_manifest.json` are accepted.

Sections, in this order:

1. **Answer and key numbers**: one sentence answering the study question, then the key numbers, latest same-period YTD first, then the last full-year comparison. Use `tables/focal_split.csv` (focal origin, other origins, total: value, share, calculated duty in USD, realized duty; weight when the study has the second unit).
2. **Tariff timeline**: the table from `tables/tariff_timeline.csv` (or the report's timeline section), and how many rows are 未经用户核对 or 待核对. No timeline in the study: say so in one line.
3. **HTS4 map**: `tables/heading_map.csv` and chart `11_heading_map` when the study has `[context] hts4 = true` and the data were fetched; otherwise one line: "HTS4 地图：未取数".
4. **Body**: the scope total at the scope's level (HTS6 for a first study), with segments side by side when the study has `[[segments]]` (`tables/segment_series.csv`). Charts `03` (focal share path with timeline lines) and `09` (realized duty with timeline lines), each with a one-line takeaway; add `05`/`06` only if space allows.
5. **Questions worth asking**: observations only, each with its table, no explanation. For example: an origin that is new or grew from almost nothing; realized duty below the base rate (or far below base rate plus additional duties in the timeline); a unit value far from the other origins; count and weight moving in opposite directions (`quantity_conflict` findings); an import surge in the months before a timeline date. Write the observation as a question for the reader, never "可能是……".
6. **Drill-down menu**: from `tables/drilldown_menu.csv` when the study has one: for each finer level, its lines, the HTS8 base rate, whether a chapter 99 duty covers only some lines, splits in the study period, and the verdict (same as the level above / rates or additional duties split here, drill down / statistical split only). Also the other directions: a period, a gainer as focal origin, the second unit.
7. **Notes on measures**: the footnotes below that apply.

A drill-down brief (a finer scope or a second-unit study) stands alone: state its own numbers in full, and do not cite or correct an earlier brief. Keep the evidence label and source table on every number. At two pages, cut a chart before cutting the notes.

## Default narrative

Lead with charts, not a tour of the database. Use only what the request needs:

1. The question and the scope: what the HTS lines hold and what they cannot separate (`01`).
2. Who supplies the product and how concentrated it is (`04`, `07`).
3. Where the change went: every origin's inflow and outflow, annual and same-period YTD (`05`, `06`).
4. Which codes inside the scope moved (`02`).
5. Timing around policy dates (`03`).
6. Unit value where quantity is comparable, and realized duty (`08`, `09`).
7. Alternatives checked, what stays unknown, and the data check (`10`).

## Writing style

- Lead with the result visible in the chart.
- Short, spoken sentences for presentations.
- Percentage points for share changes, percent for growth.
- Taiwan is an origin or area; no aside needed.
- Technical caveats go in a compact source note unless they change the finding.
- No essay transitions, no "one important rule" filler, no causal verbs the evidence does not support.

## Footnotes

Include only those relevant to the page:

- USITC DataWeb, U.S. imports for consumption, `data_through`, and that Census revises the last three years each June;
- quantity unit and YTD window;
- realized duty, not the statutory rate;
- country of origin, not value added;
- 连续性未核实, when the scope says so;
- descriptive, not causal;
- 回放结果，不是已批准研究, for a replay.

## QA

- Every plotted value is in the same-stem chart CSV or a named table.
- Titles and bullets agree with the latest validated output.
- Check page or slide count and legibility separately from content.
- Do not say a report or deck is updated until the exported file has been opened or rendered and looked at. For `final_report.pdf`, render its pages to images if the viewer cannot show PDFs, and check that tables, charts and the appendix are legible.
