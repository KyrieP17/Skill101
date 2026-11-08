"""Chart rendering from already-computed research tables.

Charts do not calculate business metrics.  Each function writes the exact
plotted DataFrame to a same-stem CSV before saving the PNG, so a reviewer can
audit the visual without reverse-engineering pixels.  Titles, units, product
names and origins come from the approved scope and the study.

Colors follow the entity, never its rank: the focal origin takes categorical
slot 1 and highlighted origins take slots 2-7 in study order, in every chart.
The seven slots were checked with the dataviz palette validator (adjacent CVD
dE >= 9.1, normal-vision dE >= 19.6 on the light surface); three are below 3:1
contrast, so every chart keeps a legend and a chart-data table.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import textwrap
from typing import Any, Iterable

import numpy as np
import pandas as pd

from .study import MAX_HIGHLIGHTS


SLOTS = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6")
SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
INFLOW = "#2a78d6"
OUTFLOW = "#e34948"
OTHER_ORIGINS = "Other reported origins"
assert len(SLOTS) == MAX_HIGHLIGHTS + 1  # focal + highlights, no cycling


@dataclass(frozen=True)
class ChartLabels:
    """Display metadata taken from the scope and study, never from code."""

    product: str
    title: str
    focal_origin: str
    highlight_origins: tuple[str, ...] = ()
    quantity_unit: str | None = None
    source: str = "Source: USITC DataWeb, U.S. imports for consumption."

    def color(self, origin: str) -> str:
        palette = dict(zip((self.focal_origin, *self.highlight_origins), SLOTS))
        return {**palette, OTHER_ORIGINS: INK_MUTED, "World": INK}.get(origin, INK_MUTED)


def _plt():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - environment-specific message
        raise RuntimeError("matplotlib is required only for the `charts` stage") from exc
    plt.rcParams.update(
        {
            "font.family": "sans-serif",
            "font.sans-serif": ["Segoe UI", "Helvetica", "Arial", "DejaVu Sans"],
            "figure.facecolor": SURFACE,
            "axes.facecolor": SURFACE,
            "savefig.facecolor": SURFACE,
            "axes.edgecolor": AXIS,
            "axes.linewidth": 0.8,
            "axes.labelcolor": INK_SECONDARY,
            "axes.titlecolor": INK,
            "axes.titlesize": 13,
            "axes.titleweight": "semibold",
            "axes.labelsize": 9.5,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "axes.grid": True,
            "grid.color": GRID,
            "grid.linewidth": 0.6,
            "grid.linestyle": "-",
            "xtick.color": INK_MUTED,
            "ytick.color": INK_MUTED,
            "xtick.labelcolor": INK_SECONDARY,
            "ytick.labelcolor": INK_SECONDARY,
            "legend.frameon": False,
            "legend.fontsize": 8.5,
            "lines.linewidth": 1.6,
            "lines.solid_capstyle": "round",
            "lines.solid_joinstyle": "round",
            "figure.dpi": 110,
            "savefig.dpi": 200,
        }
    )
    return plt


def _prepare_output(output_dir: str | Path, stem: str, data: pd.DataFrame) -> tuple[Path, Path]:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    png = directory / f"{stem}.png"
    csv = directory / f"{stem}.csv"
    data.to_csv(csv, index=False)
    return png, csv


def _finish(fig, png: Path, source: str, *, footer: Iterable[str] = ()) -> Path:
    """Source line at the bottom; optional key lines above it (no text over data)."""

    from matplotlib.ticker import MaxNLocator

    for ax in fig.axes:
        low, high = ax.get_xlim()
        if 1900 <= low and high <= 2100:  # a numeric year axis (date axes count days, far above 2100)
            ax.xaxis.set_major_locator(MaxNLocator(integer=True))  # no 2022.5 ticks on a few annual points
    width, height = fig.get_size_inches()
    lines = [part for line in (*footer, source) for part in textwrap.wrap(line, width=int(width * 18))]
    fig.text(0.01, 0.01, "\n".join(lines), ha="left", va="bottom", fontsize=7.2, color=INK_MUTED, linespacing=1.5)
    fig.tight_layout(rect=(0, min(0.4, (0.16 + 0.155 * len(lines)) / height), 1, 1))
    fig.savefig(png)
    import matplotlib.pyplot as plt

    plt.close(fig)
    return png


MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def window_label(text: str) -> str:
    """'2024' -> '2024'; '2025-01..2025-08' -> 'Jan-Aug 2025'; other ranges keep their ends."""

    start, _, end = str(text).partition("..")
    end = end or start
    if len(start) == 7 and start[:4] == end[:4]:
        months = MONTHS[int(start[5:7]) - 1] + ("" if start == end else "\u2013" + MONTHS[int(end[5:7]) - 1])
        return f"{months} {start[:4]}"
    return start if start == end else f"{start}\u2013{end}"


def _chunk(entries: list[str], width: int = 170) -> list[str]:
    lines: list[str] = []
    for entry in entries:
        if lines and len(lines[-1]) + 4 + len(entry) <= width:
            lines[-1] += "    " + entry
        else:
            lines.append(entry)
    return lines


def _peak(values: Iterable[float]) -> float:
    array = np.abs(np.asarray(list(values), dtype=float))
    return float(np.nanmax(array)) if np.isfinite(array).any() else 0.0


def _usd_scale(values: Iterable[float]) -> tuple[float, str]:
    peak = _peak(values)
    return next(((factor, label) for factor, label in ((1e9, "$bn"), (1e6, "$mn"), (1e3, "$k")) if peak >= factor), (1.0, "$"))


def _count_scale(values: Iterable[float]) -> tuple[float, str]:
    peak = _peak(values)
    scales = ((1e9, "billion"), (1e6, "million"), (1e3, "thousand"))
    return next(((factor, label) for factor, label in scales if peak >= factor), (1.0, ""))


def _x_values(frame: pd.DataFrame) -> pd.Series:
    if frame["frequency"].iloc[0] == "monthly":
        return pd.to_datetime(frame["period"].astype(str) + "-01")
    return pd.to_numeric(frame["period"].astype(str).str[:4])


def _pick_frequency(frame: pd.DataFrame, *, min_annual: int = 3) -> pd.DataFrame:
    """Full annual years when there are enough, otherwise months.

    A partial year (a YTD sum under the plain year label) stays out of annual
    lines so it cannot read as a collapse; the YTD comparison covers it.
    """

    annual = frame[frame["frequency"].eq("annual")]
    if "period_status" in annual.columns:
        annual = annual[annual["period_status"].eq("full_year")]
    if annual["period"].nunique() >= min_annual or frame["frequency"].eq("monthly").sum() == 0:
        return annual.copy()
    return frame[frame["frequency"].eq("monthly")].copy()


def _text(value: Any) -> str:
    """A CSV cell as text: blank for NaN, integers without '.0' (codes read back as numbers)."""

    if value is None or (isinstance(value, float) and value != value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _timeline_lines(axes, timeline: pd.DataFrame | None, *, frequency: str, x_min, x_max, label_axis=None) -> tuple[list[str], pd.DataFrame]:
    """Vertical tariff-timeline lines: effective dates dark and solid, announced dates light and dashed.

    Lines are numbered T1, T2, ... above ``label_axis`` and spelled out in the
    returned key, so long measure names never sit on the data.  Returns the
    key lines and the plotted rows (written as ``<stem>.timeline.csv``).
    """

    if timeline is None or timeline.empty:
        return [], pd.DataFrame()
    rows = timeline.copy()
    dates = pd.to_datetime(rows["date"], errors="coerce")
    if frequency == "monthly":
        rows["x"] = dates
    else:
        rows["x"] = dates.dt.year + (dates.dt.dayofyear - 1) / 365.25
    rows = rows[rows["x"].between(x_min, x_max)].sort_values("x", kind="stable").reset_index(drop=True)
    if rows.empty:
        return [], rows
    rows.insert(0, "number", [f"T{index}" for index in range(1, len(rows) + 1)])
    label_axis = label_axis or axes[0]
    positions = (
        rows["x"].dt.year + rows["x"].dt.dayofyear / 365.25 if frequency == "monthly" else rows["x"]
    ).astype(float).tolist()
    # Lines closer than 1/40 of the plotted span share one label ("T6-T12").
    span = max(float(x_max.year - x_min.year + 1) if frequency == "monthly" else float(x_max - x_min), 1.0)
    groups: list[list[int]] = []
    for index, position in enumerate(positions):
        if groups and position - positions[groups[-1][0]] < span / 40:
            groups[-1].append(index)
        else:
            groups.append([index])
    for number, group in enumerate(groups):
        first, last = rows.loc[group[0], "number"], rows.loc[group[-1], "number"]
        label_axis.text(rows.loc[group[0], "x"], 1.01 + 0.045 * (number % 2), first if first == last else f"{first}–{last}",
                        transform=label_axis.get_xaxis_transform(), va="bottom", ha="left" if len(group) > 1 else "center",
                        fontsize=7, color=INK_SECONDARY)
    key = []
    for row in rows.itertuples(index=False):
        effective = str(row.date_type) == "effective"
        for axis in axes:
            axis.axvline(row.x, color=INK_SECONDARY if effective else AXIS, linewidth=0.9,
                         linestyle="-" if effective else (0, (3, 2)), zorder=1)
        scope = _text(row.applies_to) or "whole scope"
        origins = _text(row.origins) or "all origins"
        rate = f" {_text(row.rate_change)}" if _text(row.rate_change) else ""
        check = "" if bool(row.primary_source) else " [to verify]"
        key.append(f"{row.number} {row.date} {row.date_type}: {row.measure}{rate} ({origins}; {scope}){check}")
    return key, rows.drop(columns=["x"])


def _end_label(ax, x, y, text: str) -> None:
    ax.annotate(text, (x, y), xytext=(4, 0), textcoords="offset points", va="center", fontsize=8, color=INK_SECONDARY)


def plot_scope_trend(scope_series: pd.DataFrame, output_dir: str | Path, labels: ChartLabels) -> Path:
    """Total imports in value and, when units are comparable, in quantity."""

    data = _pick_frequency(scope_series, min_annual=12)
    data = data[data["world_value_usd"].notna()].copy()
    if data.empty:
        raise ValueError("Cannot plot an empty scope series")
    quantity = data["quantity_comparable"].astype(bool).all() and data["world_quantity"].notna().any()
    plotted = data[["frequency", "period", "world_value_usd", "world_quantity", "world_unit_value_usd"]]
    png, _ = _prepare_output(output_dir, "01_scope_trend", plotted)
    plt = _plt()
    rows = 2 if quantity else 1
    fig, axes = plt.subplots(rows, 1, figsize=(10.5, 6.6 if quantity else 4.4), sharex=True, squeeze=False)
    x = _x_values(data)
    factor, unit = _usd_scale(data["world_value_usd"])
    axes[0][0].plot(x, data["world_value_usd"] / factor, color=INK, linewidth=1.8)
    axes[0][0].set_ylabel(f"Customs value ({unit})")
    axes[0][0].set_title(f"{labels.product}: total U.S. imports", loc="left")
    if quantity:
        qfactor, qunit = _count_scale(data["world_quantity"])
        axes[1][0].plot(x, data["world_quantity"] / qfactor, color=SLOTS[0], linewidth=1.8)
        axes[1][0].set_ylabel(f"Quantity ({' '.join(filter(None, [qunit, labels.quantity_unit or 'units']))})")
    note = "" if quantity else " Quantity is not shown: quantity units are not comparable across the scope."
    return _finish(fig, png, labels.source + note)


def plot_product_reallocation(summary: pd.DataFrame, output_dir: str | Path, labels: ChartLabels | None = None) -> Path:
    """Demand movement against the focal origin's share movement, one dot per HTS code."""

    if summary.empty:
        raise ValueError("Cannot plot an empty product-reallocation table")
    png, _ = _prepare_output(output_dir, "02_product_reallocation", summary)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10.2, 6.0))
    quantity_available = summary["world_import_quantity_growth_pct"].notna().any()
    x_column = "world_import_quantity_growth_pct" if quantity_available else "world_import_value_growth_pct"
    share_column = "focal_share_change_pp" if "focal_share_change_pp" in summary else "china_share_change_pp"
    focal = str(summary["focal_origin"].dropna().iloc[0]) if "focal_origin" in summary else "focal origin"
    plotted = summary[summary[x_column].notna()]
    ax.scatter(
        plotted[x_column],
        plotted[share_column],
        s=64,
        c=np.where(plotted[share_column] >= 0, INFLOW, OUTFLOW),
        edgecolor=SURFACE,
        linewidth=1.5,
        zorder=3,
    )
    for row in plotted.itertuples(index=False):
        ax.annotate(str(row.hts_code), (getattr(row, x_column), getattr(row, share_column)), xytext=(5, 5),
                    textcoords="offset points", fontsize=8, color=INK_SECONDARY)
    ax.axhline(0, color=AXIS, linewidth=0.8)
    ax.axvline(0, color=AXIS, linewidth=0.8)
    ax.set_xlabel("Change in total U.S. import " + ("quantity (%)" if quantity_available else "customs value (%)"))
    ax.set_ylabel(f"Change in {focal}'s import-value share (pp)")
    title = labels.product if labels else "Products in scope"
    ax.set_title(f"{title}: demand change vs {focal} share change by HTS code", loc="left")
    source = labels.source if labels else "Source: USITC DataWeb."
    return _finish(fig, png, source + " Quantity is used only when its units are comparable. Descriptive, not causal.")


