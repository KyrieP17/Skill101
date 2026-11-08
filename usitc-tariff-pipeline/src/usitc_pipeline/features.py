"""Tables for opt-in study features, computed from the validated panel.

* :func:`build_focal_split`: the focal origin, every other origin together and
  the total, for each comparison window: value, share, calculated duty in USD,
  realized duty, and quantity (plus the second unit when it was fetched).
* :func:`build_segment_series`: the same measures for labeled groups of scope
  codes (``[[segments]]``), e.g. to join HTS10 lines that were split mid-study.
* :func:`build_quantity_check`: growth of the first unit against the second
  unit for every code, segment and the total; opposite signs of at least 5%
  make a quantity conclusion unreliable.
* :func:`add_second_unit_columns`: the second unit beside the first in an
  origin or comparison table (``[quantity] unit = "both"``).

These functions only add arithmetic on validated values; nothing here reads
files or decides scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from .analysis import PERIOD, safe_divide


QUANTITY_CONFLICT_PCT = 5.0
TOTAL_LABEL = "Scope total"


@dataclass(frozen=True)
class Window:
    """One comparison: ``base`` periods against ``compare`` periods of one frequency."""

    comparison: str
    frequency: str
    base: tuple[str, ...]
    compare: tuple[str, ...]

    @property
    def kind(self) -> str:
        return "annual" if self.comparison.startswith("annual_") else "ytd"

    @staticmethod
    def label(periods: tuple[str, ...]) -> str:
        return periods[0] if len(periods) == 1 else f"{periods[0]}..{periods[-1]}"


def _growth(compare: Any, base: Any) -> float:
    value = safe_divide(compare, base)
    return float((value - 1) * 100) if pd.notna(value) else float("nan")


def _pct(part: Any, whole: Any) -> float:
    value = safe_divide(part, whole)
    return float(value * 100) if pd.notna(value) else float("nan")


def _sum(values: pd.Series) -> float:
    total = pd.to_numeric(values, errors="coerce").sum(min_count=1)
    return float(total) if pd.notna(total) else float("nan")


def _code_world(panel: pd.DataFrame) -> pd.DataFrame:
    """One row per (period, code) with that code's independent World totals."""

    return panel.drop_duplicates(PERIOD + ["hts_code"])


def _totals(panel: pd.DataFrame, weight: pd.DataFrame | None, frequency: str, periods: Iterable[str],
            codes: set[str] | None, focal: str) -> dict[str, float]:
    """World and focal totals over ``periods`` for ``codes`` (all codes when None)."""

    periods = [str(period) for period in periods]

    def select(frame: pd.DataFrame) -> pd.DataFrame:
        mask = frame["frequency"].eq(frequency) & frame["period"].astype(str).isin(periods)
        if codes is not None:
            mask &= frame["hts_code"].astype(str).isin(codes)
        return frame[mask]

    rows = select(panel)
    world = _code_world(rows)
    focal_rows = rows[rows["origin_name"].eq(focal)]
    result = {
        "periods_observed": int(world["period"].nunique()),
        "world_value_usd": _sum(world["world_value_usd"]),
        "world_duty_usd": _sum(world["world_calculated_duty_usd"]),
        "world_quantity": _sum(world["world_quantity"]),
        "focal_value_usd": _sum(focal_rows["customs_value_usd"]) if not focal_rows.empty else 0.0,
        "focal_duty_usd": _sum(focal_rows["calculated_duty_usd"]) if not focal_rows.empty else 0.0,
        "focal_quantity": _sum(focal_rows["quantity"]) if not focal_rows.empty else 0.0,
        "quantity_units": int(rows.loc[pd.to_numeric(rows["quantity"], errors="coerce").fillna(0).ne(0), "quantity_unit"].nunique()),
    }
    if weight is not None:
        wrows = select(weight)
        wfocal = wrows[wrows["origin_name"].eq(focal)]
        result["world_quantity_2"] = _sum(_code_world(wrows)["world_quantity"])
        result["focal_quantity_2"] = _sum(wfocal["quantity"]) if not wfocal.empty else 0.0
    return result


