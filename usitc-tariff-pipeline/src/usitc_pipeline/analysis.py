"""Pure research metrics and validation rules.

Every function in this module accepts DataFrames and returns DataFrames/dicts;
none reads files, calls APIs, or draws charts.  That separation makes each
number testable and prevents a report or slide builder from quietly recomputing
the result with a different denominator.

Evidence boundary
-----------------
The outputs describe trade patterns around policy and shock dates.  They do not
identify a causal tariff effect.  In particular:

* country/area of origin is not a value-added supply-chain map;
* a falling focal-origin share can reflect reallocation, demand contraction,
  or domestic production, so those channels are screened separately;
* quantity is comparable only when every contributing row reports one first
  unit; otherwise quantity, quantity share and unit value are left blank while
  value-based results continue;
* realized duty (calculated duty / customs value) is not the statutory rate;
* HTS-to-NAICS/FRED links are analyst-selected broad proxies.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


KEY_COLUMNS = ["period", "frequency", "hts_level", "hts_code", "origin_name"]
PRODUCT_PERIOD = ["period", "frequency", "hts_level", "hts_code"]
PERIOD = ["frequency", "period"]
WORLD_NAMES = {"world", "total", "all countries", "all origins"}
OTHER_ORIGINS = "Other reported origins"
MEASURES = ["customs_value_usd", "quantity", "calculated_duty_usd"]
WORLD_MEASURES = ["world_value_usd", "world_quantity", "world_calculated_duty_usd"]
EVENT_PATH_COLUMNS = [
    "period",
    "period_date",
    "display_origin",
    "customs_value_usd",
    "quantity",
    "world_value_usd",
    "world_quantity",
    "value_share",
    "quantity_share",
    "unit_value_usd",
]


class ValidationError(ValueError):
    """A data-contract check failed; the message is meant for the analyst."""


def safe_divide(numerator: Any, denominator: Any) -> Any:
    """Divide while turning zero denominators into missing values.

    Works with pandas Series and scalar numeric values.  A missing result is
    preferable to either infinity or a fabricated zero rate.
    """

    if isinstance(numerator, pd.Series) or isinstance(denominator, pd.Series):
        numerator_series = numerator if isinstance(numerator, pd.Series) else pd.Series(numerator)
        denominator_series = denominator if isinstance(denominator, pd.Series) else pd.Series(denominator)
        denominator_series = pd.to_numeric(denominator_series, errors="coerce").where(
            pd.to_numeric(denominator_series, errors="coerce").ne(0)
        )
        return pd.to_numeric(numerator_series, errors="coerce").div(denominator_series)
    if denominator is None or pd.isna(denominator) or float(denominator) == 0:
        return float("nan")
    if numerator is None or pd.isna(numerator):
        return float("nan")
    return float(numerator) / float(denominator)


def add_period_status(
    frame: pd.DataFrame,
    *,
    ytd_end_month: int,
    ytd_year: int | None = None,
) -> pd.DataFrame:
    """Label annual YTD rows without changing monthly observations.

    ``ytd_year`` is the partial year; when omitted, the maximum annual year is
    used.  Monthly rows stay ``monthly``: the fact that the latest year has only
    some observed months belongs to annual aggregation, not to each month.
    """

    if not 1 <= int(ytd_end_month) <= 12:
        raise ValidationError("ytd_end_month must be between 1 and 12")
    result = frame.copy()
    if not {"period", "frequency"}.issubset(result.columns):
        raise ValidationError("period and frequency are required")
    annual_mask = result["frequency"].eq("annual")
    annual_years = pd.to_numeric(result.loc[annual_mask, "period"].astype(str).str[:4], errors="coerce")
    inferred_year = int(annual_years.max()) if ytd_year is None and annual_years.notna().any() else ytd_year
    result["period_status"] = np.where(annual_mask, "full_year", "monthly")
    if inferred_year is not None and int(ytd_end_month) < 12:
        row_year = pd.to_numeric(result["period"].astype(str).str[:4], errors="coerce")
        mask = annual_mask & row_year.eq(int(inferred_year))
        result.loc[mask, "period_status"] = f"ytd_through_{int(ytd_end_month):02d}"
    return result


def _required_columns(frame: pd.DataFrame, required: Iterable[str], *, context: str) -> None:
    missing = sorted(set(required) - set(frame.columns))
    if missing:
        raise ValidationError(f"{context} is missing required columns: {missing}")


def _world_mask(frame: pd.DataFrame) -> pd.Series:
    return frame["origin_name"].astype(str).str.strip().str.casefold().isin(WORLD_NAMES)


def derive_data_through(
    tidy: pd.DataFrame,
    active_codes_by_period: Mapping[tuple[str, str], Iterable[str]],
) -> dict[str, str | None]:
    """Return the conservative last complete observation by frequency.

    ``active_codes_by_period`` carries the scope history explicitly: each
    ``(frequency, period)`` key lists the HTS codes that must be present in the
    independent World extract for that period.  A period is observed when each
    active code has at least one non-missing reported measure.  Numeric zero is
    therefore a real observation, while a blank future placeholder is not.

    The scan stops at the first incomplete period within a frequency, so an
    internal gap or an incomplete code after a split is never hidden.  The one
    exception is a split inside a fully queried year: the blank months before a
    successor starts or after a predecessor ends (see ``_codes_not_in_force``).
    """

    _required_columns(
        tidy,
        ["period", "frequency", "hts_code", "origin_name"],
        context="Data-through input",
    )
    measure_columns = [column for column in MEASURES if column in tidy.columns]
    if not measure_columns:
        raise ValidationError("Data-through input has no supported trade measure")

    world = tidy.loc[_world_mask(tidy)].copy()
    if world.empty:
        raise ValidationError("Data-through requires independent World query rows")
    world["period"] = world["period"].astype(str)
    world["frequency"] = world["frequency"].astype(str)
    world["hts_code"] = world["hts_code"].astype(str)
    numeric_measures = world[measure_columns].apply(pd.to_numeric, errors="coerce")
    world["_has_observation"] = numeric_measures.notna().any(axis=1)
    world["_has_nonzero"] = numeric_measures.fillna(0).ne(0).any(axis=1)
    observed = (
        world.loc[world["_has_observation"]]
        .groupby(["frequency", "period"], dropna=False)["hts_code"]
        .agg(lambda values: set(values.astype(str)))
        .to_dict()
    )
    nonzero = (
        world.loc[world["_has_nonzero"]]
        .groupby(["frequency", "period"], dropna=False)["hts_code"]
        .agg(lambda values: set(values.astype(str)))
        .to_dict()
    )

    schedule: dict[str, dict[str, set[str]]] = {}
    for key, codes in active_codes_by_period.items():
        if not isinstance(key, tuple) or len(key) != 2:
            raise ValidationError("active_codes_by_period keys must be (frequency, period) tuples")
        frequency, period = map(str, key)
        active = {str(code) for code in codes if str(code).strip()}
        if not active:
            raise ValidationError(f"No active HTS codes for {frequency} {period}")
        schedule.setdefault(frequency, {})[period] = active

    result: dict[str, str | None] = {}
    for frequency, by_period in sorted(schedule.items()):
        periods = sorted(by_period)
        not_in_force = _codes_not_in_force(frequency, by_period, observed)
        by_period = {period: by_period[period] - not_in_force.get(period, set()) for period in periods}
        # DataWeb monthly calendar-year tables encode not-yet-published trailing
        # months as literal zeroes rather than blanks.  Preserve explicit zeroes
        # inside the observed sequence, but trim a contiguous all-zero suffix at
        # the end of a monthly query.
        trailing_placeholders: set[str] = set()
        if frequency == "monthly":
            for period in reversed(periods):
                active = by_period[period]
                if active.issubset(observed.get((frequency, period), set())) and not (
                    active & nonzero.get((frequency, period), set())
                ):
                    trailing_placeholders.add(period)
                    continue
                break
        last_complete: str | None = None
        for period in periods:
            if period in trailing_placeholders:
                break
            if not by_period[period].issubset(observed.get((frequency, period), set())):
                break
            last_complete = period
        result[frequency] = last_complete
    return result


def codes_not_in_force(
    tidy: pd.DataFrame,
    active_codes_by_period: Mapping[tuple[str, str], Iterable[str]],
) -> dict[tuple[str, str], set[str]]:
    """``(frequency, period)`` -> codes left blank because a split put them out of force.

    The same rule ``derive_data_through`` applies; used to narrow the coverage
    audit so a successor is not reported missing before it existed.
    """

    world = tidy.loc[_world_mask(tidy)].copy()
    measure_columns = [column for column in MEASURES if column in world.columns]
    if world.empty or not measure_columns:
        return {}
    has_observation = world[measure_columns].apply(pd.to_numeric, errors="coerce").notna().any(axis=1)
    observed: dict[tuple[str, str], set[str]] = {}
    for frequency, period, code in zip(
        world.loc[has_observation, "frequency"].astype(str),
        world.loc[has_observation, "period"].astype(str),
        world.loc[has_observation, "hts_code"].astype(str),
    ):
        observed.setdefault((frequency, period), set()).add(code)
    schedule: dict[str, dict[str, set[str]]] = {}
    for (frequency, period), codes in active_codes_by_period.items():
        schedule.setdefault(str(frequency), {})[str(period)] = {str(code) for code in codes}
    result: dict[tuple[str, str], set[str]] = {}
    for frequency, by_period in schedule.items():
        for period, codes in _codes_not_in_force(frequency, by_period, observed).items():
            result[(frequency, period)] = codes
    return result


def _codes_not_in_force(
    frequency: str,
    by_period: Mapping[str, set[str]],
    observed: Mapping[tuple[str, str], set[str]],
) -> dict[str, set[str]]:
    """Codes DataWeb leaves blank because they are not in force that month.

    A split inside a year is queried for the whole year with the predecessor
    and its successors.  DataWeb then returns a blank, not a zero, for a code
    in the months it is not in force (zeros are its unpublished placeholder).
    Within a fully queried calendar year (January and December scheduled), a
    code's blank run before its first observation (created mid-year) or after
    its last observation through December (retired mid-year) is excused, but
    only in months where another active code is observed.  A code never
    observed in the year, a blank between two observations, and a partial
    schedule are never excused, so a missing code still stops the scan.
    """

    excused: dict[str, set[str]] = {}
    if frequency != "monthly":
        return excused
    years: dict[str, list[str]] = {}
    for period in sorted(by_period):
        years.setdefault(period[:4], []).append(period)
    for year, months in years.items():
        if f"{year}-01" not in by_period or f"{year}-12" not in by_period:
            continue
        for code in set().union(*(by_period[month] for month in months)):
            active = [month for month in months if code in by_period[month]]
            seen = [month for month in active if code in observed.get((frequency, month), set())]
            if not seen:
                continue
            candidates = []
            if active[0] == f"{year}-01":
                candidates += [month for month in active if month < seen[0]]
            if active[-1] == f"{year}-12":
                candidates += [month for month in active if month > seen[-1]]
            for month in candidates:
                others = (by_period[month] - {code}) & observed.get((frequency, month), set())
                if others:
                    excused.setdefault(month, set()).add(code)
    return excused


def quantity_unit_status(frame: pd.DataFrame) -> pd.DataFrame:
    """Return, per product-period, whether first-unit quantities can be added.

    Quantities are comparable only when every non-zero measured row carries one
    known unit and that unit is the same for every origin and the World row.
    A label containing ``|`` marks a key whose source rows mixed units; its
    quantity was blanked during normalization, so it blocks the product-period
    whether or not a number is left.
    """

    data = frame[PRODUCT_PERIOD + ["quantity", "quantity_unit"]].copy()
    unit = data["quantity_unit"].astype("string").str.strip().str.lower()
    quantity = pd.to_numeric(data["quantity"], errors="coerce")
    measured = quantity.notna() & quantity.ne(0)
    unlabeled = unit.isna() | unit.eq("")
    mixed = unit.str.contains("|", regex=False).fillna(False)
    data["_bad"] = mixed | (measured & unlabeled)
    data["_unit"] = unit.where(measured & ~unlabeled & ~mixed)
    grouped = data.groupby(PRODUCT_PERIOD, dropna=False)
    status = pd.DataFrame(
        {
            "_bad_rows": grouped["_bad"].sum(),
            "_unit_count": grouped["_unit"].nunique(),
            "quantity_unit_label": grouped["_unit"].agg(lambda values: "|".join(sorted(set(values.dropna())))),
        }
    ).reset_index()
    status["quantity_comparable"] = status["_bad_rows"].eq(0) & status["_unit_count"].eq(1)
    return status.drop(columns=["_bad_rows", "_unit_count"])


def build_trade_panel(
    tidy: pd.DataFrame,
    *,
    require_independent_world: bool = True,
) -> pd.DataFrame:
    """Derive one validated origin-level trade panel.

    Independent World-query rows are required by default and used as the
    denominator.  A caller rebuilding a documented legacy extract may opt in
    to origin-sum fallback with ``require_independent_world=False``.  Explicit
    World rows are not included in the returned origin panel.

    Mixed or missing first units do not stop the panel: the product-period is
    flagged ``quantity_comparable=False`` and its quantity, quantity share and
    unit values are blank.  The reported raw quantities remain in the tidy
    table for audit.
    """

    required = KEY_COLUMNS + ["hts_description", *MEASURES, "quantity_unit", "source_file"]
    _required_columns(tidy, required, context="Normalized trade data")
    frame = tidy.copy()
    if frame.duplicated(KEY_COLUMNS).any():
        examples = frame.loc[frame.duplicated(KEY_COLUMNS, keep=False), KEY_COLUMNS].head().to_dict("records")
        raise ValidationError(f"Duplicate normalized product-period-origin keys: {examples}")
    for column in MEASURES:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["hts_code"] = frame["hts_code"].astype("string")
    frame["hts_level"] = pd.to_numeric(frame["hts_level"], errors="raise").astype(int)
    frame["quantity_unit"] = frame["quantity_unit"].astype("string").str.strip().str.lower()
    units = quantity_unit_status(frame)

    is_world = _world_mask(frame)
    origins = frame.loc[~is_world].copy()
    world = frame.loc[is_world].copy()
    if origins.empty:
        raise ValidationError("Trade panel contains no origin rows")

    origin_totals = origins.groupby(PRODUCT_PERIOD, as_index=False, dropna=False).agg(
        origin_value_sum_usd=("customs_value_usd", "sum"),
        origin_quantity_sum=("quantity", "sum"),
        origin_duty_sum_usd=("calculated_duty_usd", "sum"),
    )
    if world.empty and require_independent_world:
        raise ValidationError("Trade panel requires an independent World query for every product-period")
    if world.empty:
        totals = origin_totals.rename(
            columns={
                "origin_value_sum_usd": "world_value_usd",
                "origin_quantity_sum": "world_quantity",
                "origin_duty_sum_usd": "world_calculated_duty_usd",
            }
        )
        totals["world_total_source"] = "sum_of_reported_origins"
        totals = totals.merge(origin_totals, on=PRODUCT_PERIOD, how="left", validate="one_to_one")
    else:
        if world.duplicated(PRODUCT_PERIOD).any():
            examples = world.loc[world.duplicated(PRODUCT_PERIOD, keep=False), PRODUCT_PERIOD].head().to_dict("records")
            raise ValidationError(f"Duplicate World rows: {examples}")
        world_totals = world[PRODUCT_PERIOD + MEASURES].rename(columns=dict(zip(MEASURES, WORLD_MEASURES)))
        world_totals["world_total_source"] = "independent_world_query"
        missing_world_keys = origin_totals[PRODUCT_PERIOD].merge(
            world_totals[PRODUCT_PERIOD],
            on=PRODUCT_PERIOD,
            how="left",
            indicator=True,
        )
        missing_world_keys = missing_world_keys[missing_world_keys["_merge"].eq("left_only")]
        if require_independent_world and not missing_world_keys.empty:
            examples = missing_world_keys[PRODUCT_PERIOD].head().to_dict("records")
            raise ValidationError(f"Missing independent World rows for product-periods: {examples}")
        totals = world_totals.merge(origin_totals, on=PRODUCT_PERIOD, how="outer", validate="one_to_one")
        if not require_independent_world:
            for world_column, origin_column in (
                ("world_value_usd", "origin_value_sum_usd"),
                ("world_quantity", "origin_quantity_sum"),
                ("world_calculated_duty_usd", "origin_duty_sum_usd"),
            ):
                totals[world_column] = totals[world_column].combine_first(totals[origin_column])
            totals["world_total_source"] = totals["world_total_source"].fillna("sum_of_reported_origins")

    totals = totals.merge(units, on=PRODUCT_PERIOD, how="left", validate="one_to_one")
    totals["quantity_comparable"] = totals["quantity_comparable"].fillna(False).astype(bool)
    totals.loc[~totals["quantity_comparable"], ["world_quantity", "origin_quantity_sum"]] = np.nan
    totals["world_value_gap_pct"] = safe_divide(
        totals["origin_value_sum_usd"] - totals["world_value_usd"], totals["world_value_usd"]
    ) * 100
    totals["world_quantity_gap_pct"] = safe_divide(
        totals["origin_quantity_sum"] - totals["world_quantity"], totals["world_quantity"]
    ) * 100

    panel = origins.merge(totals, on=PRODUCT_PERIOD, how="left", validate="many_to_one")
    panel.loc[~panel["quantity_comparable"], "quantity"] = np.nan
    panel["value_share"] = safe_divide(panel["customs_value_usd"], panel["world_value_usd"])
    panel["quantity_share"] = safe_divide(panel["quantity"], panel["world_quantity"])
    panel["unit_value_usd"] = safe_divide(panel["customs_value_usd"], panel["quantity"])
    panel["world_unit_value_usd"] = safe_divide(panel["world_value_usd"], panel["world_quantity"])
    panel["realized_duty_rate"] = safe_divide(panel["calculated_duty_usd"], panel["customs_value_usd"])
    panel["world_realized_duty_rate"] = safe_divide(
        panel["world_calculated_duty_usd"], panel["world_value_usd"]
    )
    return panel.sort_values(KEY_COLUMNS, kind="stable").reset_index(drop=True)


def validate_trade_panel(
    panel: pd.DataFrame,
    *,
    strict: bool = True,
    share_tolerance: float = 0.005,
    reconciliation_tolerance_pct: float = 0.5,
) -> dict[str, Any]:
    """Run arithmetic and schema guardrails on a derived trade panel.

    Returns a machine-readable summary.  With ``strict=True`` any error raises
    :class:`ValidationError` so charts cannot be produced from a silently
    inconsistent panel.  Non-comparable quantity is a warning, not an error.
    """

    _required_columns(
        panel,
        KEY_COLUMNS
        + [
            "customs_value_usd",
            "quantity",
            "quantity_unit",
            "calculated_duty_usd",
            "world_value_usd",
            "value_share",
            "quantity_share",
            "realized_duty_rate",
        ],
        context="Trade panel",
    )
    errors: list[str] = []
    warnings: list[str] = []

    duplicates = panel.duplicated(KEY_COLUMNS, keep=False)
    if duplicates.any():
        errors.append(f"Duplicate product-period-origin rows: {int(duplicates.sum())}")

    expected_lengths = panel["hts_level"].astype(int)
    observed_lengths = panel["hts_code"].astype(str).str.len()
    if not expected_lengths.eq(observed_lengths).all():
        errors.append("HTS code length does not match hts_level")

    comparable = (
        panel["quantity_comparable"].astype(bool)
        if "quantity_comparable" in panel
        else pd.Series(True, index=panel.index)
    )
    blocked = panel.loc[~comparable, PRODUCT_PERIOD].drop_duplicates()
    if not blocked.empty:
        warnings.append(
            f"Quantity units are mixed or missing in {len(blocked)} product-period(s); "
            "quantity, quantity share and unit value are blank there"
        )

    bad_duty = panel["calculated_duty_usd"].fillna(0).gt(0) & panel["customs_value_usd"].fillna(0).le(0)
    if bad_duty.any():
        errors.append(f"Positive calculated duty with non-positive customs value: {int(bad_duty.sum())}")

    value_sums = panel.groupby(PRODUCT_PERIOD, dropna=False)["value_share"].sum(min_count=1)
    bad_value = value_sums[(value_sums - 1).abs() > share_tolerance]
    if not bad_value.empty:
        errors.append(f"Origin value shares do not sum to one in {len(bad_value)} product-period(s)")

    if "world_quantity" in panel:
        quantity_eligible = panel[comparable & panel["world_quantity"].fillna(0).gt(0)]
        if not quantity_eligible.empty:
            quantity_sums = quantity_eligible.groupby(PRODUCT_PERIOD, dropna=False)["quantity_share"].sum(min_count=1)
            bad_quantity = quantity_sums[(quantity_sums - 1).abs() > share_tolerance]
            if not bad_quantity.empty:
                errors.append(f"Origin quantity shares do not sum to one in {len(bad_quantity)} product-period(s)")

    if "world_value_gap_pct" in panel:
        gaps = panel[PRODUCT_PERIOD + ["world_value_gap_pct", "world_total_source"]].drop_duplicates(PRODUCT_PERIOD)
        independent = gaps["world_total_source"].eq("independent_world_query")
        bad_gap = independent & gaps["world_value_gap_pct"].abs().gt(reconciliation_tolerance_pct)
        if bad_gap.any():
            errors.append(f"Origin sum and independent World value differ in {int(bad_gap.sum())} product-period(s)")

    if "period_status" in panel:
        statuses = set(panel["period_status"].dropna().astype(str))
        if any(value.startswith("ytd_through_") for value in statuses) and "full_year" in statuses:
            warnings.append("Panel contains full-year and YTD rows; comparisons must filter period_status explicitly")

    result = {
        "ok": not errors,
        "rows": int(len(panel)),
        "product_periods": int(panel[PRODUCT_PERIOD].drop_duplicates().shape[0]),
        "quantity_blocked_product_periods": int(len(blocked)),
        "errors": errors,
        "warnings": warnings,
    }
    if strict and errors:
        raise ValidationError("; ".join(errors))
    return result


def _growth_pct(compare: float, base: float) -> float:
    return (safe_divide(compare, base) - 1) * 100


def build_product_reallocation(
    panel: pd.DataFrame,
    *,
    base_year: int,
    compare_year: int,
    focal_origin: str = "China",
) -> pd.DataFrame:
    """Separate focal-origin share movement, total demand, and gaining origins.

    The top gaining origin is a descriptive receiver, not proof that production
    moved directly from the focal origin.  When quantity is not comparable the
    demand channel is unknown, so a share loss is labeled as such instead of
    ``limited_share_change``.
    """

    annual = panel[panel["frequency"].eq("annual")].copy()
    annual["year"] = pd.to_numeric(annual["period"].astype(str).str[:4], errors="coerce")
    annual = annual[annual["year"].isin([base_year, compare_year])]
    rows: list[dict[str, Any]] = []

    for hts_code, product in annual.groupby("hts_code", sort=True):
        years = set(product["year"].dropna().astype(int))
        if {base_year, compare_year} - years:
            continue
        if "period_status" in product.columns:
            statuses: dict[int, str] = {}
            for year in (base_year, compare_year):
                values = (
                    product.loc[product["year"].eq(year), "period_status"]
                    .dropna()
                    .astype(str)
                    .str.strip()
                    .unique()
                )
                if len(values) > 1:
                    raise ValidationError(
                        f"Multiple period_status values for HTS {hts_code} in {year}: {values.tolist()}"
                    )
                if len(values) == 1:
                    statuses[year] = values[0]
            if base_year in statuses and compare_year in statuses and statuses[base_year] != statuses[compare_year]:
                raise ValidationError(
                    "Cannot compare full-year and YTD/mismatched period_status values: "
                    f"{base_year}={statuses[base_year]}, {compare_year}={statuses[compare_year]}"
                )
        description = product["hts_description"].dropna().astype(str)
        product_name = description.iloc[0] if not description.empty else str(hts_code)
        shares = product.pivot_table(
            index="origin_name", columns="year", values="value_share", aggfunc="sum"
        ).fillna(0)
        values = product.pivot_table(
            index="origin_name", columns="year", values="customs_value_usd", aggfunc="sum"
        ).fillna(0)
        quantities = product.pivot_table(
            index="origin_name", columns="year", values="quantity", aggfunc="sum"
        ).fillna(0)
        share_change = (shares[compare_year] - shares[base_year]) * 100
        non_focal = share_change.drop(labels=[focal_origin], errors="ignore")
        positive_gainers = non_focal[non_focal.gt(0)]
        top_origin = positive_gainers.idxmax() if not positive_gainers.empty else pd.NA
        top_gain = float(positive_gainers.max()) if not positive_gainers.empty else float("nan")

        focal_base_share = float(shares.at[focal_origin, base_year] * 100) if focal_origin in shares.index else 0.0
        focal_compare_share = (
            float(shares.at[focal_origin, compare_year] * 100) if focal_origin in shares.index else 0.0
        )
        focal_value_base = float(values.at[focal_origin, base_year]) if focal_origin in values.index else 0.0
        focal_value_compare = float(values.at[focal_origin, compare_year]) if focal_origin in values.index else 0.0

        world_by_year = product[["year", "world_value_usd", "world_quantity"]].drop_duplicates("year").set_index("year")
        world_value_base = float(world_by_year.at[base_year, "world_value_usd"])
        world_value_compare = float(world_by_year.at[compare_year, "world_value_usd"])
        world_quantity_base = float(world_by_year.at[base_year, "world_quantity"])
        world_quantity_compare = float(world_by_year.at[compare_year, "world_quantity"])

        quantity_values = pd.to_numeric(product["quantity"], errors="coerce")
        quantity_labels = product["quantity_unit"].astype("string").str.strip().str.lower()
        # Same rule as quantity_unit_status: a zero row (an origin without trade
        # that year, often unlabeled) says nothing about the unit.
        measured_quantity = quantity_values.notna() & quantity_values.ne(0)
        missing_unit = measured_quantity & (quantity_labels.isna() | quantity_labels.eq(""))
        units = quantity_labels[measured_quantity & ~missing_unit].unique()
        quantity_comparable = bool(measured_quantity.any() and not missing_unit.any() and len(units) == 1)
        if "quantity_comparable" in product:
            quantity_comparable = quantity_comparable and bool(product["quantity_comparable"].astype(bool).all())
        world_quantity_growth = (
            _growth_pct(world_quantity_compare, world_quantity_base) if quantity_comparable else float("nan")
        )
        focal_quantity_growth = float("nan")
        if quantity_comparable and focal_origin in quantities.index:
            focal_quantity_growth = _growth_pct(
                float(quantities.at[focal_origin, compare_year]), float(quantities.at[focal_origin, base_year])
            )

        focal_share_change = focal_compare_share - focal_base_share
        if focal_share_change < -0.5:
            if pd.isna(world_quantity_growth):
                pattern = "share_loss_demand_quantity_not_comparable"
            elif world_quantity_growth > 0:
                pattern = "share_loss_with_demand_growth"
            else:
                pattern = "share_loss_with_demand_contraction"
        elif focal_share_change > 0.5:
            pattern = "china_share_gain" if focal_origin == "China" else "focal_share_gain"
        else:
            pattern = "limited_share_change"

        record = {
            "hts_code": str(hts_code),
            "product": product_name,
            "base_year": base_year,
            "compare_year": compare_year,
            "focal_origin": focal_origin,
            "focal_share_base_pct": focal_base_share,
            "focal_share_compare_pct": focal_compare_share,
            "focal_share_change_pp": focal_share_change,
            "focal_import_value_growth_pct": _growth_pct(focal_value_compare, focal_value_base),
            "focal_import_quantity_growth_pct": focal_quantity_growth,
            "world_import_value_growth_pct": _growth_pct(world_value_compare, world_value_base),
            "world_import_quantity_growth_pct": world_quantity_growth,
            "quantity_unit": units[0] if quantity_comparable else pd.NA,
            "quantity_comparable": quantity_comparable,
            "top_gaining_origin": top_origin,
            "top_origin_share_gain_pp": top_gain,
            "pattern": pattern,
        }
        if focal_origin == "China":
            record.update(
                {
                    "china_share_base_pct": focal_base_share,
                    "china_share_compare_pct": focal_compare_share,
                    "china_share_change_pp": focal_share_change,
                    "china_import_value_growth_pct": record["focal_import_value_growth_pct"],
                    "china_import_quantity_growth_pct": focal_quantity_growth,
                }
            )
        rows.append(record)
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("focal_share_change_pp", kind="stable").reset_index(drop=True)


def _period_quantity_ok(panel: pd.DataFrame) -> pd.Series:
    """(frequency, period) -> True when every contributing row shares one comparable unit."""

    comparable = panel["quantity_comparable"].astype(bool) if "quantity_comparable" in panel else True
    data = panel.assign(_ok=comparable)
    flags = data.groupby(PERIOD)["_ok"].all()
    measured = data[pd.to_numeric(data["quantity"], errors="coerce").notna()]
    unit_counts = measured.groupby(PERIOD)["quantity_unit"].nunique().reindex(flags.index).fillna(0)
    return flags & unit_counts.eq(1)


def _with_rates(frame: pd.DataFrame) -> pd.DataFrame:
    frame["value_share"] = safe_divide(frame["customs_value_usd"], frame["world_value_usd"])
    frame["quantity_share"] = safe_divide(frame["quantity"], frame["world_quantity"])
    frame["unit_value_usd"] = safe_divide(frame["customs_value_usd"], frame["quantity"])
    frame["realized_duty_rate"] = safe_divide(frame["calculated_duty_usd"], frame["customs_value_usd"])
    return frame


def build_origin_series(panel: pd.DataFrame, *, quantity_comparable: bool = True) -> pd.DataFrame:
    """Aggregate the panel across scope codes: one row per period and origin.

    The World denominator is the sum of the per-code independent World rows.
    Quantity is kept only where the scope declares one unit and every
    contributing row in that period reports it.
    """

    status = (
        panel[PERIOD + ["period_status"]].drop_duplicates(PERIOD)
        if "period_status" in panel
        else panel[PERIOD].drop_duplicates().assign(period_status=pd.NA)
    )
    origin = panel.groupby(PERIOD + ["origin_name"], dropna=False)[MEASURES].sum(min_count=1).reset_index()
    world = (
        panel.drop_duplicates(PERIOD + ["hts_code"])
        .groupby(PERIOD, dropna=False)[WORLD_MEASURES]
        .sum(min_count=1)
        .reset_index()
    )
    ok = _period_quantity_ok(panel) & bool(quantity_comparable)
    world = world.merge(ok.rename("quantity_comparable").reset_index(), on=PERIOD, how="left")
    world["quantity_comparable"] = world["quantity_comparable"].fillna(False).astype(bool)
    result = origin.merge(world, on=PERIOD, how="left", validate="many_to_one").merge(status, on=PERIOD, how="left")
    result.loc[~result["quantity_comparable"], ["quantity", "world_quantity"]] = np.nan
    result = _with_rates(result)
    columns = PERIOD + ["period_status", "origin_name", *MEASURES, *WORLD_MEASURES]
    columns += ["value_share", "quantity_share", "unit_value_usd", "realized_duty_rate", "quantity_comparable"]
    return result[columns].sort_values(PERIOD + ["origin_name"], kind="stable").reset_index(drop=True)


def build_display_series(
    origin_series: pd.DataFrame,
    *,
    focal_origin: str,
    highlight_origins: Iterable[str] = (),
) -> pd.DataFrame:
    """Collapse non-highlighted origins into one display group per period."""

    keep = {focal_origin, *highlight_origins}
    data = origin_series.copy()
    data["display_origin"] = np.where(data["origin_name"].isin(keep), data["origin_name"], OTHER_ORIGINS)
    grouped = data.groupby(PERIOD + ["display_origin"], dropna=False)[MEASURES].sum(min_count=1).reset_index()
    world = data.drop_duplicates(PERIOD)[PERIOD + ["period_status", *WORLD_MEASURES, "quantity_comparable"]]
    result = grouped.merge(world, on=PERIOD, how="left", validate="many_to_one")
    result.loc[~result["quantity_comparable"].astype(bool), "quantity"] = np.nan
    result = _with_rates(result)
    columns = PERIOD + ["period_status", "display_origin", *MEASURES, *WORLD_MEASURES]
    columns += ["value_share", "quantity_share", "unit_value_usd", "realized_duty_rate", "quantity_comparable"]
    return result[columns].sort_values(PERIOD + ["display_origin"], kind="stable").reset_index(drop=True)


def build_scope_series(origin_series: pd.DataFrame, *, focal_origin: str) -> pd.DataFrame:
    """Scope totals, unit value, realized duty and concentration per period.

    HHI uses origin value shares on the 0-10,000 scale.  It measures how
    concentrated declared origins are, not value-added concentration.
    """

    rows: list[dict[str, Any]] = []
    for (frequency, period), group in origin_series.groupby(PERIOD, sort=True):
        first = group.iloc[0]
        shares = group["value_share"].fillna(0)
        ordered = group.assign(_share=shares).sort_values(["_share", "origin_name"], ascending=[False, True])
        focal = group.loc[group["origin_name"].eq(focal_origin), "value_share"]
        world_value = float(first["world_value_usd"]) if pd.notna(first["world_value_usd"]) else float("nan")
        rows.append(
            {
                "frequency": frequency,
                "period": period,
                "period_status": first["period_status"],
                "world_value_usd": world_value,
                "world_quantity": first["world_quantity"],
                "world_unit_value_usd": safe_divide(world_value, first["world_quantity"]),
                "world_calculated_duty_usd": first["world_calculated_duty_usd"],
                "world_realized_duty_rate_pct": safe_divide(first["world_calculated_duty_usd"], world_value) * 100,
                "reporting_origins": int(group["customs_value_usd"].fillna(0).gt(0).sum()),
                "hhi": float((shares * 100).pow(2).sum()),
                "top_origin": ordered.iloc[0]["origin_name"] if shares.gt(0).any() else pd.NA,
                "top_origin_share_pct": float(ordered["_share"].iloc[0] * 100),
                "top3_share_pct": float(ordered["_share"].head(3).sum() * 100),
                "focal_origin": focal_origin,
                "focal_value_share_pct": float(focal.fillna(0).sum() * 100) if world_value > 0 else float("nan"),
                "quantity_comparable": bool(first["quantity_comparable"]),
            }
        )
    return pd.DataFrame(rows)


def _window_totals(origin_series: pd.DataFrame, frequency: str, periods: Iterable[str]) -> tuple[pd.DataFrame, dict]:
    selected = origin_series[origin_series["frequency"].eq(frequency) & origin_series["period"].isin(list(periods))]
    by_origin = selected.groupby("origin_name")[MEASURES].sum(min_count=1)
    per_period = selected.drop_duplicates("period")
    world = {
        "customs_value_usd": per_period["world_value_usd"].sum(min_count=1),
        "quantity": per_period["world_quantity"].sum(min_count=1),
        "calculated_duty_usd": per_period["world_calculated_duty_usd"].sum(min_count=1),
        "quantity_comparable": bool(per_period["quantity_comparable"].astype(bool).all()) and not per_period.empty,
        "periods": int(per_period["period"].nunique()),
    }
    if not world["quantity_comparable"]:
        by_origin["quantity"] = np.nan
        world["quantity"] = float("nan")
    return by_origin, world


def compare_origin_windows(
    origin_series: pd.DataFrame,
    *,
    comparison: str,
    frequency: str,
    base_periods: Iterable[str],
    compare_periods: Iterable[str],
) -> pd.DataFrame:
    """Value, share, quantity, unit value and duty changes for every origin.

    ``direction`` describes customs-value inflow or outflow for an origin;
    ``contribution_pp`` is that origin's change as a share of the base-period
    World value, so the origin rows sum to the World growth rate.
    """

    base_periods, compare_periods = list(map(str, base_periods)), list(map(str, compare_periods))
    base, base_world = _window_totals(origin_series, frequency, base_periods)
    compare, compare_world = _window_totals(origin_series, frequency, compare_periods)
    if base_world["periods"] != len(base_periods) or compare_world["periods"] != len(compare_periods):
        return pd.DataFrame()
    world_row = pd.DataFrame(
        [{**{f"{key}_base": base_world[key] for key in MEASURES}, **{f"{key}_compare": compare_world[key] for key in MEASURES}}],
        index=pd.Index(["World"], name="origin_name"),
    )
    table = pd.concat([world_row, base.join(compare, how="outer", lsuffix="_base", rsuffix="_compare")])
    table.index.name = "origin_name"
    for column in ("customs_value_usd_base", "customs_value_usd_compare", "calculated_duty_usd_base", "calculated_duty_usd_compare"):
        table[column] = table[column].fillna(0.0)
    quantity_ok = base_world["quantity_comparable"] and compare_world["quantity_comparable"]
    for column in ("quantity_base", "quantity_compare"):
        table[column] = table[column].fillna(0.0) if quantity_ok else np.nan

    def pct_of_world(values: pd.Series, world_total: float) -> pd.Series:
        return values / world_total * 100 if world_total and pd.notna(world_total) else values * np.nan

    value_base, value_compare = table["customs_value_usd_base"], table["customs_value_usd_compare"]
    result = pd.DataFrame(
        {
            "comparison": comparison,
            "frequency": frequency,
            "base_periods": base_periods[0] if len(base_periods) == 1 else f"{base_periods[0]}..{base_periods[-1]}",
            "compare_periods": compare_periods[0] if len(compare_periods) == 1 else f"{compare_periods[0]}..{compare_periods[-1]}",
            "base_value_usd": value_base,
            "compare_value_usd": value_compare,
            "value_change_usd": value_compare - value_base,
            "value_growth_pct": (safe_divide(value_compare, value_base) - 1) * 100,
            "contribution_pp": pct_of_world(value_compare - value_base, base_world["customs_value_usd"]),
            "base_share_pct": pct_of_world(value_base, base_world["customs_value_usd"]),
            "compare_share_pct": pct_of_world(value_compare, compare_world["customs_value_usd"]),
            "base_quantity": table["quantity_base"],
            "compare_quantity": table["quantity_compare"],
            "quantity_growth_pct": (safe_divide(table["quantity_compare"], table["quantity_base"]) - 1) * 100,
            "base_unit_value_usd": safe_divide(value_base, table["quantity_base"]),
            "compare_unit_value_usd": safe_divide(value_compare, table["quantity_compare"]),
            "base_realized_duty_pct": safe_divide(table["calculated_duty_usd_base"], value_base) * 100,
            "compare_realized_duty_pct": safe_divide(table["calculated_duty_usd_compare"], value_compare) * 100,
        },
        index=table.index,
    )
    result["share_change_pp"] = result["compare_share_pct"] - result["base_share_pct"]
    result["unit_value_change_pct"] = (safe_divide(result["compare_unit_value_usd"], result["base_unit_value_usd"]) - 1) * 100
    result["quantity_comparable"] = quantity_ok
    result["direction"] = np.select(
        [
            result.index == "World",
            value_base.eq(0) & value_compare.gt(0),
            value_base.gt(0) & value_compare.eq(0),
            result["value_change_usd"].gt(0),
            result["value_change_usd"].lt(0),
        ],
        ["total", "new_origin", "exited", "inflow", "outflow"],
        default="unchanged",
    )
    result = result.reset_index()
    world_row = result[result["origin_name"].eq("World")]
    others = result[result["origin_name"].ne("World")].sort_values(
        ["value_change_usd", "origin_name"], ascending=[False, True], kind="stable"
    )
    return pd.concat([world_row, others], ignore_index=True)


def build_world_check(panel: pd.DataFrame, *, tolerance_pct: float = 0.5) -> pd.DataFrame:
    """One row per product-period: independent World total vs the origin sum."""

    columns = PRODUCT_PERIOD + [
        "world_total_source",
        "world_value_usd",
        "origin_value_sum_usd",
        "world_value_gap_pct",
        "world_quantity",
        "origin_quantity_sum",
        "world_quantity_gap_pct",
        "world_calculated_duty_usd",
        "origin_duty_sum_usd",
    ]
    check = panel.drop_duplicates(PRODUCT_PERIOD)[columns].copy()
    check["duty_gap_pct"] = safe_divide(
        check["origin_duty_sum_usd"] - check["world_calculated_duty_usd"], check["world_calculated_duty_usd"]
    ) * 100
    check["status"] = np.where(check["world_value_gap_pct"].abs().le(tolerance_pct), "ok", "check")
    return check.sort_values(["frequency", "period", "hts_code"], kind="stable").reset_index(drop=True)


def build_event_path(
    display_series: pd.DataFrame,
    *,
    focal_origin: str,
    windows: Iterable[Any],
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Monthly display-origin path plus neutral summaries for each event window.

    No tariff, pandemic, exit or recovery story is inferred from a window's
    position in the configuration; every window gets the same statistics.
    """

    data = display_series[display_series["frequency"].eq("monthly")].copy()
    if data.empty:
        return pd.DataFrame(columns=EVENT_PATH_COLUMNS), {"available": False, "reason": "No monthly rows"}
    data["period_date"] = pd.to_datetime(data["period"].astype(str) + "-01", errors="coerce")
    data = data[data["period_date"].notna() & data["world_value_usd"].fillna(0).gt(0)]
    monthly = data[EVENT_PATH_COLUMNS].sort_values(["period_date", "display_origin"], kind="stable")
    monthly = monthly.reset_index(drop=True)
    focal = monthly[monthly["display_origin"].eq(focal_origin)].sort_values("period_date")
    if focal.empty:
        return monthly, {"available": False, "reason": f"No {focal_origin} monthly rows"}

    window_metrics: dict[str, dict[str, Any]] = {}
    previous_weighted: float | None = None
    for item in windows:
        rows = focal[focal["period"].between(item.start, item.end)]
        valid = rows.dropna(subset=["value_share"])
        minimum = valid.loc[valid["value_share"].idxmin()] if not valid.empty else None
        maximum = valid.loc[valid["value_share"].idxmax()] if not valid.empty else None
        weighted = float(safe_divide(rows["customs_value_usd"].sum(), rows["world_value_usd"].sum()) * 100)
        slope = (
            float(np.polyfit(np.arange(len(valid)), valid["value_share"] * 100, 1)[0])
            if len(valid) >= 2
            else float("nan")
        )
        window_metrics[item.window_id] = {
            "label": item.label,
            "start": item.start,
            "end": item.end,
            "months_observed": int(len(valid)),
            "weighted_focal_value_share_pct": weighted,
            "first_month_share_pct": float(valid["value_share"].iloc[0] * 100) if not valid.empty else float("nan"),
            "last_month_share_pct": float(valid["value_share"].iloc[-1] * 100) if not valid.empty else float("nan"),
            "min_share_month": str(minimum["period"]) if minimum is not None else None,
            "min_share_pct": float(minimum["value_share"] * 100) if minimum is not None else float("nan"),
            "max_share_month": str(maximum["period"]) if maximum is not None else None,
            "max_share_pct": float(maximum["value_share"] * 100) if maximum is not None else float("nan"),
            "share_slope_pp_per_month": slope,
            "weighted_change_from_previous_window_pp": (
                weighted - previous_weighted if previous_weighted is not None else float("nan")
            ),
        }
        previous_weighted = weighted
    return monthly, {
        "available": True,
        "focal_origin": focal_origin,
        "event_windows": window_metrics,
        "summary": _event_summary(window_metrics),
        "interpretation": "descriptive_event_windows_no_causal_claim",
    }


