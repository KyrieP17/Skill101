"""Rule-based tables that help write the brief, never conclusions (D-019, D-024).

* :func:`build_anomaly_flags` -> ``tables/anomaly_flags.csv`` (``[anomalies]
  enabled = true``): the anomaly types of deliverables section 5, each with
  its numbers, the rule it crossed and a question template.  A flag is an
  observation to ask about, not a finding and not a cause.
* :func:`build_mention_list` -> ``tables/mention_list.csv`` (``[brief]
  candidates = true``): what the brief must mention, with the threshold that
  put it on the list; ``brief-check`` looks for each item's keywords.
* :func:`build_window_candidates` -> ``tables/window_candidates.csv``
  (``[brief] candidates = true``): turning points of the focal origin's
  monthly share path, as candidate ``[decomposition] windows``.

Thresholds are the module constants below; change them here, with a test.
"""

from __future__ import annotations

from datetime import date
import re
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from .analysis import OTHER_ORIGINS, safe_divide


# Anomaly rules.
NEW_ORIGIN_MIN_SHARE_PCT = 0.1   # a new origin is flagged once it reaches this share
NEAR_ZERO_SHARE_PCT = 0.5        # "almost nothing" in the base window ...
GREW_TO_SHARE_PCT = 1.0          # ... and at least this in the compare window
UNIT_VALUE_RATIO = 1.5           # unit value >= 1.5x, or <= 1/1.5, of the scope's
MATERIAL_SHARE_PCT = 1.0         # unit-value and duty rules skip origins below this share
BASE_RATE_GAP_PP = 0.5           # realized duty this far below the HTS8 base rate
TIMELINE_DUTY_FRACTION = 0.5     # realized duty below half of base rate + the row's rate
SURGE_PCT = 30.0                 # value growth over the same months a year earlier
SURGE_MONTHS = 2                 # months before a timeline date, and after an effective date
# Must-mention list.
MENTION_SHARE_PP = 2.0
MENTION_CONTRIBUTION_PP = 2.0
MENTION_TOP = 3
MENTION_DUTY_PP = 1.0
# Window candidates.
CANDIDATE_EPSILON_PP = 5.0       # Douglas-Peucker tolerance on the monthly share path
CANDIDATE_MIN_CHANGE_PP = 5.0    # smaller turns are listed but not suggested
SEASONAL_MAX_MONTHS = 2          # a short move (this many months or fewer) ...
SEASONAL_OTHER_YEARS = 2         # ... ending in a calendar month where it recurs, same sign, in this many other years

WORLD = "World"
OTHERS = "Other origins"
ANOMALY_COLUMNS = (
    "rule", "comparison", "periods", "origin", "value", "reference", "threshold",
    "observed", "timeline", "source", "question",
)
MENTION_COLUMNS = ("item", "keywords", "reason", "source")
CANDIDATE_COLUMNS = (
    "start", "end", "months", "start_share_pct", "end_share_pct", "share_change_pp",
    "kind", "suggested", "in_study", "note", "toml",
)


def _text(value: Any) -> str:
    return "" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value).strip()


def _number(value: Any) -> float:
    number = pd.to_numeric(value, errors="coerce")
    return float(number) if pd.notna(number) else float("nan")


def _sides(row: pd.Series) -> str:
    return f"{row['base_periods']} -> {row['compare_periods']}"