def plot_study_event_path(
    monthly: pd.DataFrame,
    events: pd.DataFrame,
    output_dir: str | Path,
    *,
    title: str,
    focal_origin: str,
    product_name: str,
    highlight_origins: Iterable[str] = (),
    windows: Iterable[dict[str, Any]] = (),
    source: str | None = None,
    timeline: pd.DataFrame | None = None,
) -> Path:
    """Monthly origin shares with numbered event markers and shaded study windows.

    ``03_event_path.csv`` and ``03_event_path.events.csv`` are the frozen
    audit artifacts of the laptop regression; window shading is written to a
    separate ``03_event_path.windows.csv``.  Windows (W1, W2, ...) and events
    (1, 2, ...) are numbered on the chart and spelled out in the key below it,
    so long labels never collide with each other or with the data.
    """

    if monthly.empty:
        raise ValueError("Cannot plot an empty study event table")
    data = monthly.copy()
    data["period_date"] = pd.to_datetime(data["period_date"], errors="coerce")
    png, _ = _prepare_output(output_dir, "03_event_path", data)
    events.to_csv(Path(output_dir) / "03_event_path.events.csv", index=False)
    windows = list(windows)
    if windows:
        pd.DataFrame(windows).to_csv(Path(output_dir) / "03_event_path.windows.csv", index=False)
    highlights = tuple(highlight_origins) or tuple(
        origin for origin in data["display_origin"].drop_duplicates() if origin not in {focal_origin, OTHER_ORIGINS}
    )
    labels = ChartLabels(product=product_name, title=title, focal_origin=focal_origin, highlight_origins=highlights)

    plt = _plt()
    fig, (ax_share, ax_world) = plt.subplots(
        2, 1, figsize=(11.2, 8.0), sharex=True, gridspec_kw={"height_ratios": [1.7, 0.8]}
    )
    key: list[str] = []
    for number, item in enumerate(windows, start=1):
        start = pd.Timestamp(f"{item['start']}-01")
        end = pd.Timestamp(f"{item['end']}-01") + pd.offsets.MonthEnd(0)
        for axis in (ax_share, ax_world):
            axis.axvspan(start, end, color=INK_MUTED, alpha=0.08, linewidth=0)
        ax_share.text(start + (end - start) / 2, 1.0, f"W{number}", transform=ax_share.get_xaxis_transform(),
                      fontsize=7.5, color=INK_SECONDARY, va="bottom", ha="center")
        key.append(f"W{number} {item.get('label') or item.get('id')} ({item['start']} to {item['end']})")
    for origin in (focal_origin, *highlights):
        group = data[data["display_origin"].eq(origin)].sort_values("period_date")
        if group.empty:
            continue
        ax_share.plot(group["period_date"], group["value_share"] * 100, label=origin, color=labels.color(origin),
                      linewidth=2.4 if origin == focal_origin else 1.4)
    focal_rows = data[data["display_origin"].eq(focal_origin)].dropna(subset=["value_share"]).sort_values("period_date")
    if not focal_rows.empty:
        last = focal_rows.iloc[-1]
        _end_label(ax_share, last["period_date"], last["value_share"] * 100, f"{focal_origin} {last['value_share'] * 100:.1f}%")
    world = data[["period_date", "world_value_usd"]].drop_duplicates("period_date").sort_values("period_date")
    factor, unit = _usd_scale(world["world_value_usd"])
    ax_world.plot(world["period_date"], world["world_value_usd"] / factor, color=INK, linewidth=1.6)

    plotted_events = events.copy()
    plotted_events["date"] = pd.to_datetime(plotted_events["date"], errors="coerce")
    plotted_events = plotted_events[plotted_events["date"].between(data["period_date"].min(), data["period_date"].max())]
    previous, row_index, event_key = None, 0, []
    for number, row in enumerate(plotted_events.sort_values("date").itertuples(index=False), start=1):
        implemented = str(getattr(row, "status", "")).lower() == "implemented"
        for axis in (ax_share, ax_world):
            axis.axvline(row.date, color=INK_SECONDARY if implemented else AXIS, linewidth=0.8, zorder=1)
        row_index = (row_index + 1) % 3 if previous is not None and (row.date - previous).days < 120 else 0
        previous = row.date
        ax_share.text(row.date, 1.045 + 0.04 * row_index, str(number), transform=ax_share.get_xaxis_transform(),
                      va="bottom", ha="center", fontsize=7.5, color=INK_SECONDARY)
        event_key.append(f"{number} {row.date:%Y-%m-%d} {getattr(row, 'short_label', 'event')}")
    timeline_key, timeline_rows = _timeline_lines(
        (ax_share, ax_world), timeline, frequency="monthly",
        x_min=data["period_date"].min(), x_max=data["period_date"].max() + pd.offsets.MonthEnd(0),
    )
    if not timeline_rows.empty:
        timeline_rows.to_csv(Path(output_dir) / "03_event_path.timeline.csv", index=False)

    ax_share.set_ylabel("Share of U.S. import value (%)")
    ax_share.set_title(title, loc="left", pad=40)
    ax_share.legend(ncol=min(7, 1 + len(highlights)), loc="upper center", bbox_to_anchor=(0.5, -0.06))
    ax_world.set_ylabel(f"Total imports ({unit})")
    note = (source or f"Source: USITC DataWeb, {product_name}, U.S. imports for consumption.") + (
        " Dark event lines: implemented; light: announced or context." if event_key else ""
    ) + (
        " Tariff timeline: dark solid = effective date, light dashed = announced date." if timeline_key else ""
    ) + " Descriptive, not causal."
    return _finish(fig, png, note, footer=_chunk(key) + _chunk(event_key) + timeline_key)


