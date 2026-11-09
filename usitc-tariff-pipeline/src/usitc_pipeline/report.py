"""Findings with evidence labels, and the Markdown report built from them.

``build_findings`` runs inside ``analyze``: it turns the validated result
tables into short claims, each with an evidence label and the table it came
from.  ``render_report`` runs after ``validate`` and only lays out
``findings.json`` and the chart manifest; it computes nothing.

Evidence labels: **Measured** (a DataWeb value as reported), **Derived**
(arithmetic on measured values: shares, growth, HHI, unit value, realized
duty), **Inference** (an interpretation needing outside evidence; this module
writes none), **Unknown** (cannot be answered from these data).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


SECTIONS = (
    ("scale", "总量：进口额、数量、单位价值", ("01_scope_trend", "08_unit_value")),
    ("sources", "来源地份额与流入流出", ("04_origin_shares", "05_origin_change", "02_product_reallocation")),
    ("ytd", "同期 YTD 对比", ("06_ytd_comparison",)),
    ("events", "事件窗口", ("03_event_path",)),
    ("concentration", "来源地集中度（HHI）", ("07_concentration",)),
    ("duty", "实际税率（realized duty）", ("09_realized_duty",)),
    ("data", "数据核对与口径", ("10_world_check", "12_product_mix")),
)
# Each key point takes the first available finding of its alternatives, so a
# study without an annual comparison still reports its YTD equivalent.
KEY_POINT_ORDER = (
    ("focal_share_annual", "ytd_focal_share"),
    ("top_movers_annual", "ytd_top_movers"),
    ("scale_annual",),
    ("ytd",),
    ("realized_duty", "ytd_realized_duty"),
    ("tariff_snapshot",),
    ("event_low",),
    ("event_latest",),
    ("quantity_blocked",),
    ("concentration",),
    ("unit_value",),
    ("world_check",),
    ("data_through",),
)
PATTERN_TEXT = {
    "share_loss_with_demand_growth": "份额下降，同时总进口数量上升",
    "share_loss_with_demand_contraction": "份额下降，同时总进口数量下降",
    "share_loss_demand_quantity_not_comparable": "份额下降；数量单位不可比，需求方向无法判断",
    "china_share_gain": "份额上升",
    "focal_share_gain": "份额上升",
    "limited_share_change": "份额变化不足 0.5 个百分点",
}


def usd(value: Any) -> str:
    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e8:
        return f"{sign}{value / 1e8:,.1f} 亿美元"
    if value >= 1e4:
        return f"{sign}{value / 1e4:,.1f} 万美元"
    return f"{sign}{value:,.0f} 美元"


def pct(value: Any, digits: int = 1, *, signed: bool = False) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{float(value):{'+' if signed else ''}.{digits}f}%"


def pp(value: Any, digits: int = 1, unit: str = " pp") -> str:
    return "—" if value is None or pd.isna(value) else f"{float(value):+.{digits}f}{unit}"


def gap(value: Any, digits: int = 1) -> str:
    """'低 64.3 pp' / '高 9.7 pp': a difference stated in words."""

    return "—" if value is None or pd.isna(value) else f"{'低' if float(value) < 0 else '高'} {abs(float(value)):.{digits}f} pp"


def window(text: Any) -> str:
    """'2024' -> '2024 年'; '2025-01..2025-08' -> '2025 年 1–8 月'; other ranges keep both ends."""

    start, _, end = str(text).partition("..")
    end = end or start
    if len(start) == 7 and start[:4] == end[:4]:
        months = str(int(start[5:7])) + ("" if start == end else f"–{int(end[5:7])}")
        return f"{start[:4]} 年 {months} 月"
    if start == end:
        return f"{start} 年" if len(start) == 4 else start
    return f"{start} 至 {end}"


def _finding(key: str, section: str, label: str, claim: str, source: str, **values: Any) -> dict[str, Any]:
    return {"id": key, "section": section, "label": label, "claim": claim, "source": source, "values": values}


def _row(frame: pd.DataFrame, **match: Any) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    mask = pd.Series(True, index=frame.index)
    for column, value in match.items():
        mask &= frame[column].astype(str).eq(str(value))
    rows = frame[mask]
    return rows.iloc[0] if not rows.empty else None


def _comparison_findings(changes: pd.DataFrame, focal: str, quantity_unit: str | None) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    if changes is None or changes.empty:
        return findings
    for comparison, block in changes.groupby("comparison", sort=False):
        annual = str(comparison).startswith("annual_")
        world = _row(block, origin_name="World")
        focal_row = _row(block, origin_name=focal)
        origins = block[block["origin_name"].ne("World")]
        span = f"{window(world['base_periods'])}对 {window(world['compare_periods'])}"
        if annual:
            findings.append(
                _finding(
                    "scale_annual", "scale", "Derived",
                    f"总进口额 {usd(world['compare_value_usd'])}（{window(world['compare_periods'])}），"
                    f"比 {window(world['base_periods'])}的 {usd(world['base_value_usd'])} 变化 {pct(world['value_growth_pct'], signed=True)}"
                    + (f"；数量变化 {pct(world['quantity_growth_pct'], signed=True)}（单位 {quantity_unit}）" if bool(world["quantity_comparable"]) else "")
                    + "。",
                    "tables/origin_change.csv",
                    comparison=comparison, value_growth_pct=world["value_growth_pct"], quantity_growth_pct=world["quantity_growth_pct"],
                )
            )
            if bool(world["quantity_comparable"]) and pd.notna(world["unit_value_change_pct"]):
                findings.append(
                    _finding(
                        "unit_value", "scale", "Derived",
                        f"平均单位价值（海关价值 / 数量，单位 {quantity_unit}）从 {world['base_unit_value_usd']:,.0f} 美元"
                        f"变为 {world['compare_unit_value_usd']:,.0f} 美元（{pct(world['unit_value_change_pct'], signed=True)}）；"
                        "单位价值同时反映价格和 scope 内产品组合的变化。",
                        "tables/origin_change.csv",
                        base=world["base_unit_value_usd"], compare=world["compare_unit_value_usd"],
                    )
                )
        if focal_row is not None:
            findings.append(
                _finding(
                    "focal_share_annual" if annual else "ytd_focal_share", "sources" if annual else "ytd", "Derived",
                    f"{focal} 占进口额的份额：{span}，{pct(focal_row['base_share_pct'])} → {pct(focal_row['compare_share_pct'])}"
                    f"（{pp(focal_row['share_change_pp'])}），进口额 {pct(focal_row['value_growth_pct'], signed=True)}。",
                    "tables/origin_change.csv",
                    comparison=comparison, base_share_pct=focal_row["base_share_pct"],
                    compare_share_pct=focal_row["compare_share_pct"], share_change_pp=focal_row["share_change_pp"],
                )
            )
        gainers = origins[origins["share_change_pp"].gt(0) & origins["origin_name"].ne(focal)]
        gainers = gainers.sort_values("share_change_pp", ascending=False).head(3)
        inflow = origins[origins["value_change_usd"].gt(0)]
        outflow = origins[origins["value_change_usd"].lt(0)]
        if annual or not gainers.empty:
            names = "、".join(f"{row.origin_name}（{pp(row.share_change_pp)}）" for row in gainers.itertuples()) or "无"
            findings.append(
                _finding(
                    "top_movers_annual" if annual else "ytd_top_movers", "sources" if annual else "ytd", "Derived",
                    f"{span}：份额上升最多的来源地是 {names}。进口额增加的来源地 {len(inflow)} 个，合计 {usd(inflow['value_change_usd'].sum())}；"
                    f"减少的 {len(outflow)} 个，合计 {usd(outflow['value_change_usd'].sum())}。"
                    f"份额上升不等于产能从 {focal} 直接转移。",
                    "tables/origin_change.csv",
                    comparison=comparison, gainers=gainers["origin_name"].tolist(),
                )
            )
        if not annual:
            findings.append(
                _finding(
                    "ytd", "ytd", "Derived",
                    f"同期对比（{span}）：总进口额 {usd(world['compare_value_usd'])}，比上年同期 {pct(world['value_growth_pct'], signed=True)}"
                    + (f"；{focal} 份额 {pct(focal_row['base_share_pct'])} → {pct(focal_row['compare_share_pct'])}（{pp(focal_row['share_change_pp'])}）" if focal_row is not None else "")
                    + "。",
                    "tables/origin_change.csv",
                    comparison=comparison, value_growth_pct=world["value_growth_pct"],
                )
            )
        if pd.notna(world.get("base_realized_duty_pct")):
            focal_text = (
                f"；{focal} {pct(focal_row['base_realized_duty_pct'], 2)} → {pct(focal_row['compare_realized_duty_pct'], 2)}"
                if focal_row is not None
                else ""
            )
            findings.append(
                _finding(
                    "realized_duty" if annual else "ytd_realized_duty", "duty", "Derived",
                    f"实际税率（已计算关税 / 海关价值）：全部来源 {pct(world['base_realized_duty_pct'], 2)} → "
                    f"{pct(world['compare_realized_duty_pct'], 2)}{focal_text}（{span}）。这是实际征收水平，不是法定税率。",
                    "tables/origin_change.csv",
                    world_base=world["base_realized_duty_pct"], world_compare=world["compare_realized_duty_pct"],
                )
            )
    return findings


def _event_findings(metrics: dict[str, Any], focal: str) -> list[dict[str, Any]]:
    windows = (metrics or {}).get("event_windows") or {}
    findings = [
        _finding(
            f"event_{window_id}", "events", "Derived",
            f"{item['label']}（{item['start']} 至 {item['end']}）：{focal} 加权份额 {pct(item['weighted_focal_value_share_pct'])}，"
            f"最低 {pct(item['min_share_pct'])}（{item['min_share_month']}），最高 {pct(item['max_share_pct'])}（{item['max_share_month']}），"
            f"趋势 {pp(item['share_slope_pp_per_month'], 2, ' pp/月')}。",
            "tables/event_metrics.json",
            **{key: item.get(key) for key in ("weighted_focal_value_share_pct", "min_share_pct", "max_share_pct")},
        )
        for window_id, item in windows.items()
        if item.get("months_observed")
    ]
    summary = (metrics or {}).get("summary") or {}
    if summary:
        low = windows[summary["low_window"]]
        reference = (
            f"，比前一窗口（{windows[summary['reference_window']]['label']}）的加权份额 "
            f"{pct(summary['reference_weighted_share_pct'])} {gap(summary['low_vs_reference_pp'])}"
            if "reference_window" in summary
            else ""
        )
        findings.append(
            _finding(
                "event_low", "events", "Derived",
                f"各事件窗口里 {focal} 份额最低的月份是 {summary['low_month']}（{low['label']}），{pct(summary['low_share_pct'])}"
                f"{reference}。窗口与政策时间对齐只是描述，不是因果识别。",
                "tables/event_metrics.json", **summary,
            )
        )
        if "latest_window" in summary:
            latest = windows[summary["latest_window"]]
            findings.append(
                _finding(
                    "event_latest", "events", "Derived",
                    f"最近一个窗口（{latest['label']}，{latest['start']} 至 {latest['end']}）{focal} 份额最高 "
                    f"{pct(summary['latest_max_share_pct'])}（{summary['latest_max_month']}），比最低点{gap(summary['latest_max_vs_low_pp'])}。",
                    "tables/event_metrics.json", **summary,
                )
            )
    return findings


def _unit_word(unit: Any) -> str:
    """'No.' -> '个' (as in 按个), anything else unchanged."""

    return "个" if str(unit or "").strip().lower().rstrip(".") in {"no", "number"} else str(unit or "第一单位")


def _feature_findings(features: dict[str, Any], focal: str, identity: dict[str, Any]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    snapshot_rows = features.get("tariff_snapshot") or []
    if snapshot_rows:
        statuses = sorted({str(row.get("snapshot_status") or row.get("status") or "Unresolved") for row in snapshot_rows})
        origins = list(dict.fromkeys(str(row.get("origin") or "") for row in snapshot_rows))
        open_rows = sum(str(row.get("status") or "").casefold() != "resolved" for row in snapshot_rows)
        label = "Measured" if statuses == ["Resolved"] else "Unknown"
        findings.append(
            _finding(
                "tariff_snapshot", "duty", label,
                f"HTS 法定关税处理快照（{', '.join(origins)}）：总体状态 {', '.join(statuses)}；"
                f"{len(snapshot_rows)} 个产品行中 {open_rows} 行仍有 Mixed、Conditional 或 Unresolved 项。"
                "快照保留 Column 1-General 与已核实 Chapter 99 公式，不以 realized duty 代替法定处理。",
                "tables/tariff_treatment_snapshot.csv", statuses=statuses, rows=len(snapshot_rows), open_rows=open_rows,
            )
        )
    status = features.get("heading_map_status")
    if status and not features.get("heading_map"):
        findings.append(_finding(
            "heading_map_not_fetched", "scale", "Unknown",
            f"HTS4 地图：未取数（{status['jobs']} 个 HTS4 context 查询在 scope 之外，要用户另外批准；acquire 不取，已缓存 {status['cached']} 个）。",
            "query_plan.json", jobs=status["jobs"], cached=status["cached"],
        ))
    for row in features.get("heading_map") or []:
        if row["in_scope"]:
            findings.append(_finding(
                f"heading_map_{row['hts6']}_{row['comparison']}", "scale", "Derived",
                f"HTS4 地图（{window(row['compare_periods'])}）：{row['hts6']} 占 {row['heading']} 进口额 "
                f"{row['share_of_heading_pct']:.1f}%，同比 {row['value_growth_pct']:+.1f}%，{focal} 份额 {row['focal_share_pct']:.1f}%，"
                f"实际税率 {row['realized_duty_pct']:.1f}%。",
                "tables/heading_map.csv", hts6=row["hts6"],
            ))
    split = features.get("focal_split") or []
    for comparison in dict.fromkeys(row["comparison"] for row in split):
        rows = {row["group"]: row for row in split if row["comparison"] == comparison}
        total, focal_row = rows.get("total"), rows.get("focal")
        if not total or not focal_row:
            continue
        span = f"{window(total['base_periods'])}对 {window(total['compare_periods'])}"
        findings.append(
            _finding(
                f"calculated_duty_{comparison}", "duty", "Derived",
                f"已计算关税（美元）：全部来源 {usd(total['base_calculated_duty_usd'])} → {usd(total['compare_calculated_duty_usd'])}，"
                f"{focal} {usd(focal_row['base_calculated_duty_usd'])} → {usd(focal_row['compare_calculated_duty_usd'])}（{span}）。",
                "tables/focal_split.csv", comparison=comparison,
                world_base=total["base_calculated_duty_usd"], world_compare=total["compare_calculated_duty_usd"],
                focal_base=focal_row["base_calculated_duty_usd"], focal_compare=focal_row["compare_calculated_duty_usd"],
            )
        )
    for row in features.get("quantity_check") or []:
        if not row.get("conflict"):
            continue
        what = {"code": f"HTS {row['key']}", "segment": f"分段 {row['key']}", "total": "scope 合计"}[row["level"]]
        findings.append(
            _finding(
                f"quantity_conflict_{row['level']}_{row['key']}_{row['comparison']}", "scale", "Derived",
                f"{what}，{window(row['base_periods'])}对 {window(row['compare_periods'])}：数量结论不可靠"
                f"（按{_unit_word(row['first_unit'])} {pct(row['quantity_growth_pct'], signed=True)}，"
                f"按 {row['second_unit']} {pct(row['quantity_2_growth_pct'], signed=True)}）。两种计量单位的变化方向相反。",
                "tables/quantity_check.csv", level=row["level"], item=row["key"], comparison=row["comparison"],
                first=row["quantity_growth_pct"], second=row["quantity_2_growth_pct"],
            )
        )
    return findings


def focal_split_table(rows: list[dict[str, Any]]) -> list[str]:
    """Focal origin / other origins / total, one block per comparison."""

    has_weight = any("base_quantity_2" in row for row in rows)
    head = "| 对比 | 来源地 | 进口额 | 变化 | 金额份额 | 已计算关税 | 实际税率 |" + (" 重量 | 重量变化 | 重量份额 |" if has_weight else "")
    table = [head, "|" + "---|" * (head.count("|") - 1)]
    for row in rows:
        line = (
            f"| {window(row['base_periods'])} → {window(row['compare_periods'])} | {row['label']} | "
            f"{usd(row['base_value_usd'])} → {usd(row['compare_value_usd'])} | {pct(row['value_growth_pct'], signed=True)} | "
            f"{pct(row['base_share_pct'])} → {pct(row['compare_share_pct'])} | "
            f"{usd(row['base_calculated_duty_usd'])} → {usd(row['compare_calculated_duty_usd'])} | "
            f"{pct(row['base_realized_duty_pct'])} → {pct(row['compare_realized_duty_pct'])} |"
        )
        if has_weight:
            line += (
                f" {tonnes(row.get('base_quantity_2'), row.get('quantity_2_unit'))} → {tonnes(row.get('compare_quantity_2'), row.get('quantity_2_unit'))} | "
                f"{pct(row.get('quantity_2_growth_pct'), signed=True)} | "
                f"{pct(row.get('base_quantity_2_share_pct'))} → {pct(row.get('compare_quantity_2_share_pct'))} |"
            )
        table.append(line)
    return table


def segment_table(rows: list[dict[str, Any]], periods: list[str]) -> list[str]:
    """Segments side by side for the chosen periods: value, focal value share, focal realized duty (and weight share)."""

    segments = list(dict.fromkeys(row["segment"] for row in rows))
    has_weight = any("focal_quantity_2_share_pct" in row for row in rows)
    by_key = {(row["segment"], row["period"]): row for row in rows}
    head = "| 时期 | " + " | ".join(
        f"{name} 进口额 | {name} 焦点份额 | {name} 焦点实际税率" + (f" | {name} 焦点重量份额" if has_weight else "")
        for name in segments
    ) + " |"
    table = [head, "|" + "---|" * (head.count("|") - 1)]
    for period in periods:
        cells = []
        for name in segments:
            row = by_key.get((name, period))
            values = (
                [usd(row["world_value_usd"]), pct(row["focal_value_share_pct"]), pct(row["focal_realized_duty_pct"])]
                + ([pct(row.get("focal_quantity_2_share_pct"))] if has_weight else [])
                if row else ["—"] * (4 if has_weight else 3)
            )
            cells += values
        table.append(f"| {window(period)} | " + " | ".join(cells) + " |")
    return table


def tonnes(value: Any, unit: Any) -> str:
    """kg -> '70.9 万吨' / '4,100 吨'; other units keep their number and unit."""

    if value is None or pd.isna(value):
        return "—"
    value = float(value)
    if str(unit or "").lower().startswith("k"):
        tons = value / 1000
        return f"{tons / 1e4:,.1f} 万吨" if abs(tons) >= 1e4 else f"{tons:,.0f} 吨"
    return f"{value:,.0f} {unit or ''}".strip()


def build_findings(
    *,
    identity: dict[str, Any],
    focal_origin: str,
    validation: dict[str, Any],
    tables: dict[str, pd.DataFrame],
    event_metrics: dict[str, Any],
    skips: dict[str, Any],
    features: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Turn validated tables into labeled claims and pick 5-8 key points.

    ``features`` carries the results of opt-in study features (the tariff
    timeline, ...); a study without them gets exactly the findings it got
    before those features existed.
    """

    features = features or {}

    scope_series, world_check = tables["scope_series"], tables["world_check"]
    coverage, reallocation = tables["coverage_audit"], tables.get("product_reallocation")
    findings = _comparison_findings(tables.get("origin_change"), focal_origin, identity.get("quantity_unit"))
    findings += _event_findings(event_metrics, focal_origin)

    if reallocation is not None and not reallocation.empty:
        for row in reallocation.itertuples():
            findings.append(
                _finding(
                    f"pattern_{row.hts_code}", "sources", "Derived",
                    f"HTS {row.hts_code}：{focal_origin} {PATTERN_TEXT.get(row.pattern, row.pattern)}"
                    f"（份额 {pp(row.focal_share_change_pp)}，总数量 {pct(row.world_import_quantity_growth_pct, signed=True)}）。",
                    "tables/product_reallocation.csv", pattern=row.pattern,
                )
            )

    annual = scope_series[scope_series["frequency"].eq("annual") & scope_series["period_status"].eq("full_year")]
    hhi_rows = annual if len(annual) >= 2 else scope_series[scope_series["frequency"].eq("monthly")]
    if len(hhi_rows) >= 2:
        first, last = hhi_rows.iloc[0], hhi_rows.iloc[-1]
        findings.append(
            _finding(
                "concentration", "concentration", "Derived",
                f"来源地集中度 HHI 从 {first['hhi']:,.0f}（{first['period']}）变为 {last['hhi']:,.0f}（{last['period']}）；"
                f"{last['period']} 最大来源地 {last['top_origin']} 占 {pct(last['top_origin_share_pct'])}，前三名合计 {pct(last['top3_share_pct'])}。",
                "tables/scope_series.csv",
                first_period=first["period"], last_period=last["period"], first_hhi=first["hhi"], last_hhi=last["hhi"],
            )
        )

    if not identity.get("quantity_comparable") or not scope_series["quantity_comparable"].astype(bool).all():
        findings.append(
            _finding(
                "quantity_blocked", "scale", "Unknown",
                "数量单位不可比（scope 内单位不一致，或部分产品期单位混合/缺失），数量、数量份额和单位价值的结论全部停掉；以进口额为基础的结论不受影响。",
                "tables/scope_series.csv", blocked_product_periods=validation.get("quantity_blocked_product_periods"),
            )
        )
    second_units = identity.get("second_units") or []
    if identity.get("quantity_basis") == "both":
        findings.append(
            _finding(
                "quantity_basis", "data", "Measured",
                f"数量主列用第一计量单位 {identity.get('quantity_unit')}；第二计量单位 {identity.get('second_unit')}"
                "（DataWeb CONS_SEC_UNIT_QUANT）的数量、份额和单位价值并列在旁边，列名带 _2。",
                "processed/trade_tidy.csv", quantity_unit=identity.get("quantity_unit"), second_unit=identity.get("second_unit"),
            )
        )
    elif identity.get("quantity_basis") == "second":
        findings.append(
            _finding(
                "quantity_basis", "data", "Measured",
                f"本次数量、数量份额和单位价值按第二计量单位 {identity.get('quantity_unit')} 计算"
                f"（DataWeb CONS_SEC_UNIT_QUANT），不是第一单位 {identity.get('first_unit')}。",
                "processed/trade_tidy.csv", quantity_unit=identity.get("quantity_unit"),
                first_unit=identity.get("first_unit"),
            )
        )
    elif second_units:
        second_unit = identity.get("second_unit")
        findings.append(
            _finding(
                "second_unit", "data", "Unknown",
                (f"HTS 对本 scope 的编码还规定了第二计量单位 {second_unit}" if second_unit
                 else f"部分编码有第二计量单位（{'、'.join(second_units)}），不能在整个 scope 里加总")
                + f"；本次数量只用第一单位（{identity.get('quantity_unit') or '不可比'}），按第二单位计的数量没有取，留待后续分析。",
                "analysis_manifest.json", second_unit=second_unit, second_units=second_units,
            )
        )
    gaps = pd.to_numeric(world_check["world_value_gap_pct"], errors="coerce").abs()
    findings.append(
        _finding(
            "world_check", "data", "Measured",
            f"各来源地加总与独立 World 查询的最大偏差 {gaps.max():.3f}%，{int(world_check['status'].ne('ok').sum())} 个产品期超出 ±0.5% 容差。",
            "tables/world_check.csv", max_abs_gap_pct=gaps.max(),
        )
    )
    through = identity.get("data_through") or {}
    missing = coverage[coverage["missing_period_count"].gt(0)] if not coverage.empty else coverage
    findings.append(
        _finding(
            "data_through", "data", "Measured",
            "数据截至 " + "，".join(f"{key} {value}" for key, value in through.items() if value)
            + "（由独立 World 查询确定，尾部零值占位月份不算数据）"
            + ("；覆盖审计无内部缺口。" if missing.empty else f"；覆盖审计发现缺口：{'；'.join(missing['hts_code'].astype(str) + ' ' + missing['missing_periods'].astype(str))}。"),
            "tables/coverage_audit.csv", data_through=through,
        )
    )
    gate = identity.get("gate") or {}
    if not gate.get("continuity_verified"):
        findings.append(
            _finding(
                "continuity_unverified", "data", "Unknown",
                "HTS 连续性未核实：没有用历年 HTS 档案确认这些编码在整个研究期内口径不变，跨年比较可能受编码拆分或合并影响。",
                "analysis_manifest.json", continuity_verified=False,
            )
        )
    if gate.get("mode") == "legacy_replay":
        findings.append(
            _finding(
                "legacy_replay", "data", "Unknown",
                "这是 legacy replay：scope 尚未批准，旧快照没有 payload hash（只核对了响应哈希）。结果用于回归验收和方法演示，不是已批准研究的结论。",
                "analysis_manifest.json", reason=gate.get("reason"),
            )
        )

    findings += _feature_findings(features, focal_origin, identity)
    timeline = features.get("timeline") or []
    if timeline:
        to_verify = sum(bool(row["needs_check"]) for row in timeline)
        sourced = len(timeline) - to_verify
        findings.append(
            _finding(
                "timeline", "events", "Unknown",
                f"关税时间线 {len(timeline)} 条（按税号 {sum(row['layer'] == 'product' for row in timeline)} 条，"
                f"按国家 {sum(row['layer'] == 'country' for row in timeline)} 条）：{sourced} 条有"
                f"一手来源（HTS 档案、Federal Register、USTR、CBP），{to_verify} 条来源仍待核实。"
                "表中仅保留通过关税库 coverage=applies 筛选的商品适用措施和单列的 MFN 基础税率。"
                "时间线只是背景，和进口数据的时间对齐不是因果识别。",
                "tables/tariff_timeline.csv", rows=len(timeline), sourced=sourced, to_verify=to_verify,
            )
        )

    by_id = {item["id"]: item for item in findings}
    key_points = [next(key for key in options if key in by_id) for options in KEY_POINT_ORDER if any(key in by_id for key in options)][:8]
    return {
        "schema_version": 1,
        "identity": identity,
        "evidence_labels": {
            "Measured": "DataWeb 报告的原值",
            "Derived": "由原值算出的份额、增速、HHI、单位价值、实际税率",
            "Inference": "需要外部证据的解释（本报告不写）",
            "Unknown": "这些数据回答不了",
        },
        "findings": findings,
        "key_points": key_points,
        "skips": skips,
        **({"timeline": timeline} if timeline else {}),
        **{
            name: features[name]
            for name in (
                "focal_split", "segment_series", "drilldown_menu", "product_hierarchy_matrix", "heading_map",
                "share_decomposition", "proxy_notes", "next_steps", "tariff_snapshot"
            )
            if features.get(name)
        },
        "boundaries": [
            "原产地是申报的原产国/地区，不是增加值来源。",
            "份额上升的来源地不等于从焦点来源地直接转移的产能。",
            "与政策时间对齐只是描述，没有因果识别设计。",
            "实际税率不是法定税率；HTS8/10 的税则和排除清单不在本数据里。",
        ],
    }


