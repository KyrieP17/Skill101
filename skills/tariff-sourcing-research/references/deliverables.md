# Deliverables

Read this when the user asks for a report, deck, meeting brief or speaking notes.

## What a study run delivers

`all --study <study>.toml` fills one analysis directory (`outputs/runs/<study_id>/plan-<h12>/analysis-<h12>/`):

1. **Dataset**: `processed/trade_panel.csv` (the validated panel; `trade_tidy.csv` beside it), result tables in `tables/`, and `analysis_manifest.json` with the identity hashes, `data_through`, skip reasons and the SHA-256 of every output. New-framework studies also expose `tables/tariff_treatment_snapshot.csv` when configured, `tables/product_hierarchy_matrix.csv` with unavailable product shares left blank rather than inferred, `tables/proxy_notes.csv`, and the at-most-three-row `tables/next_steps.csv`.
2. **Report**: `report.md`, with an identity table (scope and version, `scope_sha256`, `plan_sha256`, `study_sha256`, approval, HTS continuity 已核实/未核实, `data_through`), every chart, and text in which each conclusion carries its evidence label and source table. Charts are `charts/<stem>.png`, each with `charts/<stem>.csv` holding exactly what is plotted, listed in `chart_manifest.json`.
3. **Key points**: `key_points.md`, 5–8 points, each with its label and source. `findings.json` holds every finding behind both.
4. **Final report** (what the user receives): `final_report.pdf`, from `report-pdf --study <study>.toml` once `brief.md` is written. The body is `brief.md` as written; the appendix is generated from validated files: A. study identity with full hashes, B. data checks (panel validation, origins against World, code coverage), C. what the data cannot show, D. every chart the body does not embed, with its data CSV, E. files with SHA-256. `final_report.html` is the same page; `final_report.json` records the input hashes. Re-running replaces only these three files. The PDF is printed by local Edge or Chrome (`--browser` or `USITC_PDF_BROWSER` to choose one).

## Building anything else

- Build from those files only. If a number or chart is missing, extend the pipeline first ([operations](operations.md)); never calculate in Word, PowerPoint, Slides or a JavaScript builder.
- Keep each claim's evidence label and source table. Do not add explanations the findings do not state: write the observation as a question for the reader (D-019).

## Two-page macro brief