def build_focal_split(
    panel: pd.DataFrame,
    windows: Iterable[Window],
    *,
    focal_origin: str,
    quantity_unit: str | None,
    quantity_comparable: bool,
    weight_panel: pd.DataFrame | None = None,
    weight_unit: str | None = None,
) -> pd.DataFrame:
    """Three rows per comparison: focal origin, all other origins, total.

    The total is the independent World query; "other origins" is total minus
    focal, so the three rows always add up.  Quantity appears only when the
    study's units are comparable in both windows.
    """

    rows: list[dict[str, Any]] = []
    for window in windows:
        base = _totals(panel, weight_panel, window.frequency, window.base, None, focal_origin)
        compare = _totals(panel, weight_panel, window.frequency, window.compare, None, focal_origin)
        if base["periods_observed"] != len(window.base) or compare["periods_observed"] != len(window.compare):
            continue
        quantity_ok = quantity_comparable and base["quantity_units"] <= 1 and compare["quantity_units"] <= 1
        for group, label in (("focal", focal_origin), ("other", "Other origins"), ("total", "All origins")):

            def pick(side: dict[str, float], measure: str) -> float:
                world, focal = side[f"world_{measure}"], side[f"focal_{measure}"]
                return {"focal": focal, "other": world - focal, "total": world}[group]

            row: dict[str, Any] = {
                "comparison": window.comparison,
                "base_periods": Window.label(window.base),
                "compare_periods": Window.label(window.compare),
                "group": group,
                "label": label,
            }
            for side_name, side in (("base", base), ("compare", compare)):
                row[f"{side_name}_value_usd"] = pick(side, "value_usd")
                row[f"{side_name}_share_pct"] = _pct(pick(side, "value_usd"), side["world_value_usd"])
                row[f"{side_name}_calculated_duty_usd"] = pick(side, "duty_usd")
                row[f"{side_name}_realized_duty_pct"] = _pct(pick(side, "duty_usd"), pick(side, "value_usd"))
            row["value_change_usd"] = row["compare_value_usd"] - row["base_value_usd"]
            row["value_growth_pct"] = _growth(row["compare_value_usd"], row["base_value_usd"])
            row["share_change_pp"] = row["compare_share_pct"] - row["base_share_pct"]
            row["duty_change_usd"] = row["compare_calculated_duty_usd"] - row["base_calculated_duty_usd"]
            row["quantity_unit"] = quantity_unit if quantity_ok else None
            for side_name, side in (("base", base), ("compare", compare)):
                row[f"{side_name}_quantity"] = pick(side, "quantity") if quantity_ok else float("nan")
            row["quantity_growth_pct"] = _growth(row["compare_quantity"], row["base_quantity"])
            if weight_panel is not None:
                row["quantity_2_unit"] = weight_unit
                for side_name, side in (("base", base), ("compare", compare)):
                    row[f"{side_name}_quantity_2"] = pick(side, "quantity_2")
                    row[f"{side_name}_quantity_2_share_pct"] = _pct(pick(side, "quantity_2"), side["world_quantity_2"])
                    row[f"{side_name}_unit_value_2_usd"] = safe_divide(row[f"{side_name}_value_usd"], row[f"{side_name}_quantity_2"])
                row["quantity_2_growth_pct"] = _growth(row["compare_quantity_2"], row["base_quantity_2"])
            rows.append(row)
    return pd.DataFrame(rows)


