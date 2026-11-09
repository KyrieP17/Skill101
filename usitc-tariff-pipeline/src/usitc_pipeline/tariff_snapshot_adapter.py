"""Adapt the file-backed tariff registry and an HTS structure to a snapshot.

The existing registry intentionally keeps legal evidence separate from the
timeline.  ``treatment_rules.csv`` adds only the machine-readable lifecycle
fields that must never be guessed from prose such as ``+25% -> +50%``.
Missing rules for an active, applicable measure fail closed and force the
resulting tariff-treatment snapshot to ``Unresolved``.
"""

from __future__ import annotations

import csv
from datetime import date
from hashlib import sha256
from html import unescape
import json
from pathlib import Path
import re
from typing import Any, Iterable, Mapping, Sequence

from .registry import origins_overlap
from .scope import canonical_sha256
from .tariff_snapshot import build_tariff_treatment_snapshot
from .timeline import split_list


TREATMENT_RULE_COLUMNS = (
    "measure_id",
    "effective_from",
    "effective_to",
    "action",
    "rate_text",
    "rate_type",
    "stack_group",
    "supersedes",
    "layer",
    "heading",
    "status",
    "source_ids",
    "notes",
)

RELEASE_METADATA_FIELDS = (
    "schema_version",
    "release_id",
    "effective_date",
    "structure_file",
    "source_url",
    "source_sha256",
)

DEFAULT_ENTRY_CONTEXT: dict[str, Any] = {
    "rate_column": "Column 1-General",
    "preference_claim": "none",
    "entry_type": "ordinary_consumption",
    "chapter_98": False,
    "quota_or_trq": False,
    "ad_cvd": False,
    "firm_specific_rate": False,
    "requires_binding_classification": False,
}


class TariffSnapshotAdapterError(ValueError):
    """Registry lifecycle metadata or HTS structure is not usable."""


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()