def _origin_lines(
    data: pd.DataFrame,
    column: str,
    *,
    stem: str,
    title: str,
    ylabel: str,
    output_dir: str | Path,
    labels: ChartLabels,
    scale: float = 1.0,
    world: pd.DataFrame | None = None,
    height: float = 5.2,
):
    """Shared body of the per-origin line charts; returns (png, figure) for _finish."""

    plotted = data[["frequency", "period", "display_origin", column]].copy()
    if world is not None:
        plotted = pd.concat([plotted, world[["frequency", "period", "display_origin", column]]], ignore_index=True)
    png, _ = _prepare_output(output_dir, stem, plotted)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10.5, height))
    order = [labels.focal_origin, *labels.highlight_origins, OTHER_ORIGINS] + (["World"] if world is not None else [])
    for origin in order:
        group = plotted[plotted["display_origin"].eq(origin)]
        if group[column].isna().all():
            continue  # NaN months stay in the series, so masked months show as gaps
        ax.plot(_x_values(group), group[column] * scale, label=origin, color=labels.color(origin),
                linewidth=2.4 if origin in {labels.focal_origin, "World"} else 1.4)
    ax.set_title(title, loc="left")
    ax.set_ylabel(ylabel)
    ax.legend(ncol=4, loc="upper left")
    return png, fig


