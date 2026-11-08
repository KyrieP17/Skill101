"""Offline, hash-backed HTS edition evidence and continuity checks.

The live HTS search is useful for discovery, but it is not historical evidence.
This module queries a local catalog of downloaded official edition sources.
Every catalog row names the edition and effective dates, description, unit,
source URL, local source file, and SHA-256 of that file.  The resulting evidence
record is immutable and is re-verified whenever an approved scope is loaded.

This is intentionally not an approval tool.  A successful history query says
that the proposed scope has continuous, source-backed HTS coverage; it does not
say that the product classification decision is correct or user-approved.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable


HISTORY_SCHEMA_VERSION = 1
CATALOG_COLUMNS = (
    "edition",
    "effective_from",
    "effective_to",
    "hts_level",
    "hts_code",
    "description",
    "quantity_unit",
    "source_url",
    "source_file",
    "source_sha256",
)
HEX_SHA256 = re.compile(r"[0-9a-f]{64}")


class HistoryEvidenceError(ValueError):
    """Historical HTS evidence is absent, malformed, discontinuous, or changed."""


def file_sha256(path: str | Path) -> str:
    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date(value: str, *, field: str, where: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HistoryEvidenceError(f"{where}: {field} must be YYYY-MM-DD, got {value!r}") from exc


def _normal_text(value: Any) -> str:
    return " ".join(str(value or "").split()).casefold()


def _normal_unit(value: Any) -> str:
    return _normal_text(value)


def _portable_path(target: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(target.resolve(), base.resolve())).as_posix()
    except ValueError:
        return target.resolve().as_posix()


def _resolve(recorded: str, base: Path) -> Path:
    path = Path(recorded)
    return (path if path.is_absolute() else base / path).resolve()


def _uncovered(windows: Iterable[tuple[date, date]], start: date, end: date) -> list[tuple[date, date]]:
    merged: list[tuple[date, date]] = []
    for left, right in sorted(windows):
        left, right = max(left, start), min(right, end)
        if left > right:
            continue
        if merged and (left <= merged[-1][1] or (merged[-1][1] < date.max and left == merged[-1][1] + timedelta(days=1))):
            merged[-1] = (merged[-1][0], max(merged[-1][1], right))
        else:
            merged.append((left, right))
    gaps: list[tuple[date, date]] = []
    cursor = start
    for left, right in merged:
        if left > cursor:
            gaps.append((cursor, date.fromordinal(left.toordinal() - 1)))
        if right >= end:
            return gaps
        cursor = max(cursor, right + timedelta(days=1))
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def _read_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = {(name or "").strip() for name in (reader.fieldnames or [])}
            missing = [name for name in CATALOG_COLUMNS if name not in fields]
            if missing:
                raise HistoryEvidenceError(f"HTS history catalog is missing columns: {', '.join(missing)}")
            return [
                {name: str(row.get(name) or "").strip() for name in CATALOG_COLUMNS}
                for row in reader
                if any(str(value or "").strip() for value in row.values())
            ]
    except OSError as exc:
        raise HistoryEvidenceError(f"Cannot read HTS history catalog {path}: {exc}") from exc


def _validate_catalog_row(row: dict[str, str], *, number: int, catalog: Path) -> dict[str, Any]:
    where = f"Catalog row {number}"
    for field in ("edition", "hts_code", "description", "source_url", "source_file", "source_sha256"):
        if not row[field]:
            raise HistoryEvidenceError(f"{where}: {field} is required")
    try:
        level = int(row["hts_level"])
    except ValueError as exc:
        raise HistoryEvidenceError(f"{where}: hts_level must be 6, 8, or 10") from exc
    if level not in {6, 8, 10} or not row["hts_code"].isdigit() or len(row["hts_code"]) != level:
        raise HistoryEvidenceError(f"{where}: hts_code must be exactly hts_level digits")
    start = _date(row["effective_from"], field="effective_from", where=where)
    end = _date(row["effective_to"], field="effective_to", where=where)
    if start > end:
        raise HistoryEvidenceError(f"{where}: effective_from is later than effective_to")
    declared = row["source_sha256"].casefold()
    if not HEX_SHA256.fullmatch(declared):
        raise HistoryEvidenceError(f"{where}: source_sha256 must be 64 lowercase hexadecimal characters")
    source_path = _resolve(row["source_file"], catalog.parent)
    if not source_path.is_file():
        raise HistoryEvidenceError(f"{where}: downloaded source file is missing: {source_path}")
    actual = file_sha256(source_path)
    if actual != declared:
        raise HistoryEvidenceError(
            f"{where}: source_sha256 does not match {source_path} (expected {declared}, got {actual})"
        )
    return {
        **row,
        "hts_level": level,
        "effective_from": start,
        "effective_to": end,
        "source_path": source_path,
        "source_sha256": actual,
    }


def _digest(evidence: dict[str, Any]) -> str:
    from .scope import canonical_sha256

    return canonical_sha256(
        {key: value for key, value in evidence.items() if key not in {"created_at_utc", "evidence_sha256"}}
    )


def build_history_evidence(
    scope_path: str | Path,
    catalog_path: str | Path,
    *,
    output_path: str | Path | None = None,
    evidence_base: str | Path | None = None,
) -> dict[str, Any]:
    """Match every included scope interval to source-backed HTS edition rows."""

    from . import scope as scope_module

    scope_file = Path(scope_path).resolve()
    catalog_file = Path(catalog_path).resolve()
    scope_data = scope_file.read_bytes()
    raw_scope = scope_module._parse_scope(scope_data)
    summary = scope_module.validate_scope(raw_scope, require_approved=False)
    if output_path is not None:
        base = Path(output_path).resolve().parent
    elif evidence_base is not None:
        base = Path(evidence_base).resolve()
    else:
        base = catalog_file.parent
    catalog_rows = [
        _validate_catalog_row(row, number=number, catalog=catalog_file)
        for number, row in enumerate(_read_csv(catalog_file), start=2)
    ]

    errors: list[str] = []
    matches: list[dict[str, Any]] = []
    included_rows = [row for row in raw_scope if row["decision"] == "include"]
    for row_number, line in enumerate(included_rows, start=1):
        start = _date(line["valid_from"], field="valid_from", where=f"Scope include {row_number}")
        end = _date(line["valid_to"], field="valid_to", where=f"Scope include {row_number}")
        candidates = [
            item
            for item in catalog_rows
            if item["hts_code"] == line["hts_code"]
            and item["hts_level"] == int(line["hts_level"])
            and item["edition"] == line["hts_release"]
            and item["effective_from"] <= end
            and item["effective_to"] >= start
        ]
        if not candidates:
            errors.append(
                f"HTS{line['hts_level']} {line['hts_code']} {line['valid_from']}..{line['valid_to']} "
                f"has no matching catalog evidence for edition {line['hts_release']!r}"
            )
            continue
        gaps = _uncovered(((item["effective_from"], item["effective_to"]) for item in candidates), start, end)
        if gaps:
            listed = ", ".join(f"{left.isoformat()}..{right.isoformat()}" for left, right in gaps)
            errors.append(f"HTS {line['hts_code']} history evidence has uncovered dates: {listed}")
        for item in candidates:
            if _normal_text(item["description"]) != _normal_text(line["description"]):
                errors.append(
                    f"HTS {line['hts_code']} description differs between scope and edition {item['edition']}"
                )
            if _normal_unit(item["quantity_unit"]) != _normal_unit(line["quantity_unit"]):
                errors.append(f"HTS {line['hts_code']} quantity unit differs in edition {item['edition']}")
            record = {
                "edition": item["edition"],
                "effective_from": item["effective_from"].isoformat(),
                "effective_to": item["effective_to"].isoformat(),
                "hts_level": item["hts_level"],
                "hts_code": item["hts_code"],
                "description": item["description"],
                "quantity_unit": item["quantity_unit"],
                "source_url": item["source_url"],
                "source_file": _portable_path(item["source_path"], base),
                "source_sha256": item["source_sha256"],
            }
            from .scope import canonical_sha256

            record["record_sha256"] = canonical_sha256(record)
            matches.append(record)

    for left, right in summary["coverage"]["gaps"]:
        errors.append(f"Scope itself has no included HTS line for {left}..{right}")

    from .scope import utc_now

    evidence: dict[str, Any] = {
        "schema_version": HISTORY_SCHEMA_VERSION,
        "artifact_type": "hts_history_evidence",
        "created_at_utc": utc_now(),
        "scope_path": _portable_path(scope_file, base),
        "scope_file_sha256": sha256(scope_data).hexdigest(),
        "scope_sha256": summary["scope_sha256"],
        "catalog_path": _portable_path(catalog_file, base),
        "catalog_file_sha256": file_sha256(catalog_file),
        "matches": sorted(
            matches,
            key=lambda item: (item["hts_code"], item["effective_from"], item["edition"]),
        ),
        "continuity": {"ok": not errors, "errors": errors},
        "approval_effect": "none; historical evidence never approves product scope",
    }
    evidence["evidence_sha256"] = _digest(evidence)
    if output_path is not None:
        target = Path(output_path).resolve()
        if target.exists():
            raise FileExistsError(f"Refusing to overwrite HTS history evidence: {target}")
        scope_module.write_json_atomic(target, evidence)
    return evidence


def verify_history_evidence(path: str | Path, *, expected_scope_sha256: str) -> dict[str, Any]:
    """Re-verify an immutable history record and every local source hash."""

    evidence_path = Path(path).resolve()
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HistoryEvidenceError(f"Cannot read HTS history evidence {evidence_path}: {exc}") from exc
    if evidence.get("schema_version") != HISTORY_SCHEMA_VERSION or evidence.get("artifact_type") != "hts_history_evidence":
        raise HistoryEvidenceError(f"Unsupported HTS history evidence schema: {evidence_path}")
    if evidence.get("scope_sha256") != expected_scope_sha256:
        raise HistoryEvidenceError("HTS history evidence belongs to a different scope_sha256")
    if _digest(evidence) != evidence.get("evidence_sha256"):
        raise HistoryEvidenceError(f"HTS history evidence was edited after creation: {evidence_path}")
    continuity = evidence.get("continuity") or {}
    if not continuity.get("ok") or continuity.get("errors"):
        raise HistoryEvidenceError(
            "HTS historical continuity failed: " + "; ".join(map(str, continuity.get("errors") or ["unknown error"]))
        )
    catalog = _resolve(str(evidence.get("catalog_path") or ""), evidence_path.parent)
    if not catalog.is_file() or file_sha256(catalog) != evidence.get("catalog_file_sha256"):
        raise HistoryEvidenceError("HTS history catalog is missing or changed after evidence creation")
    scope = _resolve(str(evidence.get("scope_path") or ""), evidence_path.parent)
    if not scope.is_file() or file_sha256(scope) != evidence.get("scope_file_sha256"):
        raise HistoryEvidenceError("Scope used to query HTS history is missing or changed")
    try:
        rebuilt = build_history_evidence(scope, catalog, evidence_base=evidence_path.parent)
    except (OSError, HistoryEvidenceError) as exc:
        raise HistoryEvidenceError(f"Cannot reproduce HTS history evidence: {exc}") from exc
    if rebuilt.get("evidence_sha256") != evidence.get("evidence_sha256"):
        raise HistoryEvidenceError(
            "HTS history evidence cannot be reproduced from its scope, catalog, and source hashes"
        )
    return evidence


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser.add_argument("--scope", type=Path, required=True, help="Candidate or reviewed scope CSV")
    parser.add_argument("--catalog", type=Path, required=True, help="Local hash-backed HTS edition catalog CSV")
    parser.add_argument("--output", type=Path, required=True, help="Immutable history evidence JSON to create")
    return parser


def run(scope: Path, catalog: Path, output: Path) -> int:
    try:
        evidence = build_history_evidence(scope, catalog, output_path=output)
    except (OSError, HistoryEvidenceError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    return 0 if evidence["continuity"]["ok"] else 2


def main(argv: list[str] | None = None) -> int:
    parser = add_arguments(argparse.ArgumentParser(description="Build source-backed historical HTS continuity evidence"))
    args = parser.parse_args(argv)
    return run(args.scope, args.catalog, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