def build_segment_series(
    panel: pd.DataFrame,
    segments: Iterable[Any],
    windows: Iterable[Window],
    *,
    focal_origin: str,
    weight_panel: pd.DataFrame | None = None,
    weight_unit: str | None = None,
) -> pd.DataFrame:
    """Per segment (and the scope total): every full annual year plus the YTD windows.

    Columns: imports, the focal origin's value share, calculated duty and
    realized duty; with the second unit also weight and the focal weight share.
    A segment-period with no observed code is left out rather than shown as 0.
    """

    groups = [(segment.label, set(segment.codes)) for segment in segments] + [(TOTAL_LABEL, None)]
    annual = panel[panel["frequency"].eq("annual")]
    if "period_status" in annual:
        annual = annual[annual["period_status"].eq("full_year")]
    periods: list[tuple[str, str, tuple[str, ...]]] = [
        ("annual", period, (period,)) for period in sorted(annual["period"].astype(str).unique())
    ]
    for window in windows:
        if window.kind == "ytd":
            for side in (window.base, window.compare):
                periods.append(("ytd", Window.label(side), side))
    rows: list[dict[str, Any]] = []
    for label, codes in groups:
        for kind, name, members in periods:
            frequency = "annual" if kind == "annual" else "monthly"
            totals = _totals(panel, weight_panel, frequency, members, codes, focal_origin)
            if totals["periods_observed"] == 0 or not totals["world_value_usd"] > 0:
                continue
            row = {
                "segment": label,
                "codes": "; ".join(sorted(codes)) if codes is not None else "all included codes",
                "frequency": kind,
                "period": name,
                "months_observed": totals["periods_observed"] if kind == "ytd" else None,
                "world_value_usd": totals["world_value_usd"],
                "focal_value_usd": totals["focal_value_usd"],
                "focal_value_share_pct": _pct(totals["focal_value_usd"], totals["world_value_usd"]),
                "world_calculated_duty_usd": totals["world_duty_usd"],
                "focal_calculated_duty_usd": totals["focal_duty_usd"],
                "world_realized_duty_pct": _pct(totals["world_duty_usd"], totals["world_value_usd"]),
                "focal_realized_duty_pct": _pct(totals["focal_duty_usd"], totals["focal_value_usd"]),
            }
            if weight_panel is not None:
                row.update(
                    quantity_2_unit=weight_unit,
                    world_quantity_2=totals["world_quantity_2"],
                    focal_quantity_2=totals["focal_quantity_2"],
                    focal_quantity_2_share_pct=_pct(totals["focal_quantity_2"], totals["world_quantity_2"]),
                    world_unit_value_2_usd=safe_divide(totals["world_value_usd"], totals["world_quantity_2"]),
                )
            rows.append(row)
    return pd.DataFrame(rows)


def build_quantity_check(
    panel: pd.DataFrame,
    weight_panel: pd.DataFrame,
    windows: Iterable[Window],
    *,
    segments: Iterable[Any] = (),
    first_unit: str | None,
    second_unit: str | None,
    total_comparable: bool,
    threshold_pct: float = QUANTITY_CONFLICT_PCT,
) -> pd.DataFrame:
    """First-unit growth against second-unit growth for each code, segment and the total.

    A code, segment or total is checked only in windows where it is observed
    in every month (or year) of both sides.  ``conflict`` is True when the two
    growth rates have opposite signs and both are at least ``threshold_pct``
    in absolute value.
    """

    codes = sorted(panel["hts_code"].astype(str).unique())
    groups = [("code", code, {code}) for code in codes]
    groups += [("segment", segment.label, set(segment.codes)) for segment in segments]
    if total_comparable:
        groups.append(("total", TOTAL_LABEL, None))
    rows: list[dict[str, Any]] = []
    for window in windows:
        for level, key, members in groups:
            base = _totals(panel, weight_panel, window.frequency, window.base, members, focal="")
            compare = _totals(panel, weight_panel, window.frequency, window.compare, members, focal="")
            if base["periods_observed"] != len(window.base) or compare["periods_observed"] != len(window.compare):
                continue
            if base["quantity_units"] > 1 or compare["quantity_units"] > 1:
                continue
            first = _growth(compare["world_quantity"], base["world_quantity"])
            second = _growth(compare["world_quantity_2"], base["world_quantity_2"])
            conflict = bool(
                np.isfinite(first) and np.isfinite(second) and first * second < 0
                and abs(first) >= threshold_pct and abs(second) >= threshold_pct
            )
            rows.append(
                {
                    "level": level,
                    "key": key,
                    "comparison": window.comparison,
                    "base_periods": Window.label(window.base),
                    "compare_periods": Window.label(window.compare),
                    "first_unit": first_unit,
                    "base_quantity": base["world_quantity"],
                    "compare_quantity": compare["world_quantity"],
                    "quantity_growth_pct": first,
                    "second_unit": second_unit,
                    "base_quantity_2": base["world_quantity_2"],
                    "compare_quantity_2": compare["world_quantity_2"],
                    "quantity_2_growth_pct": second,
                    "conflict": conflict,
                }
            )
    return pd.DataFrame(rows)