def plot_origin_shares(display: pd.DataFrame, output_dir: str | Path, labels: ChartLabels) -> Path:
    """Part-to-whole: every origin's value share, with non-highlighted origins folded together."""

    data = _pick_frequency(display)
    if data.empty:
        raise ValueError("Cannot plot empty display series")
    plotted = data[["frequency", "period", "display_origin", "customs_value_usd", "value_share"]]
    png, _ = _prepare_output(output_dir, "04_origin_shares", plotted)
    pivot = plotted.pivot_table(index="period", columns="display_origin", values="value_share", aggfunc="sum").fillna(0)
    order = [name for name in (labels.focal_origin, *labels.highlight_origins, OTHER_ORIGINS) if name in pivot.columns]
    pivot = pivot[order] * 100
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10.5, 5.4))
    x = _x_values(pd.DataFrame({"frequency": data["frequency"].iloc[0], "period": pivot.index}))
    ax.stackplot(x, [pivot[name] for name in order], labels=order, colors=[labels.color(name) for name in order],
                 alpha=0.85, edgecolor=SURFACE, linewidth=0.6)
    ax.set_ylim(0, 100)
    ax.set_ylabel("Share of U.S. import value (%)")
    ax.set_title(f"{labels.product}: share of import value by origin", loc="left")
    ax.legend(ncol=min(7, len(order)), loc="upper center", bbox_to_anchor=(0.5, -0.08))
    return _finish(fig, png, labels.source)