The default first deliverable for a new product. Write `brief.md` in the same analysis directory after `report.md`: about two printed pages, in whatever language you judge best for the reader (usually the user's; the appendix follows it, Chinese or English, `--lang` overrides). The reader is an analyst (decisions.md D-019, D-023): give the data and tariff background, and lead with what the data shows (key findings), but never a business or policy cause the data and primary sources do not support. Then run `report-pdf`: the brief is only the body of `final_report.pdf`, so start it with one `# Title` and embed charts with Markdown image syntax pointing at `charts/<stem>.png`; only charts in `chart_manifest.json` are accepted.

Every number comes from a validated table (D-024). A number no table holds (a quarter's total, January–August, a monthly mean, the other origins together, the lowest month) is declared as a `[[derived]]` entry in the study ([study-design](study-design.md)) and read from `tables/calculations.csv` after `analyze`; never add, average or divide in chat. Then run `brief-check --study <study>.toml` and fix every error before `report-pdf`, which runs the same check and refuses a brief that fails it. It checks:

- one `# Title` and the six `##` sections below, in order (headings containing 发现 / 时间线 / 主体 / 问题 / 下钻 / 口径, or finding / timeline / body / question / drill / notes);
- every paragraph, list item or table with a number in a unit (%, 个百分点, 亿美元, 美元/部, 倍 ...) has an evidence label (`**[Measured]**`, `**[Derived]**`, `**[Inference]**`, `**[Unknown]**`) and a source in backticks (`tables/origin_change.csv`) that is a validated output; a table takes both from the line right after it;
- each such number, rounded to the digits shown, is in a file the block cites (a % may come from a fraction column; 亿美元 is ×1e8);
- English monetary and ratio forms such as `$519 per unit`, `USD 58.9 bn`, `58.9 USD bn`, and `4.4x` are parsed. The check summary reports all numeric tokens as `parsed`, `ambiguous`, or `skipped`; an adjacent unknown unit is an error, never a silent skip;
- a hash is a prefix of this study's hashes; embedded images are validated charts;
- no hedged cause ("可能是", "probably") anywhere, and causal verbs ("因为", "导致", "due to" ...) only inside a question ending in ？ or ?;
- with `[brief] candidates`, every item of `tables/mention_list.csv` is named.

Sections, in this order:

1. **Key findings, then key numbers**: one sentence answering the study question, then 4–6 numbered findings: what you found while working through the tables, not a list of numbers. Each is one bold sentence stating a conclusion, then the 2–4 numbers that prove it, with label and source. Look for:
   - substitution or shrinkage: did other origins make up the focal origin's loss? (`share_decomposition`, annual and YTD rows);
   - sequence: who moved first, when, and whether a gap opened (`custom` windows, chosen from `tables/window_candidates.csv`, and the monthly path in chart 03's CSV);
   - seasonality: a month that repeats every year and whether it grew; whether including it changes the YTD answer;
   - persistence: whether shares came back once realized duty fell back;
   - price against volume: which origins grew by quantity and which by unit value (`value_change_from_quantity_usd`, `value_change_from_unit_value_usd`). Unit value includes the product mix inside the codes, so call it 单价（含产品结构）, not price; with `[decomposition] mix` and two or more codes, say how much of the unit-value part is price within codes and how much is the shift between codes (`value_change_from_price_usd`, `value_change_from_mix_usd`);
   - realized duty against the timeline: which rows the data agrees with and which it does not, saying when the rows are still 待核对.
   A finding states what the numbers show; a cause from outside the data (a company, a plant, a trade deal) goes into section 4 as a question unless a primary source says so. With `[brief] candidates`, cover every item of `tables/mention_list.csv` (an item a finding does not use goes into the key numbers or the notes). Then the key numbers: latest same-period YTD by origin, then `tables/focal_split.csv` (focal origin, other origins, total: value, share, calculated duty in USD, realized duty; weight when the study has the second unit).
2. **Tariff timeline and treatment snapshot**: show only the product-applicable rows from `tables/tariff_timeline.csv`, with the rate change, HTSUS/Executive Order/Federal Register identifier and primary-source link. Do not expose the internal `review_status` or say that rows are unchecked by the user; only genuinely unverified source facts are marked 待核对. If `[tariff_snapshot]` is present, add the non-binding **HTS-based tariff treatment snapshot / HTS法定关税处理快照** from `tables/tariff_treatment_snapshot.csv`: explicit origin, HTS child, entry date, HTS release date, rate column/preference/entry assumptions, base formula, verified Chapter 99 layers and `Resolved / Mixed / Conditional / Unresolved`. Never call it a rate calculator. No timeline or snapshot: say so in one line.
3. **Body**: the scope total at the scope's level (HTS6 for a first study), with segments side by side when the study has `[[segments]]` (`tables/segment_series.csv`). Charts `03` (focal share path with timeline lines) and `09` (realized duty with timeline lines), each with a one-line takeaway; add `05`/`06` only if space allows. Show `tables/product_hierarchy_matrix.csv` as `HTS6 industry scope → HTS8 legal rate line → HTS10 statistical category`; use chart `12_product_mix` only when same-period HTS10 World values and the independent HTS6 World denominator make every shown share measurable. An unavailable share stays blank and becomes a next step. When the study has `[decomposition]`, put under chart 03 a table of its turns from `tables/share_decomposition.csv` (the `custom` windows, then the last full year and the YTD): the focal origin's share change, the part from its own value and the part from all other origins, its value change split into quantity and unit value, and the timeline rows dated in the window. Write it as arithmetic ("份额 −44.8 个百分点：−44.4 来自 China 自身金额，−0.4 来自其他来源地"); a timeline row is a date inside the window, never the cause.
4. **Questions worth asking**: what the findings leave open and this data cannot answer, each with its table and, when known, what other data would answer it (a product split HTS cannot make, a rate row to verify). For example: an origin that is new or grew from almost nothing; realized duty below the base rate (or far below base rate plus additional duties in the timeline); a unit value far from the other origins; count and weight moving in opposite directions (`quantity_conflict` findings); an import surge in the months before a timeline date. With `[anomalies] enabled`, start from `tables/anomaly_flags.csv`: each row has the rule, the numbers, the threshold it crossed and a question template; use the flags that matter, cite the table, and leave out the rest. A flag is an observation, not a finding and not a cause. Do not repeat a finding as a question; write it as a question, never "可能是……".
5. **Drill-down and next-step menu**: from `tables/drilldown_menu.csv` when the study has one: for each finer level, its lines, the HTS8 base rate, whether a verified Chapter 99 layer covers only some lines, splits in the study period, and the verdict. Then show up to three deterministic rows from `tables/next_steps.csv`; state whether each needs official-source review, user entry details, a new DataWeb download or optional network research. The menu never performs those actions. `industry_context.mode = "light"` only records that the user opted into the separately sourced context step.
6. **Notes on measures**: use `tables/proxy_notes.csv` as the source of each proxy's fields, formula, denominator, unit, meaning, non-meaning, period, HTS release and quality warning; include the footnotes below that apply.

No HTS4 map section (decisions.md D-025): the brief stays at the scope's own level.

A drill-down brief (a finer scope or a second-unit study) stands alone: state its own numbers in full, and do not cite or correct an earlier brief. Keep the evidence label and source table on every number. At two pages, cut a chart before cutting the notes.

## Default narrative

Lead with charts, not a tour of the database. Use only what the request needs:

1. The question and the scope: what the HTS lines hold and what they cannot separate (`01`).
2. Who supplies the product and how concentrated it is (`04`, `07`).
3. Where the change went: every origin's inflow and outflow, annual and same-period YTD (`05`, `06`).
4. Which codes inside the scope moved (`02`).
5. Timing around policy dates (`03`).
6. Unit value where quantity is comparable, and realized duty (`08`, `09`).
7. HTS8/HTS10 product structure, tariff-treatment status, proxy notes, alternatives checked and the data check (`10`, `12` when measured).

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
- unit value is `CONS_CUSTOMS_VALUE / CONS_FIR_UNIT_QUANT` (or the explicitly selected second quantity): customs value per reported unit, not a transaction price, company ASP, market price or per-capacity price; it includes technology, capacity, package and specification mix, and quantity may be suppressed without a flag;
- realized duty is `CONS_CALC_DUTY / CONS_CUSTOMS_VALUE`: observed calculated duty incidence, not the HTS-based tariff treatment and not proof that every entry used one provision or program;
- country is the origin reported to U.S. Customs, not shipping country, value added, fab location, corporate supplier dependence or a transshipment-adjusted exposure;
- HTS10 share, when present, is same-period HTS10 World customs value divided by the independently queried parent HTS6 World customs value: U.S. import-value composition, not market share, company share or global capacity;
- the tariff snapshot is non-binding and tied to the stated entry date and archived HTS release; special programs, Chapter 98, quota/TRQ, warehouse/FTZ, AD/CVD, firm-specific rates and final classification can make it conditional;
- a legal-policy label such as "synthetic-opioid policy" explains the authority for a broad origin-based duty and does not describe the researched product;
- 连续性未核实, when the scope says so;
- descriptive, not causal;
- 回放结果，不是已批准研究, for a replay.

## QA

- Every plotted value is in the same-stem chart CSV or a named table.
- `brief-check` passes; `final_report.json` keeps its summary.
- Every proxy used in prose has a row in `tables/proxy_notes.csv`; every tariff-snapshot row states its status and input assumptions; no unavailable HTS10 share is shown as zero or normalized to 100%.
- Titles and bullets agree with the latest validated output.
- Check page or slide count and legibility separately from content.
- Do not say a report or deck is updated until the exported file has been opened or rendered and looked at. For `final_report.pdf`, render its pages to images if the viewer cannot show PDFs, and check that tables, charts and the appendix are legible.