def add_second_unit_columns(table: pd.DataFrame, second: pd.DataFrame, *, keys: list[str], kind: str) -> pd.DataFrame:
    """Put the second unit beside the first: ``kind`` is "series" or "change".

    ``second`` is the same table computed on the second unit.  Series tables
    gain ``quantity_2``, ``world_quantity_2``, ``quantity_2_share`` and
    ``unit_value_2_usd``; comparison tables gain base/compare second-unit
    quantity, share, unit value and growth.
    """

    if kind == "series":
        columns = {"quantity": "quantity_2", "world_quantity": "world_quantity_2",
                   "quantity_share": "quantity_2_share", "unit_value_usd": "unit_value_2_usd"}
    else:
        columns = {"base_quantity": "base_quantity_2", "compare_quantity": "compare_quantity_2",
                   "quantity_growth_pct": "quantity_2_growth_pct", "base_unit_value_usd": "base_unit_value_2_usd",
                   "compare_unit_value_usd": "compare_unit_value_2_usd"}
    extra = second[keys + [column for column in columns if column in second]].rename(columns=columns)
    if kind == "change":
        world = second[second["origin_name"].eq("World")].set_index("comparison")
        for side in ("base", "compare"):
            totals = extra["comparison"].map(world[f"{side}_quantity"])
            extra[f"{side}_quantity_2_share_pct"] = safe_divide(extra[f"{side}_quantity_2"], totals) * 100
    return table.merge(extra, on=keys, how="left", validate="one_to_one")


HEADING_MAP_COLUMNS = (
    "heading", "hts6", "in_scope", "comparison", "frequency", "base_periods", "compare_periods",
    "value_usd", "base_value_usd", "value_growth_pct", "focal_value_usd", "focal_share_pct",
    "calculated_duty_usd", "realized_duty_pct", "share_of_heading_pct",
)


def build_heading_map(
    breakout: pd.DataFrame,
    world: pd.DataFrame,
    windows: Sequence[Window],
    *,
    focal_origin: str,
    scope_hts6: Iterable[str],
) -> pd.DataFrame:
    """Each HTS6 of the scope's headings, plus the heading total, per comparison window.

    ``world`` is the independent World query (value and calculated duty);
    ``breakout`` gives the focal origin's value.  A window not fully observed
    in ``world`` is skipped, as in the other feature tables.
    """

    scope_hts6 = set(scope_hts6)
    rows: list[dict[str, Any]] = []
    for window in windows:
        world_f = world[world["frequency"].eq(window.frequency)]
        observed = set(world_f["period"].astype(str))
        if not set(window.base) <= observed or not set(window.compare) <= observed:
            continue
        focal_f = breakout[breakout["frequency"].eq(window.frequency) & breakout["origin_name"].eq(focal_origin)]

        def total(frame: pd.DataFrame, periods: tuple[str, ...], codes: set[str], column: str) -> float:
            subset = frame[frame["period"].astype(str).isin(periods) & frame["hts_code"].astype(str).isin(codes)]
            return _sum(subset[column])

        codes_all = set(world_f["hts_code"].astype(str))
        for heading in sorted({code[:4] for code in codes_all}):
            heading_codes = {code for code in codes_all if code.startswith(heading)}
            heading_value = total(world_f, window.compare, heading_codes, "customs_value_usd")
            for code in [*sorted(heading_codes), None]:
                codes = heading_codes if code is None else {code}
                value = total(world_f, window.compare, codes, "customs_value_usd")
                base_value = total(world_f, window.base, codes, "customs_value_usd")
                focal_value = total(focal_f, window.compare, codes, "customs_value_usd")
                duty = total(world_f, window.compare, codes, "calculated_duty_usd")
                rows.append({
                    "heading": heading,
                    "hts6": "HTS4 total" if code is None else code,
                    "in_scope": code is not None and code in scope_hts6,
                    "comparison": window.comparison,
                    "frequency": window.frequency,
                    "base_periods": Window.label(window.base),
                    "compare_periods": Window.label(window.compare),
                    "value_usd": value,
                    "base_value_usd": base_value,
                    "value_growth_pct": _growth(value, base_value),
                    "focal_value_usd": focal_value,
                    "focal_share_pct": _pct(focal_value, value),
                    "calculated_duty_usd": duty,
                    "realized_duty_pct": _pct(duty, value),
                    "share_of_heading_pct": _pct(value, heading_value),
                })
    return pd.DataFrame(rows, columns=list(HEADING_MAP_COLUMNS))