def _event_summary(window_metrics: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """The lowest focal month across windows, against the window before it and the latest window's peak.

    Only window order is used, never window names: the same numbers describe
    an exit and a partial return when the windows are set up that way.
    """

    observed = {key: item for key, item in window_metrics.items() if item["months_observed"]}
    if not observed:
        return {}
    ids = list(observed)
    low_id = min(ids, key=lambda key: observed[key]["min_share_pct"])
    low = observed[low_id]
    summary: dict[str, Any] = {"low_window": low_id, "low_month": low["min_share_month"], "low_share_pct": low["min_share_pct"]}
    position = ids.index(low_id)
    if position > 0:
        reference = observed[ids[position - 1]]
        summary.update(
            reference_window=ids[position - 1],
            reference_weighted_share_pct=reference["weighted_focal_value_share_pct"],
            low_vs_reference_pp=low["min_share_pct"] - reference["weighted_focal_value_share_pct"],
        )
    if ids[-1] != low_id:
        latest = observed[ids[-1]]
        summary.update(
            latest_window=ids[-1],
            latest_max_month=latest["max_share_month"],
            latest_max_share_pct=latest["max_share_pct"],
            latest_max_vs_low_pp=latest["max_share_pct"] - low["min_share_pct"],
        )
    return summary


def build_domestic_production_screen(
    fred: pd.DataFrame,
    mapping: pd.DataFrame,
    *,
    base_year: int,
    compare_year: int,
) -> pd.DataFrame:
    """Calculate annual change in manually matched Federal Reserve IP series.

    The series values are official Federal Reserve observations distributed by
    FRED.  The percentage change is our arithmetic, and the HTS→NAICS/series
    mapping is our broad analytical choice.  This screen cannot prove that a
    specific HTS product was reshored.
    """

    _required_columns(fred, ["series_id", "date", "value"], context="FRED data")
    _required_columns(
        mapping,
        ["hts_code", "series_id", "naics", "industry_name", "mapping_quality", "notes"],
        context="Proxy mapping",
    )
    data = fred.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data["year"] = data["date"].dt.year
    data["value"] = pd.to_numeric(data["value"], errors="coerce")
    annual = (
        data[data["year"].isin([base_year, compare_year])]
        .groupby(["series_id", "year"], as_index=False)
        .agg(index_value=("value", "mean"), retrieved_at=("retrieved_at", "first") if "retrieved_at" in data else ("value", lambda _: pd.NA))
    )
    pivot = annual.pivot(index="series_id", columns="year", values="index_value")
    rows: list[dict[str, Any]] = []
    for item in mapping.to_dict("records"):
        series_id = item["series_id"]
        base_index = float(pivot.at[series_id, base_year]) if series_id in pivot.index and base_year in pivot else float("nan")
        compare_index = float(pivot.at[series_id, compare_year]) if series_id in pivot.index and compare_year in pivot else float("nan")
        result = dict(item)
        result.update(
            {
                "base_year": base_year,
                "compare_year": compare_year,
                "base_index": base_index,
                "compare_index": compare_index,
                "index_change_pct": _growth_pct(compare_index, base_index),
                "source": "Federal Reserve industrial-production series via FRED",
                "inference_limit": "broad_directional_screen_not_product_level_reshoring",
            }
        )
        rows.append(result)
    return pd.DataFrame(rows)


def audit_period_coverage(
    tidy: pd.DataFrame,
    *,
    expected_periods: Iterable[str] | None = None,
    expected_by_code: Mapping[str, Iterable[str]] | None = None,
) -> pd.DataFrame:
    """Audit missing years/months and unit/description breaks by HTS code.

    Unlike a first-year/last-year check, this function lists internal gaps.  A
    code observed in 1989 and 2025 can still have years missing because HTS
    definitions change over time.  ``expected_by_code`` narrows the expected
    periods of a code to those it was queried and in force for (a code created
    or retired by a split); other codes use ``expected_periods``.
    """

    _required_columns(tidy, ["hts_code", "period", "quantity_unit", "hts_description"], context="Coverage data")
    default_expected = set(map(str, expected_periods)) if expected_periods is not None else None
    by_code = {str(code): set(map(str, periods)) for code, periods in (expected_by_code or {}).items()}
    rows: list[dict[str, Any]] = []
    for hts_code, group in tidy.groupby("hts_code", sort=True):
        expected = by_code.get(str(hts_code), default_expected)
        measure_columns = [column for column in MEASURES if column in group.columns]
        if measure_columns:
            numeric = group[measure_columns].apply(pd.to_numeric, errors="coerce")
            measured = group.loc[numeric.notna().any(axis=1)]
        else:
            measured = group
        # Labels are query coverage, not observations; an explicit numeric
        # zero inside the sequence is a real observation, a trailing all-zero
        # monthly tail is a DataWeb placeholder.
        observed = set(measured["period"].dropna().astype(str))
        if measure_columns and observed and all("-" in period for period in observed):
            numeric = group[measure_columns].apply(pd.to_numeric, errors="coerce")
            nonzero_periods = set(
                group.loc[numeric.fillna(0).ne(0).any(axis=1), "period"].dropna().astype(str)
            )
            ordered = sorted(expected or observed)
            trailing_zero_placeholders: set[str] = set()
            for period in reversed(ordered):
                if period in observed and period not in nonzero_periods:
                    trailing_zero_placeholders.add(period)
                    continue
                break
            observed -= trailing_zero_placeholders
        if expected is not None:
            observed &= expected
        local_expected = expected or observed
        missing = sorted(local_expected - observed)
        units = sorted(group["quantity_unit"].dropna().astype(str).unique())
        descriptions = sorted(group["hts_description"].dropna().astype(str).unique())
        rows.append(
            {
                "hts_code": str(hts_code),
                "first_observed_period": min(observed) if observed else pd.NA,
                "last_observed_period": max(observed) if observed else pd.NA,
                "observed_period_count": len(observed),
                "missing_period_count": len(missing),
                "missing_periods": ";".join(missing),
                "quantity_unit_count": len(units),
                "description_count": len(descriptions),
                "needs_review": bool(missing or len(units) > 1 or len(descriptions) > 1),
            }
        )
    return pd.DataFrame(rows)