def plot_origin_change(changes: pd.DataFrame, output_dir: str | Path, labels: ChartLabels, *, stem: str, top: int = 12) -> Path:
    """Inflow/outflow: customs-value change by origin between two windows."""

    if changes.empty:
        raise ValueError("Cannot plot an empty comparison")
    world = changes[changes["origin_name"].eq("World")].iloc[0]
    origins = changes[changes["origin_name"].ne("World")].copy()
    origins = origins.reindex(origins["value_change_usd"].abs().sort_values(ascending=False).index).head(top)
    origins = origins.sort_values("value_change_usd")
    plotted = origins[["comparison", "origin_name", "base_value_usd", "compare_value_usd", "value_change_usd",
                       "share_change_pp", "direction"]]
    png, _ = _prepare_output(output_dir, stem, plotted)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10.2, 0.42 * len(plotted) + 2.0))
    factor, unit = _usd_scale(plotted["value_change_usd"])
    values = plotted["value_change_usd"] / factor
    bars = ax.barh(plotted["origin_name"], values, height=0.55,
                   color=np.where(values >= 0, INFLOW, OUTFLOW), edgecolor=SURFACE, linewidth=1.5)
    ax.bar_label(bars, labels=["\u22480" if round(value, 2) == 0 else f"{value:+,.2f}" for value in values],
                 padding=3, fontsize=8, color=INK_SECONDARY)
    ax.axvline(0, color=AXIS, linewidth=0.8)
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(f"Change in customs value ({unit}); blue = inflow, red = outflow")
    span = max(float(values.abs().max()), 1e-9)
    ax.set_xlim(min(values.min(), 0) - 0.18 * span, max(values.max(), 0) + 0.18 * span)
    period = f"{window_label(world['base_periods'])} vs {window_label(world['compare_periods'])}"
    ax.set_title(f"{labels.product}: where import value moved, {period}", loc="left", pad=24)
    ax.text(0, 1.015, f"All origins together: {world['value_change_usd'] / factor:+,.2f} {unit} "
            f"({world['value_growth_pct']:+.1f}%). The {len(plotted)} largest origin moves are shown.",
            transform=ax.transAxes, ha="left", va="bottom", fontsize=9, color=INK_SECONDARY)
    return _finish(fig, png, labels.source + " Origin is declared country of origin, not value-added origin.")