def _hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _read(path: Path, required: Iterable[str]) -> list[dict[str, str]]:
    if not path.is_file():
        raise TariffSnapshotAdapterError(f"Registry input not found: {path}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = tuple(reader.fieldnames or ())
        missing = sorted(set(required) - set(fields))
        if missing:
            raise TariffSnapshotAdapterError(f"{path.name} lacks required columns: {missing}")
        return [{key: str(value or "").strip() for key, value in row.items()} for row in reader]


def load_treatment_rules(path: str | Path) -> list[dict[str, str]]:
    """Read and validate the explicit legal lifecycle rows."""

    source = Path(path)
    rows = _read(source, TREATMENT_RULE_COLUMNS)
    seen: set[str] = set()
    for number, row in enumerate(rows, start=2):
        measure_id = row["measure_id"]
        if not measure_id or measure_id in seen:
            raise TariffSnapshotAdapterError(
                f"{source.name} line {number}: measure_id must be non-empty and unique"
            )
        seen.add(measure_id)
        try:
            date.fromisoformat(row["effective_from"])
            if row["effective_to"]:
                date.fromisoformat(row["effective_to"])
        except ValueError as exc:
            raise TariffSnapshotAdapterError(
                f"{source.name} line {number}: effective dates must be YYYY-MM-DD"
            ) from exc
        if row["action"] not in {"add", "replace", "exempt", "end"}:
            raise TariffSnapshotAdapterError(
                f"{source.name} line {number}: action must be add, replace, exempt or end"
            )
        if row["action"] in {"add", "replace"} and (not row["rate_text"] or not row["stack_group"]):
            raise TariffSnapshotAdapterError(
                f"{source.name} line {number}: add/replace needs rate_text and stack_group"
            )
        if row["action"] in {"replace", "exempt", "end"} and not split_list(row["supersedes"]):
            raise TariffSnapshotAdapterError(
                f"{source.name} line {number}: {row['action']} needs explicit supersedes targets"
            )
        if row["status"] not in {"verified", "待核对"}:
            raise TariffSnapshotAdapterError(
                f"{source.name} line {number}: status must be verified or 待核对"
            )
        if not split_list(row["source_ids"]):
            raise TariffSnapshotAdapterError(f"{source.name} line {number}: source_ids is required")
    return rows


def load_structure_release_metadata(
    path: str | Path,
    *,
    structure_path: str | Path,
    expected_release_id: str,
    expected_effective_date: str,
) -> dict[str, Any]:
    """Load and cross-check the release identity bound to one HTS structure.

    The raw REST search response has no release fields of its own.  A snapshot
    therefore requires a machine-readable sidecar whose recorded SHA-256 is
    checked against the exact structure bytes.  The study's explicit release
    id and effective date must also agree with the sidecar; neither value is
    accepted as a free-form label.
    """

    metadata_path = Path(path)
    structure = Path(structure_path)
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TariffSnapshotAdapterError(
            f"Cannot read HTS release metadata {metadata_path}: {exc}"
        ) from exc
    if not isinstance(payload, dict):
        raise TariffSnapshotAdapterError("HTS release metadata must be a JSON object")
    missing = [field for field in RELEASE_METADATA_FIELDS if payload.get(field) in (None, "")]
    if missing:
        raise TariffSnapshotAdapterError(f"HTS release metadata lacks required fields: {missing}")
    if payload.get("schema_version") != 1:
        raise TariffSnapshotAdapterError("HTS release metadata schema_version must be 1")

    release_id = str(payload["release_id"]).strip()
    effective_date = str(payload["effective_date"]).strip()
    try:
        date.fromisoformat(effective_date)
    except ValueError as exc:
        raise TariffSnapshotAdapterError(
            "HTS release metadata effective_date must be YYYY-MM-DD"
        ) from exc
    if release_id != str(expected_release_id).strip():
        raise TariffSnapshotAdapterError(
            f"HTS release metadata release_id {release_id!r} does not match "
            f"tariff_snapshot.hts_release_id {expected_release_id!r}"
        )
    if effective_date != str(expected_effective_date).strip():
        raise TariffSnapshotAdapterError(
            f"HTS release metadata effective_date {effective_date!r} does not match "
            f"tariff_snapshot.hts_release_date {expected_effective_date!r}"
        )

    recorded_file = Path(str(payload["structure_file"]).strip()).name
    if recorded_file != structure.name:
        raise TariffSnapshotAdapterError(
            f"HTS release metadata structure_file {recorded_file!r} does not name {structure.name!r}"
        )
    source_url = str(payload["source_url"]).strip()
    if not re.match(r"^https://", source_url, re.I):
        raise TariffSnapshotAdapterError("HTS release metadata source_url must be an https URL")
    recorded_sha = str(payload["source_sha256"]).strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", recorded_sha):
        raise TariffSnapshotAdapterError(
            "HTS release metadata source_sha256 must contain 64 hexadecimal characters"
        )
    actual_sha = _hash(structure)
    if recorded_sha != actual_sha:
        raise TariffSnapshotAdapterError(
            "HTS release metadata source_sha256 does not match the exact structure file"
        )
    return {
        **payload,
        "release_id": release_id,
        "effective_date": effective_date,
        "structure_file": recorded_file,
        "source_url": source_url,
        "source_sha256": recorded_sha,
    }


def validate_active_scope_release(
    release_id: str,
    active_scope_releases: Iterable[str],
    *,
    entry_date: str,
) -> None:
    """Require every scope line active on the entry date to use one release."""

    releases = sorted({str(value).strip() for value in active_scope_releases if str(value).strip()})
    if releases != [str(release_id).strip()]:
        raise TariffSnapshotAdapterError(
            f"Every approved scope line active on {entry_date} must use exactly "
            f"HTS release {release_id!r}; found {releases}"
        )


def _rate_type(rate_text: str) -> str:
    normalized = rate_text.strip().casefold()
    if normalized in {"free", "free.", "0", "0%"}:
        return "free"
    has_percent = "%" in rate_text
    has_specific = bool(re.search(r"\$|¢|cents?|/\s*(kg|g|no\.?|unit|liter|litre|m2|m²)", rate_text, re.I))
    if has_percent and has_specific:
        return "compound"
    if has_percent:
        return "ad_valorem"
    if has_specific:
        return "specific"
    return "other"


def structure_snapshot_inputs(
    structure: Sequence[Mapping[str, Any]],
    *,
    parent_hts6: Iterable[str],
    classification_release: str,
    release_effective_from: str,
    source_citation: str,
    source_sha256: str = "",
    status: str = "verified",
) -> dict[str, Any]:
    """Extract current HTS10 products and their HTS8 Column 1-General rates."""

    try:
        date.fromisoformat(release_effective_from)
    except ValueError as exc:
        raise TariffSnapshotAdapterError("release_effective_from must be YYYY-MM-DD") from exc
    if status == "verified" and not source_citation:
        raise TariffSnapshotAdapterError("a verified HTS base-rate input needs a source citation")
    if status == "verified" and not re.fullmatch(r"[0-9a-fA-F]{64}", source_sha256):
        raise TariffSnapshotAdapterError("a verified HTS base-rate input needs the 64-character source SHA-256")
    parents = {_digits(code) for code in parent_hts6}
    if not parents or any(len(code) != 6 for code in parents):
        raise TariffSnapshotAdapterError("parent_hts6 must contain one or more six-digit codes")
    by_code = {
        _digits(row.get("htsno") or row.get("hts_code")): dict(row)
        for row in structure
        if len(_digits(row.get("htsno") or row.get("hts_code"))) in {6, 8, 10}
        and any(_digits(row.get("htsno") or row.get("hts_code")).startswith(parent) for parent in parents)
    }
    if not by_code:
        raise TariffSnapshotAdapterError("HTS structure has no rows under the requested HTS6 parents")

    products: list[dict[str, str]] = []
    base_rates: list[dict[str, str]] = []
    for parent in sorted(parents):
        codes10 = sorted(code for code in by_code if len(code) == 10 and code.startswith(parent))
        codes8 = sorted({code[:8] for code in by_code if len(code) in {8, 10} and code.startswith(parent)})
        if not codes10:
            products.extend(
                {"hts": code, "description": _text((by_code.get(code) or {}).get("description"))}
                for code in codes8
            )
        else:
            products.extend(
                {"hts": code, "description": _text((by_code.get(code) or {}).get("description"))}
                for code in codes10
            )
        for code8 in codes8:
            rate = _text((by_code.get(code8) or {}).get("general"))
            if not rate:
                child_rates = {
                    _text(row.get("general"))
                    for code, row in by_code.items()
                    if len(code) == 10 and code.startswith(code8) and _text(row.get("general"))
                }
                rate = child_rates.pop() if len(child_rates) == 1 else ""
            if not rate:
                # Keep a candidate row whose unverified status makes the resolver fail closed.
                rate = "Unresolved in supplied HTS structure"
                rate_status = "待核对"
            else:
                rate_status = status
            base_rates.append(
                {
                    "hts": code8,
                    "rate_text": rate,
                    "rate_type": _rate_type(rate),
                    "rate_column": "Column 1-General",
                    "effective_from": release_effective_from,
                    "status": rate_status,
                    "source": source_citation,
                    "classification_release": classification_release,
                }
            )
    return {
        "products": products,
        "base_rates": base_rates,
        "classification_release": classification_release,
        "structure_source": {
            "citation": source_citation,
            "sha256": source_sha256,
        },
    }


def _coverage_matches(code: str, products: Sequence[str]) -> bool:
    return not code or any(product.startswith(code) or code.startswith(product) for product in products)


def _covers_all_products(value: Any) -> bool:
    """Recognize registry labels that make product coverage universally broad.

    A broad origin-based measure still needs an explicit generic or
    product-specific coverage row.  This helper only identifies measures for
    which a missing row must fail closed; it never treats the label itself as
    proof that the researched product is covered.
    """

    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").casefold()).strip()
    return normalized in {
        "all",
        "all articles",
        "all goods",
        "all imports",
        "all merchandise",
        "all products",
        "all tariff lines",
    }


def registry_snapshot_inputs(
    registry_directory: str | Path,
    *,
    product_codes: Iterable[str],
    origin: str,
    entry_date: str,
) -> dict[str, Any]:
    """Merge registry evidence with explicit treatment rules for one snapshot."""

    directory = Path(registry_directory)
    on = date.fromisoformat(entry_date)
    paths = {
        "sources": directory / "sources.csv",
        "measures": directory / "measures.csv",
        "coverage": directory / "coverage.csv",
        "treatment_rules": directory / "treatment_rules.csv",
    }
    sources = {row["source_id"]: row for row in _read(paths["sources"], ("source_id", "citation", "url", "sha256"))}
    measures = {
        row["measure_id"]: row
        for row in _read(
            paths["measures"],
            ("measure_id", "date", "date_type", "headings", "origins", "products", "sources", "status"),
        )
    }
    coverages = _read(paths["coverage"], ("measure_id", "hts", "coverage", "evidence", "status"))
    rules = {row["measure_id"]: row for row in load_treatment_rules(paths["treatment_rules"])}
    unknown_rules = sorted(set(rules) - set(measures))
    if unknown_rules:
        raise TariffSnapshotAdapterError(f"treatment_rules.csv names unknown measures: {unknown_rules}")

    products = [_digits(code) for code in product_codes]
    if not products or any(len(code) not in {6, 8, 10} for code in products):
        raise TariffSnapshotAdapterError("product_codes must contain HTS6, HTS8 or HTS10 values")
    selected_coverage = [row for row in coverages if _coverage_matches(_digits(row["hts"]), products)]
    broad_measure_ids: set[str] = set()
    for measure_id, measure in measures.items():
        try:
            measure_day = date.fromisoformat(measure["date"])
        except ValueError as exc:
            raise TariffSnapshotAdapterError(f"measure {measure_id}: date must be YYYY-MM-DD") from exc
        if (
            measure_day <= on
            and measure["date_type"] in {"effective", "ended"}
            and origins_overlap(measure["origins"], origin)
            and _covers_all_products(measure["products"])
        ):
            broad_measure_ids.add(measure_id)
    measure_ids = {row["measure_id"] for row in selected_coverage} | broad_measure_ids
    policy_measures: list[dict[str, Any]] = []
    unresolved: list[str] = []

    for measure_id in sorted(measure_ids):
        measure = measures.get(measure_id)
        if measure is None:
            raise TariffSnapshotAdapterError(f"coverage.csv names unknown measure {measure_id!r}")
        try:
            measure_day = date.fromisoformat(measure["date"])
        except ValueError as exc:
            raise TariffSnapshotAdapterError(f"measure {measure_id}: date must be YYYY-MM-DD") from exc
        origin_relevant = origins_overlap(measure["origins"], origin)
        active_candidate = origin_relevant and measure_day <= on
        rule = rules.get(measure_id)
        measure_coverage_rows = [
            row for row in selected_coverage
            if row["measure_id"] == measure_id
        ]
        applicable_rows = [
            row for row in measure_coverage_rows
            if row["coverage"].strip().lower() in {"covered", "applies"}
        ]
        if rule is None:
            if active_candidate and measure["date_type"] in {"effective", "ended"}:
                explicitly_excluded = bool(measure_coverage_rows) and all(
                    row["status"].strip().lower() == "verified"
                    and row["coverage"].strip().lower() in {"exempt", "not_covered", "suspended"}
                    for row in measure_coverage_rows
                )
                if applicable_rows:
                    unresolved.append(
                        f"{measure_id}: active applicable registry measure has no machine-readable treatment rule"
                    )
                elif measure_coverage_rows and not explicitly_excluded:
                    unresolved.append(
                        f"{measure_id}: coverage does not establish a verified exclusion and no machine-readable treatment rule exists"
                    )
                elif measure_id in broad_measure_ids and not measure_coverage_rows:
                    unresolved.append(
                        f"{measure_id}: broad active measure has no product coverage or machine-readable treatment rule (fail closed)"
                    )
            continue
        if not active_candidate:
            continue
        rule_sources = split_list(rule["source_ids"])
        measure_sources = set(split_list(measure["sources"]))
        if not set(rule_sources) <= measure_sources:
            raise TariffSnapshotAdapterError(
                f"treatment rule {measure_id}: source_ids must be a subset of measures.csv sources"
            )
        missing_sources = sorted(set(rule_sources) - set(sources))
        if missing_sources:
            raise TariffSnapshotAdapterError(
                f"treatment rule {measure_id}: unknown source ids {missing_sources}"
            )
        if rule["status"] == "verified" and any(
            not re.fullmatch(r"[0-9a-fA-F]{64}", sources[source_id]["sha256"])
            for source_id in rule_sources
        ):
            raise TariffSnapshotAdapterError(
                f"treatment rule {measure_id}: every verified primary source needs a recorded SHA-256"
            )
        if (
            rule["status"] == "verified"
            and measure["status"] == "verified"
            and measure["date_type"] in {"effective", "ended"}
            and rule["effective_from"] != measure["date"]
        ):
            raise TariffSnapshotAdapterError(
                f"treatment rule {measure_id}: verified effective_from differs from measures.csv date"
            )
        citations = "; ".join(sources[source_id]["citation"] for source_id in rule_sources)
        verified = measure["status"] == "verified" and rule["status"] == "verified"
        policy_measures.append(
            {
                "measure_id": measure_id,
                "effective_from": rule["effective_from"],
                "effective_to": rule["effective_to"],
                "action": rule["action"],
                "rate_text": rule["rate_text"],
                "rate_type": rule["rate_type"],
                "stack_group": rule["stack_group"],
                "supersedes": rule["supersedes"],
                "layer": rule["layer"],
                "heading": rule["heading"] or measure["headings"],
                "origins": measure["origins"],
                "status": "verified" if verified else "待核对",
                "source": citations,
                "source_ids": rule["source_ids"],
                "notes": rule["notes"],
            }
        )

    policy_coverage = [
        {
            "measure_id": row["measure_id"],
            "hts": _digits(row["hts"]),
            "coverage": "applies" if row["coverage"].strip().lower() == "covered" else row["coverage"].strip().lower(),
            "status": row["status"],
            "evidence": row["evidence"],
        }
        for row in selected_coverage
        if row["measure_id"] in {item["measure_id"] for item in policy_measures}
    ]
    bindings = [
        {"path": path.name, "sha256": _hash(path)}
        for path in paths.values()
    ]
    return {
        "policy_measures": policy_measures,
        "policy_coverage": policy_coverage,
        "unresolved_registry_reasons": unresolved,
        "input_bindings": bindings,
        "input_set_sha256": canonical_sha256(bindings),
        "coverage_audit": selected_coverage,
    }


def build_registry_tariff_snapshot(
    *,
    structure_inputs: Mapping[str, Any],
    registry_directory: str | Path,
    origin: str,
    entry_date: str,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve a snapshot and bind it to the exact registry/HTS inputs."""

    products = list(structure_inputs.get("products") or [])
    registry_inputs = registry_snapshot_inputs(
        registry_directory,
        product_codes=[row.get("hts", "") for row in products],
        origin=origin,
        entry_date=entry_date,
    )
    entry_context = {**DEFAULT_ENTRY_CONTEXT, **dict(context or {})}
    snapshot = build_tariff_treatment_snapshot(
        products,
        structure_inputs.get("base_rates") or [],
        registry_inputs["policy_measures"],
        registry_inputs["policy_coverage"],
        origin=origin,
        entry_date=entry_date,
        **entry_context,
    )
    unresolved = registry_inputs["unresolved_registry_reasons"]
    if unresolved:
        snapshot["snapshot_status"] = "Unresolved"
        for row in snapshot["rows"]:
            row["status"] = "Unresolved"
            row["unresolved_reasons"] = list(dict.fromkeys([*row["unresolved_reasons"], *unresolved]))
        snapshot["scope_counts"] = {
            "products": len(snapshot["rows"]),
            "resolved": 0,
            "mixed": 0,
            "conditional": 0,
            "unresolved": len(snapshot["rows"]),
        }
    snapshot["input_bindings"] = [
        *registry_inputs["input_bindings"],
        {
            "path": str(structure_inputs.get("classification_release") or "HTS structure"),
            "sha256": str((structure_inputs.get("structure_source") or {}).get("sha256") or ""),
        },
    ]
    snapshot["input_set_sha256"] = canonical_sha256(snapshot["input_bindings"])
    snapshot["registry_coverage_audit"] = registry_inputs["coverage_audit"]
    return snapshot