def rate_level(text: Any) -> float | None:
    """The ad valorem rate a timeline row sets: "+10%", "+10% -> +20%" (20), "to 84%"; else None."""

    text = _text(text)
    for pattern in (
        r"\+?\s*(\d+(?:\.\d+)?)\s*%",
        r"\+?\s*\d+(?:\.\d+)?\s*%\s*(?:->|→)\s*\+?\s*(\d+(?:\.\d+)?)\s*%",
        r"(?:to|至|到)\s*(\d+(?:\.\d+)?)\s*%",
    ):
        match = re.fullmatch(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None


def named_origins(text: Any, candidates: Sequence[str]) -> list[str]:
    """Which of ``candidates`` a timeline row's ``origins`` names ("All origins [except A; B]" or "A; B")."""

    text = _text(text)
    if text.casefold().startswith("all origins") or text.startswith("全部"):
        match = re.search(r"\bexcept\s+(.+)$", text, flags=re.IGNORECASE)
        excluded = {name.strip() for name in re.split(r"[;；,，、]", match.group(1))} if match else set()
        return [origin for origin in candidates if origin not in excluded]
    names = {name.strip() for name in re.split(r"[;；]", text) if name.strip()}
    return [origin for origin in candidates if origin in names]


def base_rate(menu: Iterable[dict[str, Any]] | None) -> tuple[float | None, str]:
    """The scope's single ad valorem HTS8 base rate from the drill-down menu, or why there is none."""

    rates: dict[str, str] = {}
    for row in menu or []:
        for part in _text(row.get("hts8_base_rate")).split(";"):
            code, _, rate = part.partition(":")
            if code.strip():
                rates[code.strip()] = rate.strip()
    if not rates:
        return None, "no HTS8 base rate: the study has no [drilldown]"
    parsed: dict[str, float | None] = {}
    for code, rate in rates.items():
        match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*%", rate)
        parsed[code] = 0.0 if rate.casefold() == "free" else float(match.group(1)) if match else None
    if any(value is None for value in parsed.values()):
        return None, f"HTS8 base rate is not a plain ad valorem rate: {rates}"
    if len(set(parsed.values())) > 1:
        return None, f"HTS8 base rates differ between lines: {rates}"
    return next(iter(parsed.values())), ""


def _timeline_label(row: dict[str, Any]) -> str:
    rate = _text(row.get("rate_change"))
    text = f"{row.get('date')} {_text(row.get('date_type'))}: {_text(row.get('measure'))}" + (f" ({rate})" if rate else "")
    return text + (" [to verify]" if row.get("needs_check") else "")


class _Months:
    """Monthly value and duty of the display origins, World, and World minus the focal origin."""

    def __init__(self, display: pd.DataFrame, focal: str) -> None:
        monthly = display[display["frequency"].eq("monthly")].copy()
        monthly["period"] = monthly["period"].astype(str)
        self.rows = monthly
        self.periods = set(monthly["period"])
        self.focal = focal
        self.world = monthly.drop_duplicates("period").set_index("period")["world_value_usd"].astype(float)

    def totals(self, origin: str, periods: Sequence[str]) -> tuple[float, float, float] | None:
        """(value, duty, World value) summed over ``periods``; None unless every month is observed."""

        if not periods or not set(periods) <= self.periods:
            return None
        world = float(self.world.loc[list(periods)].sum())
        rows = self.rows[self.rows["period"].isin(periods)]
        if origin == WORLD:
            return world, float("nan"), world
        name = self.focal if origin == OTHERS else origin
        own = rows[rows["display_origin"].eq(name)]
        value = float(pd.to_numeric(own["customs_value_usd"], errors="coerce").sum())
        duty = float(pd.to_numeric(own["calculated_duty_usd"], errors="coerce").sum())
        if origin == OTHERS:
            return world - value, float("nan"), world
        return value, duty, world


def _month(day: date, offset: int) -> str:
    return str(pd.Period(day, freq="M") + offset)


def build_anomaly_flags(
    changes: pd.DataFrame,
    display: pd.DataFrame,
    *,
    focal_origin: str,
    highlight_origins: Sequence[str] = (),
    timeline: Sequence[dict[str, Any]] | None = None,
    drilldown_menu: Sequence[dict[str, Any]] | None = None,
    quantity_check: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, dict[str, str]]:
    """Every flag the rules raise, and the rules that could not run (with the reason)."""

    rows: list[dict[str, Any]] = []
    skipped: dict[str, str] = {}

    def flag(rule: str, **values: Any) -> None:
        rows.append({"rule": rule, **values})

    origins = changes[changes["origin_name"].ne(WORLD)] if not changes.empty else changes
    world_rows = changes[changes["origin_name"].eq(WORLD)].set_index("comparison") if not changes.empty else pd.DataFrame()
    if changes.empty:
        skipped["origin_rules"] = "no annual or YTD comparison"
    for _, row in origins.iterrows():
        base_share, compare_share = _number(row["base_share_pct"]), _number(row["compare_share_pct"])
        common = {"comparison": row["comparison"], "periods": _sides(row), "origin": row["origin_name"],
                  "source": "tables/origin_change.csv", "timeline": ""}
        if row.get("direction") == "new_origin" and compare_share >= NEW_ORIGIN_MIN_SHARE_PCT:
            flag("new_origin", value=compare_share, reference=0.0, threshold=f"base value 0 and compare share >= {NEW_ORIGIN_MIN_SHARE_PCT}%",
                 observed=f"{row['origin_name']} 在 {row['base_periods']} 没有进口，{row['compare_periods']} 占 {compare_share:.2f}%",
                 question=f"{row['origin_name']} 从哪个月开始有进口、是哪些编码？", **common)
        elif base_share < NEAR_ZERO_SHARE_PCT and compare_share >= GREW_TO_SHARE_PCT:
            flag("grew_from_almost_nothing", value=compare_share, reference=base_share,
                 threshold=f"base share < {NEAR_ZERO_SHARE_PCT}% and compare share >= {GREW_TO_SHARE_PCT}%",
                 observed=f"{row['origin_name']} 份额 {base_share:.2f}% -> {compare_share:.2f}%（{row['base_periods']} -> {row['compare_periods']}）",
                 question=f"{row['origin_name']} 的增长集中在哪几个月、哪些编码？", **common)
        world = world_rows.loc[row["comparison"]] if row["comparison"] in world_rows.index else None
        unit_value = _number(row["compare_unit_value_usd"])
        world_unit = _number(world["compare_unit_value_usd"]) if world is not None else float("nan")
        ratio = safe_divide(unit_value, world_unit)
        if compare_share >= MATERIAL_SHARE_PCT and pd.notna(ratio) and (ratio >= UNIT_VALUE_RATIO or ratio <= 1 / UNIT_VALUE_RATIO):
            flag("unit_value_far_off", value=unit_value, reference=world_unit,
                 threshold=f"unit value >= {UNIT_VALUE_RATIO}x or <= 1/{UNIT_VALUE_RATIO} of all origins, share >= {MATERIAL_SHARE_PCT}%",
                 observed=f"{row['origin_name']} {row['compare_periods']} 单价 {unit_value:,.0f} 美元，全部来源地 {world_unit:,.0f} 美元（{ratio:.2f} 倍）",
                 question=f"{row['origin_name']} 单价和全部来源地差 {ratio:.2f} 倍：是同类货的价格不同，还是编码里的货不一样？编码以下的资料才能回答。",
                 **common)

    rate, reason = base_rate(drilldown_menu)
    if rate is None:
        skipped["duty_below_base_rate"] = reason
    elif rate > 0:
        for _, row in origins.iterrows():
            realized = _number(row["compare_realized_duty_pct"])
            if _number(row["compare_share_pct"]) >= MATERIAL_SHARE_PCT and realized < rate - BASE_RATE_GAP_PP:
                flag("duty_below_base_rate", comparison=row["comparison"], periods=_sides(row), origin=row["origin_name"],
                     value=realized, reference=rate, threshold=f"realized duty < base rate - {BASE_RATE_GAP_PP} pp",
                     observed=f"{row['origin_name']} {row['compare_periods']} 实际税率 {realized:.2f}%，HTS8 普通税率 {rate:g}%",
                     timeline="", source="tables/origin_change.csv; tables/drilldown_menu.csv",
                     question=f"{row['origin_name']} 实际税率低于普通税率：是优惠项目（FTA、GSP）还是豁免？DataWeb 按 import program 分项能回答。")

    months = _Months(display, focal_origin)
    checked = [origin for origin in dict.fromkeys([focal_origin, *highlight_origins]) if origin != OTHER_ORIGINS]
    timeline_rows = list(timeline or [])
    timeline = [row for row in timeline_rows if _text(row.get("coverage")) == "applies"]
    if not timeline_rows:
        skipped["timeline_rules"] = "the study has no [timeline]"
    elif not timeline:
        skipped["timeline_rules"] = "the study has no verified coverage=applies tariff rows"
    elif not months.periods:
        skipped["timeline_rules"] = "the study has no monthly data"
    for row in timeline:
        level = rate_level(row.get("rate_change"))
        day = pd.to_datetime(row.get("date"), errors="coerce")
        if row.get("date_type") != "effective" or level is None or pd.isna(day) or _text(row.get("applies_to")):
            continue
        day = day.date()
        after = [_month(day, offset) for offset in range(0 if day.day == 1 else 1, (0 if day.day == 1 else 1) + SURGE_MONTHS)]
        expected = (rate or 0.0) + level
        for origin in named_origins(row.get("origins"), checked):
            totals = months.totals(origin, after)
            if totals is None:
                continue
            value, duty, world = totals
            realized = _number(safe_divide(duty, value)) * 100
            share = _number(safe_divide(value, world)) * 100
            if share >= MATERIAL_SHARE_PCT and realized < TIMELINE_DUTY_FRACTION * expected:
                flag("duty_below_timeline", comparison="", periods=f"{after[0]}..{after[-1]}", origin=origin,
                     value=realized, reference=expected,
                     threshold=f"realized duty < {TIMELINE_DUTY_FRACTION:g} x (base rate {rate or 0:g}% + row rate {level:g}%)",
                     observed=f"{row['date']} 起 {_text(row.get('measure'))}（{_text(row.get('rate_change'))}），"
                              f"{origin} {after[0]}..{after[-1]} 实际税率 {realized:.1f}%",
                     timeline=_timeline_label(row), source="tables/display_series.csv; tables/tariff_timeline.csv",
                     question=f"这一行适用于本研究的编码和 {origin} 吗？有没有例外或豁免？" + ("（这一行待核对）" if row.get("needs_check") else ""))

    windows: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for row in timeline:
        day = pd.to_datetime(row.get("date"), errors="coerce")
        if pd.isna(day):
            continue
        before = tuple(_month(day.date(), offset) for offset in range(-SURGE_MONTHS, 0))
        windows.setdefault(before, []).append(row)
    for before, members in windows.items():
        earlier = [str(pd.Period(month, freq="M") - 12) for month in before]
        for origin in (focal_origin, OTHERS, WORLD):
            now, then = months.totals(origin, before), months.totals(origin, earlier)
            if now is None or then is None:
                continue
            growth = _number(safe_divide(now[0], then[0]) - 1) * 100
            share = _number(safe_divide(now[0], now[2])) * 100
            if growth >= SURGE_PCT and share >= MATERIAL_SHARE_PCT:
                dates = "、".join(sorted({str(item["date"]) for item in members}))
                flag("surge_before_timeline_date", comparison="", periods=f"{earlier[0]}..{earlier[-1]} -> {before[0]}..{before[-1]}",
                     origin=origin, value=now[0], reference=then[0],
                     threshold=f"value in the {SURGE_MONTHS} months before the date >= {SURGE_PCT:g}% above a year earlier",
                     observed=f"{origin} {before[0]}..{before[-1]} 进口额比上年同期多 {growth:.0f}%（{dates} 之前）",
                     timeline="; ".join(_timeline_label(item) for item in members),
                     source="tables/display_series.csv; tables/tariff_timeline.csv",
                     question="这段增加和这些日期有关吗？DataWeb 只记进口月份，要订单或合同日期才能回答。")

    if quantity_check is None:
        skipped["quantity_conflict"] = 'the study does not set [quantity] unit = "both"'
    else:
        for _, row in quantity_check[quantity_check["conflict"].astype(bool)].iterrows():
            flag("quantity_conflict", comparison=row["comparison"], periods=_sides(row), origin=f"{row['level']}: {row['key']}",
                 value=_number(row["quantity_2_growth_pct"]), reference=_number(row["quantity_growth_pct"]),
                 threshold="first and second unit growth of opposite signs, both >= 5%",
                 observed=f"{row['key']} 按 {row['first_unit']} {_number(row['quantity_growth_pct']):+.1f}%，"
                          f"按 {row['second_unit']} {_number(row['quantity_2_growth_pct']):+.1f}%",
                 timeline="", source="tables/quantity_check.csv",
                 question="两种单位方向相反：每件的重量变了吗？哪种单位更能代表这类货？")
    return pd.DataFrame(rows, columns=list(ANOMALY_COLUMNS)), skipped


def build_mention_list(
    changes: pd.DataFrame,
    *,
    focal_origin: str,
    timeline: Sequence[dict[str, Any]] | None = None,
    continuity_verified: bool = True,
) -> pd.DataFrame:
    """Items the brief must mention; ``keywords`` (";"-separated) are what brief-check looks for."""

    reasons: dict[str, list[str]] = {}
    items: list[dict[str, Any]] = []
    origins = changes[changes["origin_name"].ne(WORLD)] if not changes.empty else changes
    for _, row in origins.iterrows():
        share, contribution = _number(row["share_change_pp"]), _number(row["contribution_pp"])
        found = []
        if abs(share) >= MENTION_SHARE_PP:
            found.append(f"share {_number(row['base_share_pct']):.1f}% -> {_number(row['compare_share_pct']):.1f}% ({share:+.1f} pp)")
        if abs(contribution) >= MENTION_CONTRIBUTION_PP:
            found.append(f"contribution {contribution:+.1f} pp")
        if found:
            reasons.setdefault(row["origin_name"], []).append(f"{row['comparison']}: " + ", ".join(found))
    if not changes.empty:
        comparisons = list(dict.fromkeys(changes["comparison"]))
        latest = next((name for name in comparisons if name.startswith("ytd_")), comparisons[-1])
        top = origins[origins["comparison"].eq(latest)].sort_values("compare_value_usd", ascending=False).head(MENTION_TOP)
        for rank, (_, row) in enumerate(top.iterrows(), start=1):
            reasons.setdefault(row["origin_name"], []).append(f"{latest}: top {rank} by value ({_number(row['compare_share_pct']):.1f}%)")
    for origin, found in reasons.items():
        items.append({"item": origin, "keywords": origin, "reason": "; ".join(found), "source": "tables/origin_change.csv"})
    for _, row in origins[origins["origin_name"].eq(focal_origin)].iterrows():
        base, compare = _number(row["base_realized_duty_pct"]), _number(row["compare_realized_duty_pct"])
        if np.isfinite(base) and np.isfinite(compare) and abs(compare - base) >= MENTION_DUTY_PP:
            items.append({"item": f"realized duty: {focal_origin} ({row['comparison']})", "keywords": "实际税率;realized duty",
                          "reason": f"{base:.1f}% -> {compare:.1f}% ({compare - base:+.1f} pp >= {MENTION_DUTY_PP:g} pp)",
                          "source": "tables/origin_change.csv"})
    rows = list(timeline or [])
    unsourced = sum(1 for row in rows if row.get("needs_check"))
    if unsourced:
        items.append({"item": "timeline rows without a verified primary source", "keywords": "来源待核实;待核对;to verify;unverified",
                      "reason": f"{unsourced} rows without a verified primary source",
                      "source": "tables/tariff_timeline.csv"})
    if not continuity_verified:
        items.append({"item": "HTS continuity not verified", "keywords": "连续性未核实;continuity",
                      "reason": "the scope was approved with HTS continuity not verified", "source": "analysis_manifest.json"})
    return pd.DataFrame(items, columns=list(MENTION_COLUMNS))


def turning_points(values: Sequence[float], epsilon: float) -> list[int]:
    """Douglas-Peucker on a path: indexes kept when a point is more than ``epsilon`` (vertically) off the chord."""

    keep = {0, len(values) - 1} if values else set()
    stack = [(0, len(values) - 1)] if len(values) > 2 else []
    while stack:
        first, last = stack.pop()
        best, index = 0.0, None
        for position in range(first + 1, last):
            chord = values[first] + (values[last] - values[first]) * (position - first) / (last - first)
            distance = abs(values[position] - chord)
            if distance > best:
                best, index = distance, position
        if index is not None and best > epsilon:
            keep.add(index)
            stack += [(first, index), (index, last)]
    return sorted(keep)


def build_window_candidates(
    display: pd.DataFrame,
    *,
    focal_origin: str,
    study_windows: Iterable[tuple[str, str]] = (),
    epsilon: float = CANDIDATE_EPSILON_PP,
    min_change: float = CANDIDATE_MIN_CHANGE_PP,
) -> pd.DataFrame:
    """Turns of the focal monthly share path as ``[decomposition] windows`` candidates.

    One-month moves that reverse the next month (by at least half) are
    ``spike`` rows: the same calendar month spiking in several years is a
    seasonal pattern, so they are not suggested as windows.  A short move
    (``SEASONAL_MAX_MONTHS`` or fewer) into a calendar month where a short
    move of the same sign also ends in ``SEASONAL_OTHER_YEARS`` other years is
    a ``seasonal`` row, not suggested either: the month-to-month rows of
    ``share_decomposition`` already hold it.
    """

    monthly = display[display["frequency"].eq("monthly")].copy()
    if monthly.empty:
        return pd.DataFrame(columns=list(CANDIDATE_COLUMNS))
    monthly["period"] = monthly["period"].astype(str)
    world = monthly.drop_duplicates("period").set_index("period")["world_value_usd"].astype(float).sort_index()
    focal = monthly[monthly["display_origin"].eq(focal_origin)].set_index("period")["customs_value_usd"].astype(float)
    share = (focal.reindex(world.index).fillna(0.0) / world.replace(0, np.nan) * 100).dropna()
    periods, values = list(share.index), [float(value) for value in share]

    def ordinal(period: str) -> int:
        return int(period[:4]) * 12 + int(period[5:7])

    points = turning_points(values, epsilon)
    windows = list(zip(points, points[1:]))
    spikes: dict[int, str] = {}  # window index -> the spike's peak month
    for index in range(len(windows) - 1):
        (a, b), (c, d) = windows[index], windows[index + 1]
        first, second = values[b] - values[a], values[d] - values[c]
        one_month = ordinal(periods[b]) - ordinal(periods[a]) == 1 and ordinal(periods[d]) - ordinal(periods[c]) == 1
        if one_month and first * second < 0 and abs(second) >= 0.5 * abs(first):
            spikes[index] = spikes[index + 1] = periods[b]
    peaks = set(spikes.values())
    # Short moves by (calendar month of their end, sign) -> years: a turn that recurs is seasonal.
    short: dict[tuple[str, bool], set[str]] = {}
    for a, b in windows:
        if ordinal(periods[b]) - ordinal(periods[a]) <= SEASONAL_MAX_MONTHS and values[b] != values[a]:
            short.setdefault((periods[b][5:], values[b] > values[a]), set()).add(periods[b][:4])
    inside = {tuple(window) for window in study_windows}
    rows: list[dict[str, Any]] = []
    for index, (a, b) in enumerate(windows):
        start, end = periods[a], periods[b]
        change = values[b] - values[a]
        spike = index in spikes
        seasonal = False
        notes = []
        if spike:
            peak = spikes[index]
            same = sorted(month for month in peaks if month[5:] == peak[5:] and month != peak)
            notes.append(f"one-month spike at {peak}" + (f"; same month spiked in {', '.join(same)}" if same else ""))
        elif ordinal(end) - ordinal(start) <= SEASONAL_MAX_MONTHS and change:
            others = sorted(short.get((end[5:], change > 0), set()) - {end[:4]})
            if len(others) >= SEASONAL_OTHER_YEARS:
                seasonal = True
                notes.append(f"{'rise' if change > 0 else 'fall'} into month {end[5:]} recurs in {', '.join(others)}")
        if ordinal(end) - ordinal(start) != b - a:
            notes.append("months missing inside the window")
        note = "; ".join(notes)
        rows.append({
            "start": start, "end": end, "months": ordinal(end) - ordinal(start),
            "start_share_pct": values[a], "end_share_pct": values[b], "share_change_pp": change,
            "kind": "spike" if spike else ("seasonal" if seasonal else "turn"),
            "suggested": not (spike or seasonal) and abs(change) >= min_change,
            "in_study": (start, end) in inside,
            "note": note,
            "toml": f'["{start}", "{end}"]',
        })
    return pd.DataFrame(rows, columns=list(CANDIDATE_COLUMNS))
