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
* :func:`build_share_decomposition`: for each origin and window, how much of
  its share change came from its own value and how much from all other
  origins, and how much of its value change came from quantity and how much
  from unit value (``[decomposition] enabled = true``).

These functions only add arithmetic on validated values; nothing here reads
files or decides scope.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from .analysis import OTHER_ORIGINS, PERIOD, safe_divide


QUANTITY_CONFLICT_PCT = 5.0
TOTAL_LABEL = "Scope total"
DECOMPOSITION_COLUMNS = (
    "window_kind", "base_periods", "compare_periods", "display_origin",
    "base_value_usd", "compare_value_usd", "value_change_usd", "value_growth_pct",
    "base_others_value_usd", "compare_others_value_usd", "others_value_change_usd",
    "base_world_value_usd", "compare_world_value_usd", "world_value_change_usd",
    "base_share_pct", "compare_share_pct", "share_change_pp", "share_change_from_own_pp", "share_change_from_others_pp",
    "quantity_unit", "base_quantity", "compare_quantity", "base_unit_value_usd", "compare_unit_value_usd",
    "value_change_from_quantity_usd", "value_change_from_unit_value_usd",
    "base_realized_duty_pct", "compare_realized_duty_pct", "timeline_events",
)
# ``[decomposition] mix = true``: the unit-value part split again, across the
# scope's codes, into price within each code and the shift between codes.
MIX_COLUMNS = ("value_change_from_price_usd", "value_change_from_mix_usd")


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


