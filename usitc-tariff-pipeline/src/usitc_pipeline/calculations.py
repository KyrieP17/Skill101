"""``[[derived]]`` numbers -> ``tables/calculations.csv``.

A brief sometimes needs a number no standard table holds: a quarter's total,
a share over January to August, one month against the monthly mean of
others, the lowest and highest month of a range.  The agent must not work
these out in chat.  It declares them in the study::

    [[derived]]
    id = "q1-total"
    label = "Q1 imports, 2025 against 2024"
    origin = ["World", "India", "China"]     # an origin, "World", "Other origins"
                                             # (World minus the focal origin) or
                                             # "Other reported origins" (not focal or highlighted)
    measure = "value"                        # value, quantity, duty, share, quantity_share,
                                             # unit_value, realized_duty (+ _2 second-unit measures)
    stat = "total"                           # total (default), mean, min, max over the periods
    window = ["2025-01", "2025-03"]          # "YYYY-MM", "YYYY" or [first, last]
    base = ["2024-01", "2024-03"]            # optional, same kind as window

and the pipeline writes one row per origin, measure and stat, with the
formula and the sums it used, so every number can be recomputed by hand from
``tables/origin_series.csv``.

* ``total`` sums the window first, then takes the measure (a share is total
  value over total World value).
* ``mean`` takes the measure in each period, then the mean of the periods
  that have one (for value: the monthly mean).
* ``min`` / ``max`` take the measure in each period; ``at`` names the period.

A window with an unobserved period (or an annual period that is not a full
year) gets no value and says why in ``note``.
"""

from __future__ import annotations

import difflib
from typing import Any, Iterable, Sequence

import numpy as np
import pandas as pd

from .analysis import OTHER_ORIGINS


WORLD = "World"
OTHERS = "Other origins"
SOURCE = "tables/origin_series.csv"
# measure: (numerator, denominator or None, scale, unit kind)
MEASURES: dict[str, tuple[str, str | None, int, str]] = {
    "value": ("customs_value_usd", None, 1, "usd"),
    "quantity": ("quantity", None, 1, "quantity"),
    "duty": ("calculated_duty_usd", None, 1, "usd"),
    "share": ("customs_value_usd", "world_value_usd", 100, "pct"),
    "quantity_share": ("quantity", "world_quantity", 100, "pct"),
    "unit_value": ("customs_value_usd", "quantity", 1, "unit_value"),
    "realized_duty": ("calculated_duty_usd", "customs_value_usd", 100, "pct"),
    "quantity_2": ("quantity_2", None, 1, "quantity_2"),
    "quantity_2_share": ("quantity_2", "world_quantity_2", 100, "pct"),
    "unit_value_2": ("customs_value_usd", "quantity_2", 1, "unit_value_2"),
}
CALCULATION_COLUMNS = (
    "id", "label", "origin", "measure", "stat", "frequency", "window", "base", "unit",
    "value", "base_value", "change", "change_unit", "growth_pct", "ratio", "at", "base_at",
    "formula", "components", "base_components", "source", "note",
)


class CalculationError(ValueError):
    """A ``[[derived]]`` entry names something the data does not have."""


def _fmt(value: Any) -> str:
    if value is None or not np.isfinite(value):
        return "nan"
    value = float(value)
    return f"{value:.0f}" if value.is_integer() else f"{value:.10g}"


def _expected(frequency: str, first: str, last: str) -> list[str]:
    if frequency == "annual":
        return [str(year) for year in range(int(first), int(last) + 1)]
    return [str(period) for period in pd.period_range(first, last, freq="M")]


def _label(first: str, last: str) -> str:
    return first if first == last else f"{first}..{last}"


