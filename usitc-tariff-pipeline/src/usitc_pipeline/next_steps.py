"""Deterministic next-step menu from validated study states.

The menu does not infer causes and does not silently authorize acquisition or
web research.  It names at most three follow-ups, in a fixed priority order,
and states whether each one needs user entry details, a new DataWeb download,
or optional network research.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


NEXT_STEP_COLUMNS = (
    "priority",
    "action_id",
    "title",
    "rationale",
    "required_input",
    "expected_output",
    "needs_network_or_new_data",
)

STATUS_PRIORITY = ("unresolved", "conditional", "mixed")
INDUSTRY_CONTEXT_MODES = {"off", "light"}
MAX_NEXT_STEPS = 3


class NextStepsError(ValueError):
    """Structured inputs to the menu are invalid."""


def _records(value: pd.DataFrame | Sequence[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, pd.DataFrame):
        return value.to_dict("records")
    return [dict(row) for row in value]


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip()


def _snapshot_statuses(rows: Sequence[Mapping[str, Any]]) -> set[str]:
    statuses: set[str] = set()
    missing_status = False
    for row in rows:
        status = _text(
            row.get("treatment_status") or row.get("resolution_status") or row.get("status")
        ).casefold()
        if status:
            statuses.add(status)
        else:
            missing_status = True
    # An absent snapshot or any row with no explicit status is fail-closed.
    # Compare row-by-row: comparing the number of distinct statuses with the
    # number of rows would incorrectly mark repeated Resolved rows unresolved.
    if not rows or missing_status:
        statuses.add("unresolved")
    return statuses


def _step(
    priority: int,
    action_id: str,
    title: str,
    rationale: str,
    required_input: str,
    expected_output: str,
    need: str,
) -> dict[str, Any]:
    return {
        "priority": priority,
        "action_id": action_id,
        "title": title,
        "rationale": rationale,
        "required_input": required_input,
        "expected_output": expected_output,
        "needs_network_or_new_data": need,
    }


def build_next_steps(
    *,
    tariff_snapshot: pd.DataFrame | Sequence[Mapping[str, Any]] | None,
    scope_hts_level: int,
    product_hierarchy: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    anomaly_flags: pd.DataFrame | Sequence[Mapping[str, Any]] | None = None,
    continuity_verified: bool = True,
    industry_context_mode: str = "off",
) -> pd.DataFrame:
    """Return zero to three deterministic next steps, or one explicit no-action row.

    ``industry_context_mode='light'`` records that the user opted into a small
    network-research step.  ``off`` never proposes web research.  This function
    describes acquisition needs; it never performs them.
    """

    if isinstance(scope_hts_level, bool) or not isinstance(scope_hts_level, int) or scope_hts_level not in {6, 8, 10}:
        raise NextStepsError("scope_hts_level must be 6, 8 or 10")
    mode = _text(industry_context_mode).casefold() or "off"
    if mode not in INDUSTRY_CONTEXT_MODES:
        raise NextStepsError(f"industry_context_mode must be one of {sorted(INDUSTRY_CONTEXT_MODES)}")

    snapshot_rows = _records(tariff_snapshot)
    hierarchy_rows = _records(product_hierarchy)
    flag_rows = _records(anomaly_flags)
    candidates: list[dict[str, Any]] = []

    statuses = _snapshot_statuses(snapshot_rows)
    open_status = next((status for status in STATUS_PRIORITY if status in statuses), None)
    if open_status == "unresolved":
        candidates.append(
            _step(
                10,
                "resolve_tariff_treatment_snapshot",
                "Resolve the product-level tariff treatment",
                "At least one origin/HTS/date treatment is missing or unresolved, so tariff arithmetic remains fail-closed.",
                "Official HTS rate line and Chapter 99 notes, effective dates, origin, entry date, rate column and any preference claim.",
                "A Resolved, Mixed, Conditional or explicitly Unresolved HTS tariff-treatment snapshot with source citations.",
                "official_source_review",
            )
        )
    elif open_status == "conditional":
        candidates.append(
            _step(
                20,
                "specify_entry_conditions",
                "Specify the customs entry conditions",
                "The tariff treatment depends on an entry condition that product and origin alone cannot resolve.",
                "Rate column and preference claim, plus any Chapter 98, quota/TRQ, FTZ, warehouse or special-entry treatment.",
                "A conditional-case table showing which treatment applies under each stated entry condition.",
                "user_entry_details",
            )
        )
    elif open_status == "mixed":
        candidates.append(
            _step(
                25,
                "split_mixed_tariff_treatments",
                "Split the mixed tariff treatment by finer HTS line",
                "The parent scope contains products with different verified tariff treatments.",
                "The HTS10 statistical lines under each HTS8 legal rate line and the resolved treatment for each line.",
                "A product matrix that identifies affected and unaffected subproducts without applying one rate to the full HTS6 scope.",
                "official_source_review",
            )
        )

    shares_complete = False
    if scope_hts_level == 6:
        share_statuses = {
            _text(row.get("product_share_status")).casefold()
            for row in hierarchy_rows
            if _text(row.get("product_share_status"))
        }
        shares_complete = bool(hierarchy_rows) and share_statuses == {"measured"}
        if not shares_complete:
            unavailable = ", ".join(sorted(share_statuses)) if share_statuses else "no HTS10 product-mix table"
            candidates.append(
                _step(
                    30,
                    "acquire_hts10_product_mix",
                    "Measure the HTS10 product mix inside the HTS6 scope",
                    f"The current HTS6 cache cannot produce HTS10 shares ({unavailable}); normalizing the available children would invent a denominator.",
                    "Current HTS6→HTS8→HTS10 structure; same-period HTS10 all-origin and independent World DataWeb queries; independent parent HTS6 World customs value.",
                    "product_hierarchy_matrix.csv and chart data with HTS10 import customs-value shares, unavailable rows retained, and proxy notes.",
                    "new_dataweb_download",
                )
            )

    rules = {_text(row.get("rule")).casefold() for row in flag_rows}
    duty_rules = sorted(rule for rule in rules if rule in {"duty_below_base_rate", "duty_below_timeline"})
    if duty_rules:
        candidates.append(
            _step(
                40,
                "break_out_rate_provisions",
                "Explain the realized-duty gap with entry-program data",
                "Realized duty is below a verified base or additional-duty treatment (" + ", ".join(duty_rules) + ").",
                "DataWeb breakouts by rate provision code and import program for the flagged origin/HTS/period; dutiable value if available.",
                "A reconciliation of observed calculated duty with preference, exclusion and rate-provision usage; not a replacement for the statutory snapshot.",
                "new_dataweb_download",
            )
        )

    proxy_rules = sorted(rule for rule in rules if rule in {"unit_value_far_off", "quantity_conflict"})
    if proxy_rules:
        if shares_complete:
            candidates.append(
                _step(
                    50,
                    "extend_hts10_product_mix_history",
                    "Measure HTS10 product-mix change across comparison years",
                    "The unit-value screen is abnormal (" + ", ".join(proxy_rules) + "), but the measured HTS10 mix is only one full-year cross-section and cannot explain a change over time.",
                    "Same-basis HTS10 all-origin and World queries for both comparison years, each bound to the official HTS edition in force for that year; external product specifications only if the user authorizes them.",
                    "A year-over-year decomposition separating HTS10 composition change from within-line unit-value change, with classification bridges and proxy limits preserved.",
                    "new_data_or_optional_network",
                )
            )
        else:
            candidates.append(
                _step(
                    50,
                    "drill_down_unit_value_proxy",
                    "Test whether product mix drives the unit-value signal",
                    "The unit-value or quantity-basis screen is abnormal (" + ", ".join(proxy_rules) + "), and customs value per unit is not a product price.",
                    "Finer HTS10 rows and, where defined, the second quantity unit; external product specifications only if the user authorizes them.",
                    "A decomposition separating within-line unit value from changes in HTS product mix, with the proxy limitation preserved.",
                    "new_data_or_optional_network",
                )
            )

    if not continuity_verified:
        candidates.append(
            _step(
                60,
                "verify_hts_history",
                "Verify HTS continuity across the study window",
                "The approved scope acknowledges that historical code continuity is unverified.",
                "Archived HTS editions covering the study years and a code split/merge bridge.",
                "An HTS history record that marks each study year verified or identifies a break requiring a new scope version.",
                "official_source_review",
            )
        )

    if mode == "light":
        candidates.append(
            _step(
                70,
                "add_light_industry_context",
                "Add a light industry-cycle context screen",
                "The user opted into external context; DataWeb trade records alone do not explain the industry cycle.",
                "A narrow web-search brief using primary industry, company or government sources and a stated as-of date.",
                "A separately labeled context box whose facts and inferences are not mixed with DataWeb findings.",
                "optional_network_research",
            )
        )

    ordered = sorted(candidates, key=lambda row: (int(row["priority"]), str(row["action_id"])))[:MAX_NEXT_STEPS]
    if not ordered:
        ordered = [
            _step(
                0,
                "no_action",
                "No automatic follow-up required",
                "The supplied tariff snapshot is resolved, product shares are available when required, no targeted anomaly is present, and HTS continuity is verified.",
                "None.",
                "Retain the current validated study; add a new step only when the research question changes.",
                "none",
            )
        ]
    return pd.DataFrame(ordered, columns=NEXT_STEP_COLUMNS)