def plot_concentration(scope_series: pd.DataFrame, output_dir: str | Path, labels: ChartLabels) -> Path:
    """Herfindahl-Hirschman index of origin value shares."""

    data = _pick_frequency(scope_series, min_annual=12)
    plotted = data[["frequency", "period", "hhi", "top_origin", "top_origin_share_pct", "top3_share_pct"]]
    png, _ = _prepare_output(output_dir, "07_concentration", plotted)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10.5, 4.6))
    x = _x_values(data)
    ax.plot(x, data["hhi"], color=INK, linewidth=1.8)
    for level, text in ((1500, "1,500"), (2500, "2,500")):
        ax.axhline(level, color=AXIS, linewidth=0.8)
        ax.text(x.iloc[0], level, f" {text}", va="bottom", fontsize=7, color=INK_MUTED)
    last = data.iloc[-1]
    _end_label(ax, x.iloc[-1], last["hhi"], f"{last['hhi']:,.0f}")
    ax.set_ylim(0, max(float(data["hhi"].max()) * 1.15, 2800))
    ax.set_ylabel("HHI (0-10,000)")
    ax.set_title(f"{labels.product}: concentration of import origins", loc="left")
    return _finish(fig, png, labels.source + " HHI uses declared-origin value shares; reference lines at 1,500 and 2,500.")


def plot_unit_value(display: pd.DataFrame, scope_series: pd.DataFrame, output_dir: str | Path, labels: ChartLabels) -> Path:
    """Customs value per quantity unit, by origin, with the all-origin World line.

    An origin-period under 1% of import value is not drawn: a handful of
    units makes its unit value noise that would set the axis.
    """

    data = _pick_frequency(display, min_annual=12)
    data = data.assign(unit_value_usd=data["unit_value_usd"].where(data["value_share"] >= 0.01))
    world = scope_series[scope_series["frequency"].eq(data["frequency"].iloc[0])]
    world = world.assign(display_origin="World", unit_value_usd=world["world_unit_value_usd"])
    unit = labels.quantity_unit or "unit"
    png, fig = _origin_lines(data, "unit_value_usd", stem="08_unit_value", output_dir=output_dir, labels=labels,
                             title=f"{labels.product}: customs value per {unit}", ylabel=f"US$ per {unit}", world=world)
    return _finish(fig, png, labels.source + " Unit value mixes price and product mix within the scope; "
                   "origin-months under 1% of import value are not drawn.")