def _next_month(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    return f"{year + month // 12}-{month % 12 + 1:02d}"


def _decomposition_windows(
    display: pd.DataFrame, windows: Iterable[Window], custom: Sequence[tuple[str, str]]
) -> list[tuple[str, str, tuple[str, ...], tuple[str, ...]]]:
    """(kind, frequency, base, compare): the study's named months, the YTD
    comparison, every pair of consecutive full years, every pair of consecutive months."""

    result = [("custom", "monthly", (start,), (end,)) for start, end in custom]
    result += [("ytd", window.frequency, window.base, window.compare) for window in windows if window.kind == "ytd"]
    annual = display[display["frequency"].eq("annual") & display["period_status"].eq("full_year")]
    years = sorted({int(period) for period in annual["period"].astype(str)})
    result += [("annual", "annual", (str(year - 1),), (str(year),)) for year in years if year - 1 in years]
    months = sorted(set(display.loc[display["frequency"].eq("monthly"), "period"].astype(str)))
    result += [("month", "monthly", (prev,), (cur,)) for prev, cur in zip(months, months[1:]) if _next_month(prev) == cur]
    return result


def _timeline_text(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value).strip()


def _events_between(timeline: Sequence[dict[str, Any]], first_day: pd.Timestamp, last_day: pd.Timestamp) -> str:
    """Timeline rows dated inside the window, as in the chart key; dates only, no attribution."""

    items: list[str] = []
    for row in timeline:
        when = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(when) or not first_day <= when <= last_day:
            continue
        text = f"{when:%Y-%m-%d} {_timeline_text(row.get('date_type'))}: {_timeline_text(row.get('measure'))}"
        rate = _timeline_text(row.get("rate_change"))
        items.append(text + (f" ({rate})" if rate else "") + (" [to verify]" if row.get("needs_check") else ""))
    return "; ".join(items)


def _price_mix(base: pd.DataFrame, compare: pd.DataFrame) -> tuple[float, float]:
    """Unit-value change times mean quantity, split into price within codes and mix across codes.

    ``base`` and ``compare`` hold value and quantity by code.  With quantity
    shares ``s`` and unit values ``p`` per code, the average unit value is
    ``P = sum(s * p)``, so ``P1 - P0 = sum(mean(s) * dp) + sum(ds * mean(p))``
    exactly.  A code present on one side only keeps its own unit value on the
    other, so all of its change is mix.  Returns NaN when a code has value but
    no quantity (the identity would not hold).
    """

    codes = sorted(set(base.index) | set(compare.index))
    b = base.reindex(codes).fillna(0.0).astype(float)
    c = compare.reindex(codes).fillna(0.0).astype(float)
    for side in (b, c):
        if (side["customs_value_usd"].gt(0) & ~side["quantity"].gt(0)).any():
            return float("nan"), float("nan")
    used = b["quantity"].gt(0) | c["quantity"].gt(0)
    b, c = b[used], c[used]
    q0, q1 = float(b["quantity"].sum()), float(c["quantity"].sum())
    if q0 <= 0 or q1 <= 0:
        return float("nan"), float("nan")
    p0 = b["customs_value_usd"] / b["quantity"].where(b["quantity"].gt(0))
    p1 = c["customs_value_usd"] / c["quantity"].where(c["quantity"].gt(0))
    p0, p1 = p0.fillna(p1), p1.fillna(p0)
    s0, s1 = b["quantity"] / q0, c["quantity"] / q1
    mean_quantity = (q0 + q1) / 2
    price = mean_quantity * float(((s0 + s1) / 2 * (p1 - p0)).sum())
    mix = mean_quantity * float(((s1 - s0) * (p0 + p1) / 2).sum())
    return price, mix


def build_share_decomposition(
    display: pd.DataFrame,
    windows: Iterable[Window],
    *,
    focal_origin: str,
    highlight_origins: Sequence[str],
    custom_windows: Sequence[tuple[str, str]] = (),
    quantity_unit: str | None = None,
    timeline: Sequence[dict[str, Any]] | None = None,
    panel: pd.DataFrame | None = None,
    mix: bool = False,
) -> pd.DataFrame:
    """Split each origin's share change and value change into their parts.

    For an origin with value ``v`` and all other origins ``o`` (World minus
    ``v``), share is ``v / (v + o)``.  The share change between two windows is
    split into the part from ``v`` changing and the part from ``o`` changing,
    each averaged over both orders (a two-factor Shapley split), so the two
    parts add up to the share change exactly.  The value change is split the
    same way into quantity and unit value (``v = q * p``), only when the
    quantity is comparable and positive on both sides.

    ``timeline_events`` lists the timeline rows dated from the first day of
    the base periods to the last day of the compare periods: a date
    alignment, not a cause.  A window whose periods are not all observed is
    left out.

    With ``mix`` (and the trade ``panel``), the unit-value part is split once
    more into ``value_change_from_price_usd`` (unit values within each code)
    and ``value_change_from_mix_usd`` (quantity moving between codes); see
    :func:`_price_mix`.  With one code in the scope the mix part is 0, and the
    price part still holds any product mix inside the code.
    """

    windows = list(windows)
    order = [focal_origin, *highlight_origins, OTHER_ORIGINS]
    present = set(display["display_origin"])
    origins = [origin for origin in dict.fromkeys(order) if origin in present]
    coded = None
    if mix:
        if panel is None:
            raise ValueError("the price/mix split needs the trade panel")
        keep = {focal_origin, *highlight_origins}
        coded = panel.assign(
            display_origin=np.where(panel["origin_name"].isin(keep), panel["origin_name"], OTHER_ORIGINS),
            period=panel["period"].astype(str),
            hts_code=panel["hts_code"].astype(str),
        )

    def by_code(frequency: str, periods: Sequence[str], origin: str) -> pd.DataFrame:
        rows = coded[coded["frequency"].eq(frequency) & coded["period"].isin(list(periods)) & coded["display_origin"].eq(origin)]
        return rows.groupby("hts_code")[["customs_value_usd", "quantity"]].sum(min_count=1)

    rows: list[dict[str, Any]] = []
    for kind, frequency, base_periods, compare_periods in _decomposition_windows(display, windows, custom_windows):
        sides = []
        for periods in (base_periods, compare_periods):
            selected = display[display["frequency"].eq(frequency) & display["period"].astype(str).isin(periods)]
            sides.append((selected, _sum(selected.drop_duplicates("period")["world_value_usd"])))
        if any(selected["period"].astype(str).nunique() != len(periods) for (selected, _), periods in zip(sides, (base_periods, compare_periods))):
            continue
        span = "Y" if frequency == "annual" else "M"
        first_day = pd.Period(base_periods[0], freq=span).start_time
        last_day = pd.Period(compare_periods[-1], freq=span).end_time.normalize()
        events = _events_between(timeline or [], first_day, last_day)
        for origin in origins:
            row: dict[str, Any] = {
                "window_kind": kind,
                "base_periods": Window.label(tuple(base_periods)),
                "compare_periods": Window.label(tuple(compare_periods)),
                "display_origin": origin,
            }
            measures = []
            for (selected, world), side_name in zip(sides, ("base", "compare")):
                own = selected[selected["display_origin"].eq(origin)]
                value, quantity = _sum(own["customs_value_usd"]), _sum(own["quantity"])
                value = 0.0 if np.isnan(value) else value
                comparable = not own.empty and own["quantity_comparable"].fillna(False).astype(bool).all() and quantity > 0
                measures.append((value, world, quantity if comparable else float("nan")))
                row[f"{side_name}_value_usd"] = value
                row[f"{side_name}_others_value_usd"] = world - value
                row[f"{side_name}_world_value_usd"] = world
                row[f"{side_name}_share_pct"] = _pct(value, world)
                row[f"{side_name}_realized_duty_pct"] = _pct(_sum(own["calculated_duty_usd"]), value) if value else float("nan")
            (v0, w0, q0), (v1, w1, q1) = measures
            o0, o1 = w0 - v0, w1 - v1
            row["value_change_usd"] = v1 - v0
            row["value_growth_pct"] = _growth(v1, v0)
            row["others_value_change_usd"] = o1 - o0
            row["world_value_change_usd"] = w1 - w0
            row["share_change_pp"] = row["compare_share_pct"] - row["base_share_pct"]

            def share(own_value: float, others: float) -> float:
                return _pct(own_value, own_value + others)

            row["share_change_from_own_pp"] = ((share(v1, o0) - share(v0, o0)) + (share(v1, o1) - share(v0, o1))) / 2
            row["share_change_from_others_pp"] = ((share(v0, o1) - share(v0, o0)) + (share(v1, o1) - share(v1, o0))) / 2
            quantity_ok = not (np.isnan(q0) or np.isnan(q1))
            row["quantity_unit"] = quantity_unit if quantity_ok else None
            row["base_quantity"], row["compare_quantity"] = (q0, q1) if quantity_ok else (float("nan"), float("nan"))
            p0, p1 = (v0 / q0, v1 / q1) if quantity_ok else (float("nan"), float("nan"))
            row["base_unit_value_usd"], row["compare_unit_value_usd"] = p0, p1
            row["value_change_from_quantity_usd"] = (q1 - q0) * (p0 + p1) / 2 if quantity_ok else float("nan")
            row["value_change_from_unit_value_usd"] = (p1 - p0) * (q0 + q1) / 2 if quantity_ok else float("nan")
            row["timeline_events"] = events
            if mix:
                price, shift = (float("nan"), float("nan"))
                if quantity_ok:
                    price, shift = _price_mix(by_code(frequency, base_periods, origin), by_code(frequency, compare_periods, origin))
                    unit_part = row["value_change_from_unit_value_usd"]
                    if not abs(price + shift - unit_part) <= 1e-6 * max(1.0, abs(unit_part)):
                        price, shift = float("nan"), float("nan")
                row["value_change_from_price_usd"], row["value_change_from_mix_usd"] = price, shift
            rows.append(row)
    return pd.DataFrame(rows, columns=list(DECOMPOSITION_COLUMNS) + (list(MIX_COLUMNS) if mix else []))


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
