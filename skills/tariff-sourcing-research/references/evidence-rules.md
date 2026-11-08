# Evidence Rules

Read this when interpreting results, writing a claim, reviewing someone else's claim, or adding a research angle.

## Research order

1. **Pin the measurement**: approved scope version and `scope_sha256`, trade flow (imports for consumption unless the study says otherwise), nominal customs value, HTS level, frequency, `data_through`, quantity unit, continuity status.
2. **Use what exists**: validated outputs and cached snapshots before any refresh.
3. **Measure separately**: value, quantity, unit value, shares, the World total and realized duty are different measurements; none stands in for another.
4. **Check the alternatives** below before calling a decline relocation.
5. **Map policy** at HTS8/10 by origin and effective date.
6. **Keep timing apart from causation.**
7. **Present** only after `validate` passes, citing the table or chart CSV and the manifest.

## Gates

When one fails, stop the claims it touches and keep the narrower valid work.

| Gate | Stops |
|---|---|
| Quantity units differ, are missing or mixed in a product-period | quantity, quantity share and unit value there; value results continue |
| YTD compared with anything other than the same months of the prior year | the YTD claim |
| Origins do not sum to the independent World query, or a period is missing (missing is never zero) | that product-period |
| Long window without HTS continuity evidence | the claim of one continuous product, unless the report says 连续性未核实 |
| No product-level policy mapping | any claim about tariff treatment; it is Unknown, never zero tariff |
| No identification design | causal verbs ("caused", "drove", "because of the tariff") |

## Evidence labels

- **Measured**: a DataWeb value as reported (World total, `data_through`).
- **Derived**: computed by the pipeline from measured values (shares, growth, contribution, HHI, unit value, realized duty).
- **Inference**: an interpretation that needs outside evidence. The pipeline writes none; an inference in a report is the analyst's and must say what would confirm it.
- **Unknown**: cannot be answered from these data (unmapped HTS10 treatment, value-added path, quantity that is not comparable).

An inference never becomes a fact by repetition, a chart or a confident sentence.

## Alternatives to check

When the focal origin's imports or share fall, check at least:

1. another origin gained share (`origin_change.csv`: inflow, outflow, `contribution_pp`);
2. total U.S. imports contracted (World growth);
3. U.S. domestic production moved the other way;
4. the HTS definition or quantity unit changed;
5. the window is partial or has missing months;
6. tariff treatment differed across codes or origins;
7. the gaining origin still uses the focal origin's inputs.

"Top gaining origin" means the largest observed gain in U.S. import share, not the confirmed destination of the focal origin's production. Country of origin is what was declared to CBP, not value added.

## Policy treatment

The key for a policy claim is `origin x HTS8/HTS10 x effective date x status`. Keep the announcement date, effective or collection date, suspension, exclusion or end date, rate change and legal authority, implemented versus announced-only status, source URL and verification date, and the partial-month rule. Use primary sources (USITC, CBP, USTR, Federal Register) and verify current treatment before asserting it. `config/policy_events.csv` is a timeline for chart markers, not a treatment map. A change in realized duty can flag exposure; the treatment itself comes from the mapping.

## Time structure

- Use complete months on both sides of an effective date, or state a partial-month rule.
- Treat pandemic and other shock dates as context unless the design identifies their effect.
- Check missing months and HTS validity windows before reading slopes or breaks.
- A sharp visual break is a candidate episode, not a structural break.

## Concentration

HHI is computed from individual origins only (0–10,000); never treat "Other reported origins" as one country. Say whether a change reflects spreading across many origins or substitution toward one new dominant source (`top_origin`, `top3_share_pct`).

## Domestic production

Federal Reserve industrial production (via FRED) can only say whether a domestic-production explanation is directionally plausible; the HTS-to-NAICS mapping is manual and broad, and it cannot measure output of one HTS6 product. When the run reports the screen as skipped, that alternative stays Unknown.

## Claim template

```text
Measured: China's share of <scope> fell N pp from <base> to <compare> while World value changed M%.
Checked: origin gains, total demand, quantity unit, YTD window, continuity status.
Context: verified policy events and shock dates.
Inference: consistent / not consistent with descriptive sourcing reallocation.
Unknown: value-added path, unmapped HTS10 treatment.
Source: <table or chart CSV>, <analysis_manifest.json>.
```
