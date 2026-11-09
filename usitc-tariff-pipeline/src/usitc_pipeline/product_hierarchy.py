"""HTS6 -> HTS8 -> HTS10 product hierarchy and import-composition matrix.

The HTS hierarchy and trade data answer different questions:

* HTS8 is the legal tariff-rate line.
* HTS10 is the statistical product category used for the product mix.
* An HTS10 share is measured against an *independent* HTS6 World query.

The last rule is deliberately fail-closed.  When the study only has HTS6 data,
or when the HTS10 and HTS6 periods do not match, this module leaves the share
blank and names the missing input instead of normalizing available HTS10 rows
to 100 percent.  The result is an import customs-value composition proxy, not
market share or global production capacity.
"""

from __future__ import annotations

from html import unescape
import re
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd


MATRIX_COLUMNS = (
    "hts6_code",
    "hts6_description",
    "hts8_code",
    "hts8_description",
    "hts10_code",
    "hts10_description",
    "quantity_unit",
    "hts8_mfn_rate",
    "additional_tariff_status",
    "additional_tariff_rate",
    "additional_tariff_note",
    "latest_full_period_import_value_usd",
    "share_of_parent_hts6_pct",
    "focal_origin",
    "focal_origin_import_value_usd",
    "focal_origin_source_share_pct",
    "classification_release",
    "data_period",
    "continuity_status",
    "trade_data_status",
    "product_share_status",
    "share_proxy_definition",
    "share_proxy_limit",
    "origin_proxy_limit",
)

CHART_COLUMNS = (
    "hts8_code",
    "hts8_description",
    "hts10_code",
    "hts10_description",
    "latest_full_period_import_value_usd",
    "share_of_parent_hts6_pct",
    "focal_origin",
    "focal_origin_import_value_usd",
    "focal_origin_source_share_pct",
    "display_mode",
    "chart_status",
    "data_period",
    "product_share_status",
    "sort_order",
    "proxy_note",
)

WORLD_NAMES = {"world", "total", "all countries", "all countries/areas", "all origins"}


class ProductHierarchyError(ValueError):
    """The hierarchy or optional HTS10 trade panel is internally inconsistent."""


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _text(value: Any) -> str:
    if value is None or (not isinstance(value, (list, tuple, dict)) and pd.isna(value)):
        return ""
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value)))).strip()


def _units(value: Any) -> str:
    if value is None:
        return ""
    values = value if isinstance(value, (list, tuple)) else [value]
    return "; ".join(dict.fromkeys(_text(item).lower() for item in values if _text(item)))


def _safe_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float("nan")
    return number if np.isfinite(number) else float("nan")


def _rate_for_hts8(by_code: Mapping[str, Mapping[str, Any]], code8: str) -> str:
    direct = _text((by_code.get(code8) or {}).get("general"))
    if direct:
        return direct
    child_rates = {
        _text(row.get("general"))
        for code, row in by_code.items()
        if len(code) == 10 and code.startswith(code8) and _text(row.get("general"))
    }
    return child_rates.pop() if len(child_rates) == 1 else ""


def _description(by_code: Mapping[str, Mapping[str, Any]], code: str) -> str:
    row = by_code.get(code) or {}
    return _text(row.get("description") or row.get("briefDescription") or row.get("longDescription"))