def segment_periods(rows: list[dict[str, Any]], identity: dict[str, Any]) -> list[str]:
    """The last three full years plus the YTD windows, in order."""

    annual = sorted({row["period"] for row in rows if row["frequency"] == "annual"})[-3:]
    ytd = list(dict.fromkeys(row["period"] for row in rows if row["frequency"] == "ytd"))
    return annual + ytd


def drilldown_table(rows: list[dict[str, Any]]) -> list[str]:
    """The drill-down menu as a Markdown table, one row per finer HTS level."""

    def cell(value: Any) -> str:
        return str(value or "").replace("|", "/").replace("\n", " ") or "—"

    table = [
        "| 上一层 | 层级 | 行数 | 编码 | 单位 | HTS8 基本税率 | 附加税只覆盖部分行 | 研究期内拆分 | 结论 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        table.append(
            f"| {cell(row['parent'])} | {row['level']} | {row['line_count']} | {cell(row['lines'])} | {cell(row['units'])} | "
            f"{cell(row['hts8_base_rate'])} | {cell(row['partial_duty_rows']) if row['partial_duty'] else '否'} | "
            f"{cell(row['splits_in_period']) if row['splits_in_period'] else '无'} | {row['verdict']} |"
        )
    return table


def timeline_table(rows: list[dict[str, Any]]) -> list[str]:
    """The tariff timeline as a Markdown table, in date order."""

    def cell(value: Any) -> str:
        return str(value or "").replace("|", "/").replace("\n", " ") or "—"

    table = [
        "| 日期 | 类型 | 措施/法源编号 | 税率变化 | 适用编码 | 来源地 | 数据来源 |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        source = cell(row.get("source"))
        if row.get("source_url"):
            source += f" ([原文]({row['source_url']}))"
        if row.get("needs_check"):
            source += "；来源待核实"
        table.append(
            f"| {row['date']} | {'生效' if row['date_type'] == 'effective' else '宣布'} | {cell(row['measure'])} | "
            f"{cell(row['rate_change'])} | {cell(row['applies_to']) if row['applies_to'] else '整个 scope'} | "
            f"{cell(row['origins']) if row['origins'] else '全部'} | {source} |"
        )
    return table


def proxy_notes_table(rows: list[dict[str, Any]]) -> list[str]:
    """Compact source/formula/boundary table for every proxy used in the study."""

    def cell(value: Any) -> str:
        return str(value or "").replace("|", "/").replace("\n", " ") or "—"

    table = [
        "| 指标/代理 | 证据 | 来源字段 | 公式 | 单位 | 代表什么 | 不代表什么 | 质量提示 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        table.append(
            f"| {cell(row['item_id'])} | {cell(row['evidence_label'])} | {cell(row['source_fields'])} | "
            f"{cell(row['formula'])} | {cell(row['unit'])} | {cell(row['represents'])} | "
            f"{cell(row['does_not_represent'])} | {cell(row['quality_warning'])} |"
        )
    return table


def product_hierarchy_table(rows: list[dict[str, Any]]) -> list[str]:
    """Compact HTS6 -> HTS8 -> HTS10 structure and product-share status."""

    def cell(value: Any) -> str:
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return "—"
        return str(value).replace("|", "/").replace("\n", " ") or "—"

    table = [
        "| HTS8 法定税率行 | HTS10 | 可读类目（英文，官方层级） | 官方 HTS 描述（英文） | 单位 | HTS8 Column 1-General | 附加税状态/公式 | HTS10 占 HTS6 进口额 | 数据状态 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        share = row.get("share_of_parent_hts6_pct")
        share_text = "—" if share is None or (isinstance(share, float) and pd.isna(share)) else f"{float(share):.1f}%"
        treatment = " / ".join(
            item for item in (cell(row.get("additional_tariff_status")), cell(row.get("additional_tariff_rate"))) if item != "—"
        ) or "—"
        table.append(
            f"| {cell(row.get('hts8_code'))} | {cell(row.get('hts10_code'))} | "
            f"{cell(row.get('hts10_display_label_en'))} | {cell(row.get('hts10_description'))} | "
            f"{cell(row.get('quantity_unit'))} | {cell(row.get('hts8_mfn_rate'))} | {treatment} | {share_text} | "
            f"{cell(row.get('product_share_status'))} |"
        )
    return table


def next_steps_table(rows: list[dict[str, Any]]) -> list[str]:
    """At most three deterministic follow-ups from validated study states."""

    def cell(value: Any) -> str:
        return str(value or "").replace("|", "/").replace("\n", " ") or "—"

    table = [
        "| 优先级 | 下一步 | 原因 | 需要的输入 | 预期输出 | 是否需联网/新数据 |",
        "|---|---|---|---|---|---|",
    ]
    for row in rows:
        table.append(
            f"| {cell(row.get('priority'))} | {cell(row.get('title'))} | {cell(row.get('rationale'))} | "
            f"{cell(row.get('required_input'))} | {cell(row.get('expected_output'))} | "
            f"{cell(row.get('needs_network_or_new_data'))} |"
        )
    return table


def tariff_snapshot_table(rows: list[dict[str, Any]]) -> list[str]:
    """Product-level Column 1-General plus verified Chapter 99 treatment."""

    def cell(value: Any) -> str:
        return str(value or "").replace("|", "/").replace("\n", " ") or "—"

    table = [
        "| HTS 产品行 | 产品 | 原产地 | 进口日期 | 状态 | Column 1-General | Chapter 99 层 | 处理公式 | 未解决/条件项 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        open_items = "; ".join(
            item for item in (cell(row.get("unresolved_reasons")), cell(row.get("conditional_reasons"))) if item != "—"
        ) or "—"
        chapter99 = " ".join(
            item for item in (cell(row.get("chapter_99_headings")), cell(row.get("additional_rate_text"))) if item != "—"
        ) or "—"
        table.append(
            f"| {cell(row.get('hts'))} | {cell(row.get('description'))} | {cell(row.get('origin'))} | "
            f"{cell(row.get('entry_date'))} | {cell(row.get('status'))} | {cell(row.get('base_rate_text'))} | "
            f"{chapter99} | {cell(row.get('treatment_formula'))} | {open_items} |"
        )
    return table


def render_report(
    findings: dict[str, Any],
    chart_manifest: dict[str, Any],
    *,
    validation: dict[str, Any],
    output_dir: Path,
) -> tuple[Path, Path]:
    """Write report.md and key_points.md from findings.json and the chart manifest only."""

    identity = findings["identity"]
    gate = identity.get("gate") or {}
    by_id = {item["id"]: item for item in findings["findings"]}
    charts = {chart["stem"]: chart for chart in chart_manifest.get("charts") or []}
    skipped = chart_manifest.get("skipped") or {}

    def line(item: dict[str, Any]) -> str:
        return f"{item['claim']} **[{item['label']}]** — 来源：`{item['source']}`"

    key_lines = [f"{number}. {line(by_id[key])}" for number, key in enumerate(findings["key_points"], start=1)]
    through = "，".join(f"{key} {value}" for key, value in (identity.get("data_through") or {}).items() if value)
    lines = [f"# {identity['study_title'] or identity['product_name']}", ""]
    if gate.get("mode") == "legacy_replay":
        lines += [
            "> **回放结果，不是已批准研究。** scope 未经批准；旧快照没有 payload hash，只核对了响应哈希；HTS 连续性未核实。"
            f"回放配置：`{gate.get('replay_config')}`。",
            "",
        ]
    lines += [
        "| 项目 | 内容 |",
        "|---|---|",
        f"| 产品 | {identity['product_name']}（HTS{identity['hts_level']}：{', '.join(identity['included_codes'])}） |",
        f"| scope | {identity['scope_id']} v{identity['scope_version']}，scope_sha256 `{identity['scope_sha256'][:12]}` |",
        f"| study | {identity['study_id']}，study_sha256 `{identity['study_sha256'][:12]}` |",
        f"| plan | plan_sha256 `{identity['plan_sha256'][:12]}` |",
        f"| 数据 | USITC DataWeb，U.S. imports for consumption；数据截至 {through} |",
        f"| 批准 | {('已批准（对话确认）' if gate.get('approval_method') == 'chat' else '已批准') if gate.get('scope_approved') else '**未批准**'} |",
        f"| HTS 连续性 | {'已核实' if gate.get('continuity_verified') else '**未核实**'} |",
        *(
            [f"| 数量口径 | 第二计量单位 {identity.get('quantity_unit')}（第一单位 {identity.get('first_unit')} 不用于数量和单位价值） |"]
            if identity.get("quantity_basis") == "second"
            else [f"| 数量口径 | 第一计量单位 {identity.get('quantity_unit')}；第二计量单位 {identity.get('second_unit')} 并列（列名带 _2） |"]
            if identity.get("quantity_basis") == "both"
            else [f"| 第二计量单位 | {identity.get('second_unit') or '、'.join(identity['second_units']) + '（部分编码）'}，未取，留待后续分析 |"]
            if identity.get("second_units")
            else []
        ),
        f"| 验证 | {'通过' if validation.get('ok') else '**未通过**'}（panel {validation.get('rows')} 行，{validation.get('product_periods')} 个产品期，{validation.get('charts')} 张图） |",
        "",
        "证据标签：" + "；".join(f"**{key}** = {value}" for key, value in findings["evidence_labels"].items()) + "。",
        "",
        "## 要点",
        "",
        *key_lines,
        "",
    ]
    timeline = findings.get("timeline") or []
    has_windows = any(item["id"].startswith("event_") for item in findings["findings"])
    for number, (section, title, stems) in enumerate(SECTIONS, start=1):
        items = [item for item in findings["findings"] if item["section"] == section]
        if section == "events" and timeline:
            title = "关税时间线与事件窗口" if has_windows else "关税时间线"
        lines += [f"## {number}. {title}", ""]
        if section == "events" and timeline:
            lines += timeline_table(timeline) + [""]
        if section == "duty" and findings.get("tariff_snapshot"):
            lines += [
                "HTS 法定关税处理快照（非 CBP binding ruling；不以 realized duty 代替法定税率）**[Measured/Derived]** — 来源：`tables/tariff_treatment_snapshot.csv`",
                "",
                *tariff_snapshot_table(findings["tariff_snapshot"]),
                "",
            ]
        if section == "scale" and findings.get("focal_split"):
            lines += ["焦点来源地、其他来源地和总计 **[Derived]** — 来源：`tables/focal_split.csv`", ""]
            lines += focal_split_table(findings["focal_split"]) + [""]
        if section == "data" and findings.get("drilldown_menu"):
            lines += ["下钻菜单（只看产品和税率，不看贸易数据）**[Measured]** — 来源：`tables/drilldown_menu.csv`", ""]
            lines += drilldown_table(findings["drilldown_menu"]) + [""]
        if section == "data" and findings.get("product_hierarchy_matrix"):
            lines += [
                "产品层级矩阵（HTS8 是法定税率行，HTS10 是统计品类；份额缺数据时不推算）**[Measured/Derived]** — 来源：`tables/product_hierarchy_matrix.csv`",
                "",
            ]
            lines += product_hierarchy_table(findings["product_hierarchy_matrix"]) + [""]
        if section == "sources" and findings.get("segment_series"):
            segment_rows = findings["segment_series"]
            periods = segment_periods(segment_rows, findings["identity"])
            lines += ["分段并列 **[Derived]** — 来源：`tables/segment_series.csv`", ""]
            lines += segment_table(segment_rows, periods) + [""]
        for stem in stems:
            if stem in charts:
                lines += [
                    f"![{stem}]({charts[stem]['png']['path']})",
                    "",
                    f"图表数据：`{charts[stem]['chart_data']['path']}`",
                    "",
                ]
            elif stem in skipped:
                lines += [f"*未出图 {stem}：{skipped[stem]}。*", ""]
        lines += [f"- {line(item)}" for item in items] or ["- 本节没有可报告的结果。"]
        lines.append("")
    lines += ["## 不能下的结论", "", *[f"- {item}" for item in findings["boundaries"]], ""]
    if findings.get("proxy_notes"):
        lines += [
            "## 口径与代理变量",
            "",
            "每个代理都列出来源字段、公式、分母以及解释边界 **[Measured/Derived]** — 来源：`tables/proxy_notes.csv`",
            "",
            *proxy_notes_table(findings["proxy_notes"]),
            "",
        ]
    if findings.get("next_steps"):
        lines += [
            "## 下一步菜单",
            "",
            "菜单最多三项，按未解决的关税处理、缺失的产品份额和异常口径依次排序；它不会自动联网或下载数据。",
            "",
            *next_steps_table(findings["next_steps"]),
            "",
        ]
    lines += [
        "## 交付文件",
        "",
        "- 数据集：`processed/trade_tidy.csv`、`processed/trade_panel.csv`、`tables/*.csv`、`tables/event_metrics.json`",
        "- 清单：`analysis_manifest.json`（输入快照哈希、data_through、验证结果）、`chart_manifest.json`、`findings.json`",
        "- 每张图的数据：`charts/<图名>.csv`",
        "",
    ]
    report = output_dir / "report.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    key_points = output_dir / "key_points.md"
    header = f"# 要点：{identity['study_title'] or identity['product_name']}\n\n"
    if gate.get("mode") == "legacy_replay":
        header += "> 回放结果：scope 未批准，HTS 连续性未核实。\n\n"
    key_points.write_text(header + "\n".join(key_lines) + "\n", encoding="utf-8")
    return report, key_points
