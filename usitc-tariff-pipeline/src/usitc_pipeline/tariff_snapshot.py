"""Resolve a non-binding HTS tariff-treatment snapshot at product level.

The sourcing pipeline studies an industry at HTS6, while legal tariff
treatment can split at HTS8 or HTS10.  This module therefore resolves each
supplied product line separately.  It deliberately does not turn a realized
duty ratio into a statutory rate and does not try to reproduce a CBP entry
calculation.

V1 covers the Column 1-General rate plus verified Chapter 99 policy layers for
ordinary consumption entries.  It preserves the published rate text (for
example ``$0.05/kg + 3%``) instead of coercing every treatment to an ad-valorem
percentage.  Special-program claims, Chapter 98, quota/TRQ treatment,
warehouse/FTZ or other special entries, AD/CVD, firm-specific rates and a
request for a binding classification are reported as conditional/out of
scope.

The resolver is pure Python and accepts dictionaries so the owning pipeline
can populate it from an HTS release and the tariff registry without coupling
the legal logic to a particular file format.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import re
from typing import Any, Iterable, Mapping

from .registry import origins_overlap
from .timeline import split_list


SNAPSHOT_STATUSES = ("Resolved", "Mixed", "Conditional", "Unresolved")
POLICY_ACTIONS = ("add", "replace", "exempt", "end")
POLICY_COVERAGES = ("applies", "exempt", "not_covered", "suspended", "unresolved")
RATE_TYPES = ("ad_valorem", "specific", "compound", "free", "other")
SUPPORTED_GENERAL_COLUMNS = {
    "general",
    "column 1-general",
    "column 1 - general",
    "column_1_general",
    "column1_general",
}
SUPPORTED_ENTRY_TYPES = {"consumption", "standard_consumption", "ordinary_consumption"}


class TariffSnapshotError(ValueError):
    """The requested snapshot or one of its input rows is structurally invalid."""


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _iso(value: object, where: str, *, blank: bool = False) -> date | None:
    text = str(value or "").strip()
    if blank and not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise TariffSnapshotError(f"{where} must be YYYY-MM-DD, got {text!r}") from exc


def _active(row: Mapping[str, Any], on: date) -> bool:
    start = _iso(row.get("effective_from") or row.get("date"), "effective_from")
    end = _iso(row.get("effective_to"), "effective_to", blank=True)
    return bool(start and start <= on and (end is None or on <= end))


def _verified(row: Mapping[str, Any]) -> bool:
    return str(row.get("status") or "").strip().lower() == "verified"


def _source(row: Mapping[str, Any]) -> str:
    for key in ("source", "citation", "sources", "evidence"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _normal_column(value: object) -> str:
    normalized = re.sub(r"\s+", " ", str(value or "general").strip().lower())
    return "general" if normalized in SUPPORTED_GENERAL_COLUMNS else normalized


def _preference_is_none(value: object) -> bool:
    return str(value or "none").strip().lower() in {"", "none", "no", "n/a", "not_claimed"}


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "y"}


def _origin_matches(rule: Mapping[str, Any], origin: str) -> bool:
    text = str(rule.get("origins") or rule.get("origin") or "").strip()
    return bool(text and origins_overlap(text, origin))


def _format_hts(digits: str) -> str:
    if len(digits) <= 4:
        return digits
    if len(digits) <= 6:
        return f"{digits[:4]}.{digits[4:]}"
    if len(digits) <= 8:
        return f"{digits[:4]}.{digits[4:6]}.{digits[6:]}"
    return f"{digits[:4]}.{digits[4:6]}.{digits[6:8]}.{digits[8:]}"


def _validate_product(product: Mapping[str, Any], number: int) -> dict[str, Any]:
    code = _digits(product.get("hts") or product.get("hts10") or product.get("hts8") or product.get("hts6"))
    if len(code) not in {6, 8, 10}:
        raise TariffSnapshotError(f"product {number}: hts must contain 6, 8 or 10 digits")
    return {**dict(product), "hts": _format_hts(code), "hts_digits": code}


def _validate_rate_row(row: Mapping[str, Any], number: int) -> dict[str, Any]:
    code = _digits(row.get("hts"))
    if len(code) not in {6, 8, 10}:
        raise TariffSnapshotError(f"base rate {number}: hts must contain 6, 8 or 10 digits")
    rate_text = str(row.get("rate_text") or row.get("rate") or "").strip()
    if not rate_text:
        raise TariffSnapshotError(f"base rate {number}: rate_text is required")
    rate_type = str(row.get("rate_type") or "other").strip().lower()
    if rate_type not in RATE_TYPES:
        raise TariffSnapshotError(f"base rate {number}: rate_type must be one of {list(RATE_TYPES)}")
    _iso(row.get("effective_from") or row.get("date"), f"base rate {number} effective_from")
    _iso(row.get("effective_to"), f"base rate {number} effective_to", blank=True)
    return {
        **dict(row),
        "hts_digits": code,
        "rate_text": rate_text,
        "rate_type": rate_type,
        "rate_column": _normal_column(row.get("rate_column")),
    }


def _validate_measure(row: Mapping[str, Any], number: int) -> dict[str, Any]:
    measure_id = str(row.get("measure_id") or "").strip()
    if not measure_id:
        raise TariffSnapshotError(f"policy measure {number}: measure_id is required")
    action = str(row.get("action") or "").strip().lower()
    if action not in POLICY_ACTIONS:
        raise TariffSnapshotError(f"policy measure {measure_id}: action must be one of {list(POLICY_ACTIONS)}")
    rate_text = str(row.get("rate_text") or row.get("change") or "").strip()
    if action in {"add", "replace"} and not rate_text:
        raise TariffSnapshotError(f"policy measure {measure_id}: {action} requires rate_text")
    rate_type = str(row.get("rate_type") or "other").strip().lower()
    if action in {"add", "replace"} and rate_type not in RATE_TYPES:
        raise TariffSnapshotError(f"policy measure {measure_id}: rate_type must be one of {list(RATE_TYPES)}")
    _iso(row.get("effective_from") or row.get("date"), f"policy measure {measure_id} effective_from")
    _iso(row.get("effective_to"), f"policy measure {measure_id} effective_to", blank=True)
    supersedes = split_list(str(row.get("supersedes") or ""))
    return {
        **dict(row),
        "measure_id": measure_id,
        "action": action,
        "rate_text": rate_text,
        "rate_type": rate_type,
        "supersedes_ids": supersedes,
        "stack_group": str(row.get("stack_group") or "").strip(),
        "layer": str(row.get("layer") or "Chapter 99").strip(),
        "heading": str(row.get("heading") or row.get("headings") or "").strip(),
        "sequence": int(row.get("sequence") or 0),
    }


def _validate_coverage(row: Mapping[str, Any], number: int) -> dict[str, Any]:
    measure_id = str(row.get("measure_id") or "").strip()
    if not measure_id:
        raise TariffSnapshotError(f"coverage {number}: measure_id is required")
    code = _digits(row.get("hts"))
    if code and len(code) not in {6, 8, 10}:
        raise TariffSnapshotError(f"coverage {number}: hts must be blank/generic or contain 6, 8 or 10 digits")
    value = str(row.get("coverage") or "").strip().lower()
    if value == "covered":
        value = "applies"
    if value not in POLICY_COVERAGES:
        raise TariffSnapshotError(f"coverage {number}: coverage must be one of {list(POLICY_COVERAGES)}")
    return {**dict(row), "measure_id": measure_id, "hts_digits": code, "coverage": value}


def _conditional_reasons(context: Mapping[str, Any]) -> list[str]:
    reasons: list[str] = []
    column = _normal_column(context.get("rate_column"))
    if column not in SUPPORTED_GENERAL_COLUMNS:
        reasons.append("V1 resolves Column 1-General only; another rate column was requested")
    if not _preference_is_none(context.get("preference_claim")):
        reasons.append("a special tariff-program or preference claim requires claim-specific eligibility review")
    entry_type = str(context.get("entry_type") or "consumption").strip().lower()
    if entry_type not in SUPPORTED_ENTRY_TYPES:
        reasons.append("warehouse, FTZ or another special entry type is outside the ordinary-consumption V1")
    flags = (
        ("chapter_98", "Chapter 98 treatment is outside V1"),
        ("quota_or_trq", "quota/TRQ treatment is outside V1"),
        ("ad_cvd", "AD/CVD orders and cash-deposit rates are outside V1"),
        ("firm_specific_rate", "a firm-specific rate is outside V1"),
        ("requires_binding_classification", "only CBP can provide a binding classification or final entry determination"),
    )
    for key, message in flags:
        if _bool(context.get(key)):
            reasons.append(message)
    return reasons


def _base_rate(
    product_code: str,
    base_rates: list[dict[str, Any]],
    *,
    on: date,
    rate_column: str,
) -> tuple[dict[str, Any] | None, list[str], list[dict[str, Any]]]:
    matches = [
        row for row in base_rates
        if product_code.startswith(row["hts_digits"])
        and row["rate_column"] == rate_column
        and _active(row, on)
    ]
    if not matches:
        return None, ["no active base-rate row matches this product and rate column"], []
    specificity = max(len(row["hts_digits"]) for row in matches)
    selected = [row for row in matches if len(row["hts_digits"]) == specificity]
    unresolved: list[str] = []
    if any(not _verified(row) for row in selected):
        unresolved.append("the most-specific base-rate row is not verified")
    if any(not _source(row) for row in selected):
        unresolved.append("the most-specific base-rate row has no source citation")
    signatures = {(row["rate_text"], row["rate_type"]) for row in selected}
    if len(signatures) != 1:
        unresolved.append("conflicting most-specific base-rate rows")
    if unresolved:
        return None, unresolved, selected
    row = selected[0]
    return {
        "hts": _format_hts(row["hts_digits"]),
        "rate_text": row["rate_text"],
        "rate_type": row["rate_type"],
        "rate_column": row["rate_column"],
        "source": _source(row),
        "status": row.get("status", ""),
    }, [], selected


def _coverage_for(
    measure_id: str,
    product_code: str,
    coverage_by_measure: Mapping[str, list[dict[str, Any]]],
) -> tuple[dict[str, Any] | None, list[str], list[dict[str, Any]]]:
    rows = coverage_by_measure.get(measure_id, [])
    descendants = [row for row in rows if row["hts_digits"].startswith(product_code) and len(row["hts_digits"]) > len(product_code)]
    if descendants:
        codes = ", ".join(sorted({_format_hts(row["hts_digits"]) for row in descendants}))
        return None, [f"finer coverage exists at {codes}; expand this product to those HTS lines"], descendants
    matches = [row for row in rows if not row["hts_digits"] or product_code.startswith(row["hts_digits"])]
    if not matches:
        return None, ["coverage is missing (fail closed)"], []
    specificity = max(len(row["hts_digits"]) for row in matches)
    selected = [row for row in matches if len(row["hts_digits"]) == specificity]
    if any(not _verified(row) for row in selected):
        return None, ["the most-specific coverage row is not verified"], selected
    if any(not _source(row) for row in selected):
        return None, ["the most-specific coverage row has no source evidence"], selected
    values = {row["coverage"] for row in selected}
    if len(values) != 1:
        return None, ["conflicting most-specific coverage rows"], selected
    return selected[0], [], selected


def _policy_treatment(
    product_code: str,
    measures: list[dict[str, Any]],
    coverage_by_measure: Mapping[str, list[dict[str, Any]]],
    *,
    origin: str,
    on: date,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    relevant = [row for row in measures if _origin_matches(row, origin) and _active(row, on)]
    relevant.sort(key=lambda row: (_iso(row.get("effective_from") or row.get("date"), "effective_from"), row["sequence"], row["measure_id"]))
    applied: dict[str, dict[str, Any]] = {}
    excluded: list[dict[str, Any]] = []
    lifecycle: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for measure in relevant:
        coverage, coverage_issues, selected = _coverage_for(measure["measure_id"], product_code, coverage_by_measure)
        if coverage_issues:
            unresolved.extend(f"{measure['measure_id']}: {issue}" for issue in coverage_issues)
            continue
        assert coverage is not None
        if coverage["coverage"] != "applies":
            excluded.append(
                {
                    "measure_id": measure["measure_id"],
                    "heading": measure["heading"],
                    "coverage": coverage["coverage"],
                    "matched_hts": _format_hts(coverage["hts_digits"]) if coverage["hts_digits"] else "generic",
                    "evidence": _source(coverage),
                }
            )
            continue
        if not _verified(measure):
            unresolved.append(f"{measure['measure_id']}: policy measure is not verified")
            continue
        if not _source(measure):
            unresolved.append(f"{measure['measure_id']}: policy measure has no primary-source citation")
            continue
        if measure["action"] in {"add", "replace"} and not measure["stack_group"]:
            unresolved.append(f"{measure['measure_id']}: an additive/replacement policy layer needs an explicit stack_group")
            continue
        if measure["action"] in {"replace", "exempt", "end"} and not measure["supersedes_ids"]:
            unresolved.append(f"{measure['measure_id']}: {measure['action']} needs explicit supersedes targets")
            continue

        removed: list[str] = []
        if measure["action"] in {"replace", "exempt", "end"}:
            for target in measure["supersedes_ids"]:
                if target in applied:
                    applied.pop(target)
                    removed.append(target)
            lifecycle.append(
                {
                    "measure_id": measure["measure_id"],
                    "action": measure["action"],
                    "supersedes": "; ".join(measure["supersedes_ids"]),
                    "removed_active_layers": "; ".join(removed),
                    "effective_from": str(measure.get("effective_from") or measure.get("date") or ""),
                    "source": _source(measure),
                }
            )
        if measure["action"] in {"add", "replace"}:
            applied[measure["measure_id"]] = measure

    groups: dict[str, list[str]] = defaultdict(list)
    for measure in applied.values():
        groups[measure["stack_group"]].append(measure["measure_id"])
    for group, ids in groups.items():
        if len(ids) > 1:
            unresolved.append(
                f"stack_group {group!r} has multiple active measures ({', '.join(sorted(ids))}); stacking is not assumed"
            )

    layers = [
        {
            "measure_id": row["measure_id"],
            "heading": row["heading"],
            "layer": row["layer"],
            "action": row["action"],
            "rate_text": row["rate_text"],
            "rate_type": row["rate_type"],
            "stack_group": row["stack_group"],
            "effective_from": str(row.get("effective_from") or row.get("date") or ""),
            "effective_to": str(row.get("effective_to") or ""),
            "source": _source(row),
        }
        for row in applied.values()
    ]
    layers.sort(key=lambda row: (row["stack_group"], row["effective_from"], row["measure_id"]))
    return layers, excluded, lifecycle, list(dict.fromkeys(unresolved))


def _formula(base: Mapping[str, Any] | None, layers: Iterable[Mapping[str, Any]]) -> str:
    components = []
    if base:
        components.append(f"Column 1-General {base['rate_text']}")
    components.extend(f"{row['heading'] or row['measure_id']} {row['rate_text']}" for row in layers)
    return " + ".join(components)


def _treatment_signature(row: Mapping[str, Any]) -> tuple[Any, ...]:
    base = row.get("base_rate") or {}
    layers = row.get("chapter_99_layers") or []
    return (
        base.get("rate_text"),
        base.get("rate_type"),
        tuple((item.get("measure_id"), item.get("rate_text"), item.get("rate_type")) for item in layers),
    )


def build_tariff_treatment_snapshot(
    products: Iterable[Mapping[str, Any]],
    base_rates: Iterable[Mapping[str, Any]],
    policy_measures: Iterable[Mapping[str, Any]],
    policy_coverage: Iterable[Mapping[str, Any]],
    *,
    origin: str,
    entry_date: str,
    rate_column: str = "general",
    preference_claim: str | None = None,
    entry_type: str = "consumption",
    chapter_98: bool = False,
    quota_or_trq: bool = False,
    ad_cvd: bool = False,
    firm_specific_rate: bool = False,
    requires_binding_classification: bool = False,
) -> dict[str, Any]:
    """Build a traceable treatment snapshot for supplied HTS product lines.

    ``products`` should normally be the complete HTS10 children of the HTS6
    industry scope.  Each base-rate row needs ``hts``, ``rate_text``,
    ``rate_type``, ``rate_column``, an effective date, ``status=verified`` and
    a source.  Policy measures hold legal lifecycle fields; coverage is kept in
    separate rows so absence can fail closed instead of silently meaning
    applicable.
    """

    if not str(origin or "").strip():
        raise TariffSnapshotError("origin is required")
    on = _iso(entry_date, "entry_date")
    assert on is not None
    product_rows = [_validate_product(row, i) for i, row in enumerate(products, start=1)]
    if not product_rows:
        raise TariffSnapshotError("at least one product line is required")
    if len({row["hts_digits"] for row in product_rows}) != len(product_rows):
        raise TariffSnapshotError("product HTS lines must be unique")
    base_rows = [_validate_rate_row(row, i) for i, row in enumerate(base_rates, start=1)]
    measure_rows = [_validate_measure(row, i) for i, row in enumerate(policy_measures, start=1)]
    if len({row["measure_id"] for row in measure_rows}) != len(measure_rows):
        raise TariffSnapshotError("policy measure_id values must be unique lifecycle rows")
    coverage_rows = [_validate_coverage(row, i) for i, row in enumerate(policy_coverage, start=1)]
    known = {row["measure_id"] for row in measure_rows}
    unknown = sorted({row["measure_id"] for row in coverage_rows} - known)
    if unknown:
        raise TariffSnapshotError(f"coverage names unknown policy measures: {unknown}")
    coverage_by_measure: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in coverage_rows:
        coverage_by_measure[row["measure_id"]].append(row)

    context = {
        "rate_column": rate_column,
        "preference_claim": preference_claim,
        "entry_type": entry_type,
        "chapter_98": chapter_98,
        "quota_or_trq": quota_or_trq,
        "ad_cvd": ad_cvd,
        "firm_specific_rate": firm_specific_rate,
        "requires_binding_classification": requires_binding_classification,
    }
    conditional = _conditional_reasons(context)
    normalized_column = _normal_column(rate_column)
    rows: list[dict[str, Any]] = []
    for product in product_rows:
        base, base_issues, base_candidates = _base_rate(
            product["hts_digits"], base_rows, on=on, rate_column=normalized_column
        )
        layers, excluded, lifecycle, policy_issues = _policy_treatment(
            product["hts_digits"], measure_rows, coverage_by_measure, origin=origin, on=on
        )
        unresolved = [*base_issues, *policy_issues]
        status = "Unresolved" if unresolved else "Conditional" if conditional else "Resolved"
        rows.append(
            {
                "hts": product["hts"],
                "hts_digits": product["hts_digits"],
                "description": str(product.get("description") or ""),
                "origin": origin,
                "entry_date": entry_date,
                "rate_column": rate_column,
                "preference_claim": preference_claim or "none",
                "entry_type": entry_type,
                "status": status,
                "base_rate": base,
                "chapter_99_layers": layers,
                "treatment_formula": _formula(base, layers),
                "excluded_measures": excluded,
                "lifecycle_events": lifecycle,
                "unresolved_reasons": list(dict.fromkeys(unresolved)),
                "conditional_reasons": conditional,
                "base_rate_candidates": [
                    {
                        "hts": _format_hts(item["hts_digits"]),
                        "rate_text": item["rate_text"],
                        "status": item.get("status", ""),
                        "source": _source(item),
                    }
                    for item in base_candidates
                ],
            }
        )

    row_statuses = {row["status"] for row in rows}
    if "Unresolved" in row_statuses:
        snapshot_status = "Unresolved"
    elif "Conditional" in row_statuses:
        snapshot_status = "Conditional"
    elif len({_treatment_signature(row) for row in rows}) > 1:
        snapshot_status = "Mixed"
        for row in rows:
            row["status"] = "Mixed"
    else:
        snapshot_status = "Resolved"

    return {
        "snapshot_status": snapshot_status,
        "origin": origin,
        "entry_date": entry_date,
        "rate_column": rate_column,
        "preference_claim": preference_claim or "none",
        "entry_type": entry_type,
        "rows": rows,
        "scope_counts": {
            "products": len(rows),
            "resolved": sum(row["status"] == "Resolved" for row in rows),
            "mixed": sum(row["status"] == "Mixed" for row in rows),
            "conditional": sum(row["status"] == "Conditional" for row in rows),
            "unresolved": sum(row["status"] == "Unresolved" for row in rows),
        },
        "limits": [
            "Non-binding research snapshot; it is not a CBP classification ruling or entry liquidation.",
            "V1 covers Column 1-General plus verified Chapter 99 layers for ordinary consumption entries.",
            "Published specific and compound formulas are preserved; the snapshot does not coerce them to a percentage.",
            "AD/CVD, Chapter 98, quota/TRQ, preference claims, firm-specific and special-entry treatment require separate review.",
        ],
    }


def snapshot_table_rows(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Flatten a snapshot into CSV-ready rows without discarding formula text."""

    result: list[dict[str, Any]] = []
    for row in snapshot.get("rows", []):
        base = row.get("base_rate") or {}
        layers = row.get("chapter_99_layers") or []
        result.append(
            {
                "hts": row.get("hts", ""),
                "description": row.get("description", ""),
                "origin": row.get("origin", ""),
                "entry_date": row.get("entry_date", ""),
                "rate_column": row.get("rate_column", ""),
                "preference_claim": row.get("preference_claim", ""),
                "entry_type": row.get("entry_type", ""),
                "status": row.get("status", ""),
                "base_rate_text": base.get("rate_text", ""),
                "base_rate_type": base.get("rate_type", ""),
                "base_rate_source": base.get("source", ""),
                "chapter_99_headings": "; ".join(item.get("heading", "") for item in layers),
                "additional_rate_text": "; ".join(item.get("rate_text", "") for item in layers),
                "measure_ids": "; ".join(item.get("measure_id", "") for item in layers),
                "treatment_formula": row.get("treatment_formula", ""),
                "excluded_measures": "; ".join(
                    f"{item.get('measure_id', '')}:{item.get('coverage', '')}" for item in row.get("excluded_measures", [])
                ),
                "unresolved_reasons": "; ".join(row.get("unresolved_reasons", [])),
                "conditional_reasons": "; ".join(row.get("conditional_reasons", [])),
            }
        )
    return result