class _Series:
    """Per-period values of every derived origin, for one frequency."""

    def __init__(self, origin_series: pd.DataFrame, frequency: str, *, focal: str, highlights: Sequence[str]) -> None:
        rows = origin_series[origin_series["frequency"].eq(frequency)].copy()
        rows["period"] = rows["period"].astype(str)
        self.frequency = frequency
        self.second = "quantity_2" in rows
        self.quantity_columns = ["quantity", "quantity_2"] if self.second else ["quantity"]
        world = rows.drop_duplicates("period").set_index("period").sort_index()
        self.status = world["period_status"]
        self.world = pd.DataFrame(
            {
                "customs_value_usd": world["world_value_usd"],
                "quantity": world["world_quantity"],
                "calculated_duty_usd": world["world_calculated_duty_usd"],
            }
        )
        if self.second:
            self.world["quantity_2"] = world["world_quantity_2"]
        self.rows = rows
        self.focal = focal
        self.keep = {focal, *highlights}
        self.names = set(rows["origin_name"])

    def frame(self, origin: str) -> pd.DataFrame:
        """customs_value_usd, quantity, calculated_duty_usd (, quantity_2) and the World columns."""

        if origin == WORLD:
            own = self.world.copy()
        elif origin == OTHERS:
            own = self.world - self._sum(self.rows["origin_name"].eq(self.focal))
        elif origin == OTHER_ORIGINS:
            own = self._sum(~self.rows["origin_name"].isin(self.keep))
        else:
            own = self._sum(self.rows["origin_name"].eq(origin))
        frame = own.copy()
        frame["world_value_usd"] = self.world["customs_value_usd"]
        frame["world_quantity"] = self.world["quantity"]
        if self.second:
            frame["world_quantity_2"] = self.world["quantity_2"]
        return frame

    def _sum(self, mask: pd.Series) -> pd.DataFrame:
        columns = ["customs_value_usd", *self.quantity_columns, "calculated_duty_usd"]
        summed = self.rows[mask].groupby("period")[columns].sum(min_count=1).reindex(self.world.index)
        # An origin with no row in a period imported nothing then; quantity
        # stays blank where the period's units are not comparable.
        summed[["customs_value_usd", "calculated_duty_usd"]] = summed[["customs_value_usd", "calculated_duty_usd"]].fillna(0.0)
        for column in self.quantity_columns:
            summed[column] = summed[column].where(self.world[column].isna(), summed[column].fillna(0.0))
        return summed

    def definition(self, origin: str) -> str:
        if origin == OTHERS:
            return f"Other origins = World - {self.focal}; "
        if origin == OTHER_ORIGINS:
            return f"{OTHER_ORIGINS} = every origin except {', '.join(sorted(self.keep))}; "
        return ""


def _evaluate(series: _Series, frame: pd.DataFrame, periods: list[str], measure: str, stat: str) -> dict[str, Any]:
    """The measure over ``periods``: value, the period it occurs at (min/max), components and a note."""

    numerator, denominator, scale, _ = MEASURES[measure]
    missing = [period for period in periods if period not in frame.index]
    partial = [period for period in periods if period in frame.index and series.frequency == "annual"
               and series.status.get(period) != "full_year"]
    if missing or partial:
        reasons = ([f"not observed: {', '.join(missing)}"] if missing else []) + (
            [f"not a full year: {', '.join(partial)}"] if partial else []
        )
        return {"value": float("nan"), "at": None, "components": "", "note": "; ".join(reasons)}
    window = frame.loc[periods]
    columns = [numerator] + ([denominator] if denominator else [])
    blank = [period for period in periods if window.loc[period, columns].isna().any()]
    if blank:
        return {"value": float("nan"), "at": None, "components": "",
                "note": f"quantity not comparable in {', '.join(blank)}"}
    if stat == "total":
        top = float(window[numerator].sum())
        bottom = float(window[denominator].sum()) if denominator else None
        value = top if bottom is None else (top / bottom * scale if bottom else float("nan"))
        parts = f"{numerator}={_fmt(top)}" + (f", {denominator}={_fmt(bottom)}" if denominator else "")
        return {"value": value, "at": None, "components": f"{parts} ({len(periods)} periods)",
                "note": "" if np.isfinite(value) else f"{denominator} is 0"}
    per_period = window[numerator].astype(float)
    if denominator:
        per_period = per_period / window[denominator].astype(float).replace(0, np.nan) * scale
    defined = per_period.dropna()
    note = "" if len(defined) == len(periods) else f"{len(defined)} of {len(periods)} periods have a value"
    if defined.empty:
        return {"value": float("nan"), "at": None, "components": "", "note": note or "no value"}
    if stat == "mean":
        return {"value": float(defined.mean()), "at": None, "note": note,
                "components": f"n={len(defined)}, sum of per-period values={_fmt(defined.sum())}"}
    at = defined.idxmin() if stat == "min" else defined.idxmax()
    parts = f"{numerator}={_fmt(window.loc[at, numerator])}"
    if denominator:
        parts += f", {denominator}={_fmt(window.loc[at, denominator])}"
    return {"value": float(defined[at]), "at": at, "components": f"at {at}: {parts}", "note": note}