def _as_records(value: pd.DataFrame | Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    return [dict(row) for row in value]


def _treatment_for(code10: str, treatments: Sequence[Mapping[str, Any]]) -> tuple[str, str, str]:
    """Map an already-resolved tariff snapshot onto an HTS10 line.

    Legal lifecycle, exclusions and stacking belong in the tariff-treatment
    resolver.  This mapper only takes the longest matching product code, so a
    resolved HTS10 result overrides HTS8, HTS6 and generic snapshot rows.
    """

    candidates: list[tuple[int, Mapping[str, Any]]] = []
    for row in treatments:
        prefix = _digits(row.get("hts_code") or row.get("product_code") or row.get("hts"))
        if (not prefix or code10.startswith(prefix)) and len(prefix) <= 10:
            candidates.append((len(prefix), row))
    if not candidates:
        return "Unresolved", "", "No resolved HTS tariff-treatment snapshot was supplied."
    _, row = max(candidates, key=lambda item: item[0])
    status = _text(row.get("treatment_status") or row.get("status")) or "Unresolved"
    rate = _text(
        row.get("additional_tariff_rate")
        or row.get("additional_rate_formula")
        or row.get("additional_rate_text")
        or row.get("combined_formula")
        or row.get("treatment_formula")
        or row.get("rate")
    )
    note = _text(row.get("note") or row.get("reason") or row.get("limitations"))
    return status, rate, note


def _selected_trade(
    trade_rows: pd.DataFrame | Sequence[Mapping[str, Any]] | None,
    *,
    parent_hts6: str,
    full_period: str | None,
) -> pd.DataFrame:
    rows = _as_records(trade_rows)
    if not rows or not full_period:
        return pd.DataFrame()
    frame = pd.DataFrame(rows)
    required = {"period", "hts_code", "origin_name", "customs_value_usd"}
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ProductHierarchyError(f"HTS10 trade rows lack required columns: {', '.join(missing)}")
    frame["hts_code"] = frame["hts_code"].map(_digits)
    frame = frame[
        frame["period"].astype(str).eq(str(full_period))
        & frame["hts_code"].str.startswith(parent_hts6)
        & frame["hts_code"].str.len().eq(10)
    ].copy()
    if "frequency" in frame:
        frame = frame[frame["frequency"].astype(str).str.casefold().eq("annual")].copy()
    if frame.empty:
        return frame
    frame["origin_name"] = frame["origin_name"].astype(str).str.strip()
    frame["customs_value_usd"] = pd.to_numeric(frame["customs_value_usd"], errors="coerce")
    duplicates = frame.duplicated(["hts_code", "origin_name"], keep=False)
    if duplicates.any():
        examples = frame.loc[duplicates, ["hts_code", "origin_name"]].drop_duplicates().head(3)
        labels = ", ".join(f"{row.hts_code}/{row.origin_name}" for row in examples.itertuples())
        raise ProductHierarchyError(f"Duplicate HTS10-period-origin trade rows: {labels}")
    return frame


def build_product_hierarchy_matrix(
    structure: Sequence[Mapping[str, Any]],
    *,
    parent_hts6: str,
    trade_rows: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    full_period: str | None = None,
    independent_hts6_world_value_usd: float | None = None,
    independent_hts6_world_period: str | None = None,
    tariff_treatments: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    classification_release: str = "",
    continuity_status: str = "unverified",
    focal_origin: str = "China",
    parent_description: str = "",
) -> pd.DataFrame:
    """Build one row per current HTS10 statistical category below an HTS6.

    ``trade_rows`` must be an annual, full-period HTS10 DataWeb panel that
    retains the independent ``World`` query.  ``independent_hts6_world_*`` is
    supplied separately and must cover the same period.  Supplying only the
    hierarchy or only an HTS6 study therefore never manufactures product-mix
    shares.
    """

    parent = _digits(parent_hts6)
    if len(parent) != 6:
        raise ProductHierarchyError(f"parent_hts6 must contain exactly 6 digits, got {parent_hts6!r}")
    by_code = {
        _digits(row.get("htsno") or row.get("hts_code")): row
        for row in structure
        if len(_digits(row.get("htsno") or row.get("hts_code"))) in {6, 8, 10}
        and _digits(row.get("htsno") or row.get("hts_code")).startswith(parent)
    }
    if not by_code:
        raise ProductHierarchyError(f"HTS structure has no rows under {parent_hts6}")

    hts8_codes = sorted({code[:8] for code in by_code if len(code) in {8, 10}})
    hts10_codes = sorted(code for code in by_code if len(code) == 10)
    trade = _selected_trade(trade_rows, parent_hts6=parent, full_period=full_period)
    treatments = _as_records(tariff_treatments)
    denominator = _safe_number(independent_hts6_world_value_usd)
    denominator_ok = (
        bool(full_period)
        and str(independent_hts6_world_period or "") == str(full_period)
        and np.isfinite(denominator)
        and denominator > 0
    )

    if not parent_description:
        parent_description = _description(by_code, parent)
    result: list[dict[str, Any]] = []
    for code8 in hts8_codes:
        children = [code for code in hts10_codes if code.startswith(code8)] or [""]
        for code10 in children:
            structure_row = by_code.get(code10) or by_code.get(code8) or {}
            unit = _units(structure_row.get("units"))
            status, additional_rate, tariff_note = _treatment_for(code10 or code8, treatments)

            world_value = float("nan")
            focal_value = float("nan")
            focal_share = float("nan")
            if not full_period:
                trade_status = "unavailable_missing_full_period"
            elif trade.empty:
                trade_status = "unavailable_missing_hts10_trade_rows"
            elif not code10:
                trade_status = "unavailable_missing_hts10_structure"
            else:
                product = trade[trade["hts_code"].eq(code10)]
                world = product[
                    product["origin_name"].str.casefold().isin(WORLD_NAMES)
                ]["customs_value_usd"]
                if world.empty or pd.isna(world.iloc[0]):
                    trade_status = "unavailable_missing_independent_hts10_world_query"
                else:
                    trade_status = "measured"
                    world_value = float(world.iloc[0])
                    focal = product[product["origin_name"].str.casefold().eq(focal_origin.casefold())][
                        "customs_value_usd"
                    ]
                    focal_value = 0.0 if focal.empty or pd.isna(focal.iloc[0]) else float(focal.iloc[0])
                    focal_share = focal_value / world_value * 100 if world_value > 0 else float("nan")

            if trade_status != "measured":
                share_status = trade_status
                product_share = float("nan")
            elif not np.isfinite(denominator):
                share_status = "unavailable_missing_independent_hts6_world_query"
                product_share = float("nan")
            elif str(independent_hts6_world_period or "") != str(full_period):
                share_status = "unavailable_hts6_hts10_period_mismatch"
                product_share = float("nan")
            elif denominator <= 0:
                share_status = "unavailable_nonpositive_hts6_world_value"
                product_share = float("nan")
            elif denominator_ok:
                share_status = "measured"
                product_share = world_value / denominator * 100
            else:  # Defensive: every denominator failure above should be named.
                share_status = "unavailable_independent_hts6_denominator"
                product_share = float("nan")

            period_label = str(full_period or "")
            definition = (
                f"HTS10 World imports for consumption customs value / independent HTS6 {parent} "
                f"World customs value for {period_label}."
                if period_label
                else "HTS10/HTS6 product-share period not selected."
            )
            result.append({
                "hts6_code": parent,
                "hts6_description": parent_description,
                "hts8_code": code8,
                "hts8_description": _description(by_code, code8),
                "hts10_code": code10 or pd.NA,
                "hts10_description": _description(by_code, code10) if code10 else "",
                "quantity_unit": unit,
                "hts8_mfn_rate": _rate_for_hts8(by_code, code8),
                "additional_tariff_status": status,
                "additional_tariff_rate": additional_rate,
                "additional_tariff_note": tariff_note,
                "latest_full_period_import_value_usd": world_value,
                "share_of_parent_hts6_pct": product_share,
                "focal_origin": focal_origin,
                "focal_origin_import_value_usd": focal_value,
                "focal_origin_source_share_pct": focal_share,
                "classification_release": classification_release,
                "data_period": period_label,
                "continuity_status": continuity_status,
                "trade_data_status": trade_status,
                "product_share_status": share_status,
                "share_proxy_definition": definition,
                "share_proxy_limit": (
                    "Import customs-value composition only; not market share, revenue share, installed capacity, "
                    "or global production capacity."
                ),
                "origin_proxy_limit": (
                    f"{focal_origin} means reported country of origin; it does not identify value added, "
                    "firm dependence, ownership, or transshipment."
                ),
            })
    return pd.DataFrame(result, columns=MATRIX_COLUMNS)


def product_mix_layout(matrix: pd.DataFrame) -> dict[str, Any]:
    """Choose a compact chart layout without hiding unavailable shares."""

    if matrix.empty:
        return {
            "display_mode": "unavailable",
            "collapse_hts8": False,
            "hts8_count": 0,
            "hts10_count": 0,
            "chart_status": "unavailable_empty_hierarchy",
        }
    hts8_count = int(matrix["hts8_code"].dropna().nunique())
    hts10_count = int(matrix["hts10_code"].dropna().nunique())
    measured = matrix["product_share_status"].eq("measured") & matrix["share_of_parent_hts6_pct"].notna()
    if measured.all():
        chart_status = "available"
    elif measured.any():
        chart_status = "partial_unavailable_rows"
    else:
        chart_status = "unavailable_needs_hts10_trade_and_independent_hts6_world_data"
    return {
        "display_mode": "horizontal_bar_single_hts8" if hts8_count == 1 else "grouped_horizontal_bar",
        "collapse_hts8": hts8_count == 1,
        "hts8_count": hts8_count,
        "hts10_count": hts10_count,
        "chart_status": chart_status,
    }


def build_product_mix_chart_data(matrix: pd.DataFrame) -> pd.DataFrame:
    """Return chart-ready rows; rendering remains a separate pipeline step."""

    missing = sorted(set(MATRIX_COLUMNS) - set(matrix.columns))
    if missing:
        raise ProductHierarchyError(f"Product hierarchy matrix lacks columns: {', '.join(missing)}")
    layout = product_mix_layout(matrix)
    if matrix.empty:
        return pd.DataFrame(columns=CHART_COLUMNS)
    data = matrix.copy()
    data["display_mode"] = layout["display_mode"]
    data["chart_status"] = layout["chart_status"]
    data["_share_sort"] = data["share_of_parent_hts6_pct"].fillna(-1)
    data = data.sort_values(
        ["hts8_code", "_share_sort", "hts10_code"], ascending=[True, False, True], kind="stable"
    ).reset_index(drop=True)
    data["sort_order"] = np.arange(1, len(data) + 1)
    focal = data["focal_origin"].fillna("").astype(str)
    data["proxy_note"] = (
        data["share_proxy_definition"]
        + " Focal-origin share = "
        + focal
        + " customs value / HTS10 World customs value for the same period. "
        + data["share_proxy_limit"]
        + " "
        + data["origin_proxy_limit"]
    )
    return data[list(CHART_COLUMNS)]