def plot_realized_duty(
    display: pd.DataFrame,
    scope_series: pd.DataFrame,
    output_dir: str | Path,
    labels: ChartLabels,
    *,
    timeline: pd.DataFrame | None = None,
) -> Path:
    data = _pick_frequency(display, min_annual=12)
    frequency = data["frequency"].iloc[0]
    world = scope_series[scope_series["frequency"].eq(frequency)]
    world = world.assign(display_origin="World", realized_duty_rate=world["world_realized_duty_rate_pct"] / 100)
    has_timeline = timeline is not None and not timeline.empty
    png, fig = _origin_lines(data, "realized_duty_rate", stem="09_realized_duty", output_dir=output_dir, labels=labels,
                             title=f"{labels.product}: realized duty rate by origin", ylabel="Calculated duty / customs value (%)",
                             scale=100, world=world, height=7.6 if has_timeline else 5.2)
    note = " Realized duty is collected duty over customs value, not the statutory rate."
    key: list[str] = []
    if has_timeline:
        ax = fig.axes[0]
        x = _x_values(data)
        x_max = x.max() + pd.offsets.MonthEnd(0) if frequency == "monthly" else x.max() + 0.999
        key, rows = _timeline_lines((ax,), timeline, frequency=frequency, x_min=x.min(), x_max=x_max)
        if not rows.empty:
            rows.to_csv(Path(output_dir) / "09_realized_duty.timeline.csv", index=False)
            ax.set_title(ax.get_title(loc="left"), loc="left", pad=30)  # get_title() alone reads the empty centre title
            note += " Tariff timeline: dark solid = effective date, light dashed = announced date."
    return _finish(fig, png, labels.source + note, footer=key)


def plot_world_check(world_check: pd.DataFrame, output_dir: str | Path, labels: ChartLabels) -> Path:
    data = _pick_frequency(world_check, min_annual=12)
    plotted = data[["frequency", "period", "hts_code", "world_value_usd", "origin_value_sum_usd", "world_value_gap_pct", "status"]]
    png, _ = _prepare_output(output_dir, "10_world_check", plotted)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10.5, 4.2))
    ax.axhspan(-0.5, 0.5, color=INK_MUTED, alpha=0.08, linewidth=0)
    codes = sorted(plotted["hts_code"].astype(str).unique())
    for index, code in enumerate(codes):
        group = plotted[plotted["hts_code"].astype(str).eq(code)]
        ax.plot(_x_values(group), group["world_value_gap_pct"], color=SLOTS[index % len(SLOTS)], linewidth=1.4,
                marker="o", markersize=3, label=code)
    if len(codes) > 1:
        ax.legend(ncol=6, loc="upper left")
    peak = max(float(plotted["world_value_gap_pct"].abs().max() or 0), 0.6)
    ax.set_ylim(-1.3 * peak, 1.3 * peak)
    ax.set_ylabel("(origin sum - World) / World (%)")
    checks = int(plotted["status"].ne("ok").sum())
    ax.set_title(f"{labels.product}: origin rows reconcile to the independent World total", loc="left")
    return _finish(fig, png, labels.source + f" Shaded band = +/-0.5% tolerance; periods outside it: {checks}.")


def plot_heading_map(heading_map: pd.DataFrame, output_dir: str | Path, labels: ChartLabels) -> Path:
    """HTS4 map: each HTS6 of the heading in the latest comparison window, scope lines dark."""

    annual = heading_map[heading_map["frequency"].eq("annual")]
    data = annual if not annual.empty else heading_map
    latest = data[data["comparison"].eq(data["comparison"].iloc[-1]) & data["hts6"].ne("HTS4 total")]
    plotted = latest.sort_values("value_usd")[
        ["heading", "hts6", "in_scope", "comparison", "compare_periods", "value_usd", "value_growth_pct",
         "focal_share_pct", "realized_duty_pct", "share_of_heading_pct"]
    ]
    png, _ = _prepare_output(output_dir, "11_heading_map", plotted)
    plt = _plt()
    fig, ax = plt.subplots(figsize=(10.2, 0.42 * len(plotted) + 2.0))
    factor, unit = _usd_scale(plotted["value_usd"])
    values = plotted["value_usd"] / factor
    colors = np.where(plotted["in_scope"].astype(bool), INFLOW, INK_MUTED)
    bars = ax.barh(plotted["hts6"].astype(str), values, height=0.55, color=colors, edgecolor=SURFACE, linewidth=1.5)
    ax.bar_label(
        bars,
        labels=[f"{share:.1f}% of heading; YoY {growth:+.1f}%; {labels.focal_origin} {focal:.1f}%; duty {duty:.1f}%"
                for share, growth, focal, duty in zip(plotted["share_of_heading_pct"], plotted["value_growth_pct"],
                                                      plotted["focal_share_pct"], plotted["realized_duty_pct"])],
        padding=3, fontsize=8, color=INK_SECONDARY,
    )
    ax.grid(axis="y", visible=False)
    ax.set_xlabel(f"Customs value ({unit}); blue = in the study scope, grey = rest of the heading")
    ax.set_xlim(0, float(values.max()) * 1.75 if len(values) else 1)
    period = window_label(str(plotted["compare_periods"].iloc[0])) if len(plotted) else ""
    ax.set_title(f"HTS4 map: HTS6 lines of {', '.join(sorted(set(plotted['heading'].astype(str))))}, {period}", loc="left", pad=12)
    return _finish(fig, png, labels.source + " Value and calculated duty only; quantity is not compared across HTS6 lines.")