def _formula(series: _Series, origin: str, measure: str, stat: str, has_base: bool) -> str:
    numerator, denominator, scale, kind = MEASURES[measure]
    per = numerator if denominator is None else f"{numerator} / {denominator}" + (" x 100" if scale == 100 else "")
    if stat == "total":
        text = f"sum of {numerator} over the window" if denominator is None else (
            f"sum({numerator}) / sum({denominator})" + (" x 100" if scale == 100 else "")
        )
    else:
        text = f"{stat} over the periods of ({per})"
    if has_base:
        text += "; change = value - base_value"
        if kind != "pct":
            text += "; growth_pct = (value / base_value - 1) x 100; ratio = value / base_value"
    return series.definition(origin) + text


def _unit(measure: str, quantity_unit: str | None, second_unit: str | None) -> str:
    kind = MEASURES[measure][3]
    return {
        "usd": "USD",
        "quantity": quantity_unit or "",
        "quantity_2": second_unit or "",
        "pct": "pct",
        "unit_value": f"USD per {quantity_unit}" if quantity_unit else "USD per unit",
        "unit_value_2": f"USD per {second_unit}" if second_unit else "USD per unit",
    }[kind]


def build_calculations(
    origin_series: pd.DataFrame,
    derived: Iterable[Any],
    *,
    focal_origin: str,
    highlight_origins: Sequence[str] = (),
    quantity_unit: str | None = None,
    second_unit: str | None = None,
) -> pd.DataFrame:
    """One row per ``[[derived]]`` entry, origin, measure and stat."""

    cache: dict[str, _Series] = {}
    rows: list[dict[str, Any]] = []
    for item in derived:
        if item.frequency not in cache:
            cache[item.frequency] = _Series(origin_series, item.frequency, focal=focal_origin, highlights=highlight_origins)
        series = cache[item.frequency]
        known = series.names | {WORLD, OTHERS, OTHER_ORIGINS}
        for origin in item.origins:
            if origin not in known:
                close = difflib.get_close_matches(origin, sorted(known), n=3)
                hint = f"; did you mean {close}?" if close else ""
                raise CalculationError(f"derived {item.derived_id!r}: origin {origin!r} is not in the data{hint}")
        if any(measure in ("quantity_2", "quantity_2_share", "unit_value_2") for measure in item.measures) and not series.second:
            raise CalculationError(f"derived {item.derived_id!r}: the study has no second unit beside the first")
        window = _expected(item.frequency, *item.window)
        base = _expected(item.frequency, *item.base) if item.base else None
        for origin in item.origins:
            frame = series.frame(origin)
            for measure in item.measures:
                pct = MEASURES[measure][3] == "pct"
                unit = _unit(measure, quantity_unit, second_unit)
                for stat in item.stats:
                    current = _evaluate(series, frame, window, measure, stat)
                    row: dict[str, Any] = {
                        "id": item.derived_id,
                        "label": item.label,
                        "origin": origin,
                        "measure": measure,
                        "stat": stat,
                        "frequency": item.frequency,
                        "window": _label(*item.window),
                        "base": _label(*item.base) if item.base else "",
                        "unit": unit,
                        "value": current["value"],
                        "at": current["at"],
                        "formula": _formula(series, origin, measure, stat, base is not None),
                        "components": current["components"],
                        "source": SOURCE,
                    }
                    notes = [current["note"]] if current["note"] else []
                    if base is not None:
                        previous = _evaluate(series, frame, base, measure, stat)
                        value, base_value = current["value"], previous["value"]
                        row.update(
                            base_value=base_value,
                            base_at=previous["at"],
                            base_components=previous["components"],
                            change=value - base_value,
                            change_unit="pp" if pct else unit,
                        )
                        if not pct:
                            usable = np.isfinite(value) and np.isfinite(base_value) and base_value != 0
                            row["growth_pct"] = (value / base_value - 1) * 100 if usable else float("nan")
                            row["ratio"] = value / base_value if usable else float("nan")
                        if previous["note"]:
                            notes.append(f"base: {previous['note']}")
                    row["note"] = "; ".join(notes)
                    rows.append(row)
    return pd.DataFrame(rows, columns=list(CALCULATION_COLUMNS))
