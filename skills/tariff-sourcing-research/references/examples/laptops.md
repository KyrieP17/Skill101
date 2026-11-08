# Example: U.S. laptop imports

> 这是例子，不是模板。 Copy the method, not the values: a new product gets its own scope, study and decisions.

Read this when working on laptops, when the user asks how an earlier study was done, or when you need the legacy baseline commands.

## Question

Where did China's share of U.S. laptop imports go after the 2025 tariff increases, and did it come back?

## Scope decisions

- `scopes/laptops/v2/reviewed.csv`: include HTS6 `847130` (portable ADP machines, No.); exclude `847141` (desktops and all-in-ones: same heading, different product) and `847330` (parts and accessories: inputs, a separate scope if needed).
- Tablets: `847130` holds tablets too, and no level separates them: the current HTS has one HTS8 line (8471.30.01) and one HTS10 line (8471.30.01.00) under it. So the product is named "laptops and tablets" (`decisions.md` D-014); D-001's plan to exclude tablets at HTS10 cannot be carried out.
- Continuity: no archive evidence yet, so the card says 连续性未核实 and the reviewer would have to type `UNVERIFIED`.
- `scopes/laptops/v1/scope.csv` is an older candidate draft and is not used.

## Study

`config/studies/laptops-2017-2026.toml`: annual 2017–2025, monthly 2017–2026, focal China, highlight Vietnam, Mexico, Taiwan, Thailand, India, comparison 2024 vs 2025, five event windows (pre-COVID, COVID, the baseline before the 2025 increases, the shock with its low, the partial return with its peak) and seven event markers from `config/policy_events.csv`.

## Two ways it runs

**Gated study** (the normal path, not yet run): the user approves `scopes/laptops/v2` with `scope-approve`, then `plan --study config/studies/laptops-2017-2026.toml`, `hts_current_check.py`, `acquire --plan ...` with the user's token, then `all --study ...`.

**Labeled replay** (acceptance and regression): `all --replay config/replays/laptops-legacy-baseline.toml` re-analyzes the 24 snapshots the earlier project downloaded (annual 2024–2025 and monthly 2017–2026, all origins plus World). Their response hashes are checked, but their request payloads were never recorded, and the scope is reviewed, not approved. Outputs go to `outputs/replays/laptops-legacy-baseline/`, and every page says 回放结果，不是已批准研究.

## What the replay found

Data through 2026-08 (monthly) and 2025 (annual); 116 months, none missing. China's share of U.S. laptop imports averaged 66.0% in the baseline window (2024-11 to 2025-01), fell to 1.7% in 2025-11 (64.3 pp lower), and was back to 11.4% by 2026-07. Read the gaining origins in `tables/origin_change.csv` and chart `05`; the single-code scope has no within-scope reallocation chart (`02`). These are replay numbers, Derived, and carry the replay label.

## Regression

`tests/test_study_pipeline.py` (`LaptopReplayRegressionTests`) runs the replay on a copy of the snapshots and requires that `tables/monthly_panel.csv`, `charts/03_event_path.csv` and `charts/03_event_path.events.csv` are byte-identical to the legacy baseline, and that the event numbers above match to 10 decimals. Other legacy outputs have no one-to-one counterpart: the HTS2 long-run table, other focus products, the FRED screen and the focus coverage audit are outside the laptop scope, and `event_metrics.json` changed structure but not values.

## Legacy baseline commands

Kept only to reproduce the original project. New products never use them.

```powershell
# The original research.toml baseline (the agent wrapper passes these through):
python scripts\tariff_pipeline.py all --legacy-baseline
python scripts\tariff_pipeline.py acquire --legacy-baseline    # re-downloads; needs the user's token

# Copy the earlier project's snapshots without modifying it (run in the project, not via the wrapper):
python run.py import-legacy --source "<earlier project folder>"
```

The imported long-run table holds only China, Mexico, Vietnam and World, and the baseline outputs embed absolute paths, so their hashes differ between machines.