def render_all(
    tables: dict[str, pd.DataFrame],
    *,
    events: pd.DataFrame,
    windows: list[dict[str, Any]],
    output_dir: str | Path,
    labels: ChartLabels,
    timeline: pd.DataFrame | None = None,
    heading_map: pd.DataFrame | None = None,
) -> tuple[list[Path], dict[str, str]]:
    """Render one chart per available analysis; return images and skip reasons."""

    out = Path(output_dir)
    generated: list[Path] = []
    skipped: dict[str, str] = {}
    scope_series, display = tables["scope_series"], tables["display_series"]
    generated.append(plot_scope_trend(scope_series, out, labels))
    reallocation = tables.get("product_reallocation")
    if reallocation is not None and len(reallocation) >= 2:
        generated.append(plot_product_reallocation(reallocation, out, labels))
    else:
        skipped["02_product_reallocation"] = "needs at least two HTS codes with both comparison years"
    monthly = tables.get("monthly_panel")
    if monthly is not None and not monthly.empty:
        generated.append(
            plot_study_event_path(
                monthly,
                events,
                out,
                title=labels.title or f"{labels.product}: monthly origin shares",
                focal_origin=labels.focal_origin,
                product_name=labels.product,
                highlight_origins=labels.highlight_origins,
                windows=windows,
                source=labels.source,
                timeline=timeline,
            )
        )
    else:
        skipped["03_event_path"] = "study has no monthly observations"
    generated.append(plot_origin_shares(display, out, labels))
    changes = tables.get("origin_change")
    for stem, prefix in (("05_origin_change", "annual_"), ("06_ytd_comparison", "ytd_")):
        subset = changes[changes["comparison"].astype(str).str.startswith(prefix)] if changes is not None else pd.DataFrame()
        if subset.empty:
            skipped[stem] = f"no {prefix.rstrip('_')} comparison in origin_change"
        else:
            generated.append(plot_origin_change(subset, out, labels, stem=stem))
    generated.append(plot_concentration(scope_series, out, labels))
    if display["quantity_comparable"].astype(bool).any() and display["unit_value_usd"].notna().any():
        generated.append(plot_unit_value(display, scope_series, out, labels))
    else:
        skipped["08_unit_value"] = "quantity units are not comparable, so unit value is not shown"
    generated.append(plot_realized_duty(display, scope_series, out, labels, timeline=timeline))
    generated.append(plot_world_check(tables["world_check"], out, labels))
    if heading_map is not None:  # only studies with [context] hts4 = true
        if heading_map.empty:
            skipped["11_heading_map"] = "HTS4 context queries not fetched (they need separate user approval)"
        else:
            generated.append(plot_heading_map(heading_map, out, labels))
    return generated, skipped


def plot_domestic_production_screen(screen: pd.DataFrame, output_dir: str | Path) -> Path:
    """Plot the broad Federal Reserve production-index screen (legacy baseline)."""

    if screen.empty:
        raise ValueError("Cannot plot an empty production-proxy screen")
    png, _ = _prepare_output(output_dir, "04_domestic_production_screen", screen)
    plt = _plt()
    ordered = screen.sort_values("index_change_pct")
    fig, ax = plt.subplots(figsize=(9.7, 4.8))
    colors = np.where(ordered["index_change_pct"] >= 0, INFLOW, OUTFLOW)
    bars = ax.barh(ordered["industry_name"], ordered["index_change_pct"], color=colors, height=0.55)
    ax.bar_label(bars, labels=[f"{value:+.1f}%" for value in ordered["index_change_pct"]], padding=3, fontsize=9)
    ax.axvline(0, color=AXIS, linewidth=0.8)
    ax.set_xlabel("Federal Reserve industrial-production index change (%)")
    ax.set_title("Domestic-production direction check — broad proxies, not product output", loc="left")
    minimum = float(ordered["index_change_pct"].min())
    maximum = float(ordered["index_change_pct"].max())
    span = max(maximum - minimum, 1.0)
    ax.set_xlim(minimum - 0.12 * span, maximum + 0.12 * span)
    return _finish(
        fig,
        png,
        "Source: Federal Reserve industrial-production series via FRED. HTS-to-NAICS/series mapping is analyst-selected "
        "and too broad to prove reshoring of a specific HTS6 product.",
    )


def validate_chart_audit_files(output_dir: str | Path) -> list[str]:
    """Return PNG names missing their same-stem chart-data CSV."""

    directory = Path(output_dir)
    return [path.name for path in directory.glob("*.png") if not path.with_suffix(".csv").exists()]
