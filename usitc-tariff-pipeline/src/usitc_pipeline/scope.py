"""HTS research scope: validate, approve in the reviewer's terminal, freeze, and verify.

Why this module exists
----------------------
A product name is not a stable HTS definition, so every new product is
researched through a reviewed scope record: which HTS lines are included or
excluded, at which level, for which dates, with a rationale and an approval.

The rule only works if the code that calls DataWeb enforces it.  Text in a
Skill binds an agent that has read it, not a colleague running
``python run.py acquire``.  This module is therefore the single source of
truth for scope rules; the Skill's ``validate_scope.py`` is a thin wrapper
around :func:`main`.

The module is pure Python (no pandas, no network), so validation and the
acquisition gate run in any Python 3.11+ interpreter.

Two hashes
----------
``scope_sha256`` covers only what changes the measurement: scope identity,
HTS release, level, code, decision, validity window and quantity unit.  It is
the identity carried by query plans and raw snapshots.  ``record_sha256``
also covers rationale, reviewer, timestamps, descriptions and URLs, so an
audit still detects any edit.  A typo fix in a rationale therefore does not
break downstream lineage, but it remains visible.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
import io
import json
from operator import attrgetter
import os
from pathlib import Path
import re
import sys
from typing import Any, Callable, Iterable


SCHEMA_VERSION = 4
APPROVAL_CARD_SCHEMA_VERSION = 2
APPROVAL_METHOD = "interactive_terminal_confirmation"
APPROVAL_BOUNDARY = "typed_confirmation_not_cryptographically_authenticated"
CHAT_APPROVAL_METHOD = "chat_confirmation"
CHAT_APPROVAL_BOUNDARY = "user_confirmed_in_chat_agent_ran_scope_approve"
APPROVAL_RECORDS = {APPROVAL_METHOD: APPROVAL_BOUNDARY, CHAT_APPROVAL_METHOD: CHAT_APPROVAL_BOUNDARY}
CARD_EFFECT = "none; the reviewer approves by running scope-approve in their own terminal"
CONTINUITY_WARNING = "连续性未核实: HTS codes were not checked against archived HTS editions for every year"

REQUIRED_COLUMNS = [
    "scope_id",
    "scope_version",
    "product_id",
    "product_name",
    "hts_release",
    "hts_level",
    "hts_code",
    "description",
    "quantity_unit",
    "decision",
    "rationale",
    "valid_from",
    "valid_to",
    "review_status",
    "reviewer",
    "approved_at_utc",
    "source_url",
]
# Optional: the schedule's second unit of quantity (e.g. kg next to No.).  It is
# recorded so a later study can use it; nothing fetches it yet.  Blank or absent
# means the HTS gives none, and then it changes no hash.
OPTIONAL_COLUMNS = ["second_unit"]
IDENTITY_FIELDS = ("scope_id", "scope_version", "product_id", "product_name")
SEMANTIC_FIELDS = (
    "scope_id",
    "scope_version",
    "product_id",
    "hts_release",
    "hts_level",
    "hts_code",
    "decision",
    "valid_from",
    "valid_to",
    "quantity_unit",
)
VALID_LEVELS = frozenset({6, 8, 10})
VALID_DECISIONS = frozenset({"include", "exclude"})
VALID_STATUSES = frozenset({"candidate", "reviewed", "approved"})
SPECIAL_CHAPTERS = frozenset({"98", "99"})
SLUG_PATTERN = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


class ScopeError(ValueError):
    """A scope record or manifest failed validation."""


class ScopeGateError(RuntimeError):
    """An acquisition request does not match an approved, frozen scope."""


@dataclass(frozen=True)
class ScopeLine:
    """One validated scope row."""

    row_number: int
    hts_code: str
    hts_level: int
    decision: str
    valid_from: date
    valid_to: date
    quantity_unit: str
    hts_release: str
    review_status: str
    second_unit: str = ""

    def overlaps(self, start: date, end: date) -> bool:
        return self.valid_from <= end and self.valid_to >= start

    def as_json(self) -> dict[str, Any]:
        return {
            "hts_code": self.hts_code,
            "hts_level": self.hts_level,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat(),
            "quantity_unit": self.quantity_unit,
            **({"second_unit": self.second_unit} if self.second_unit else {}),
            "hts_release": self.hts_release,
        }


@dataclass(frozen=True)
class ApprovedScope:
    """A scope whose CSV and hashes were re-verified when loaded.

    :func:`read_unapproved_scope` returns the same shape for a labeled legacy
    replay; only :func:`verify_scope_manifest` returns an approved one.
    """

    scope_id: str
    scope_version: str
    product_id: str
    product_name: str
    hts_level: int
    included: tuple[ScopeLine, ...]
    quantity_unit: str | None
    scope_sha256: str
    manifest_path: Path
    continuity_verified: bool
    second_unit: str | None = None
    second_units: tuple[str, ...] = ()
    approval_method: str | None = None

    @property
    def quantity_comparable(self) -> bool:
        """Quantities add up only when every included line reports one known unit."""

        return self.quantity_unit is not None

    def codes_for_year(self, year: int) -> tuple[str, ...]:
        """Return included codes valid on at least one day of ``year``.

        A code created in July is queried for that whole year together with
        the predecessor that ended in June; summing both gives the full year.
        """

        start, end = date(year, 1, 1), date(year, 12, 31)
        return tuple(sorted({line.hts_code for line in self.included if line.overlaps(start, end)}))

    def uncovered(self, start: date, end: date) -> list[tuple[date, date]]:
        """Return sub-periods of ``start..end`` covered by no included line."""

        return _uncovered(_merged_windows(self.included), start, end)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> bytes:
    """Serialize deterministically for hashing."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    """The SHA-256 behind every content identity: scope, record, plan, study, and payload."""

    return sha256(canonical_json(value)).hexdigest()


def write_json_atomic(path: Path, value: Any, **dumps_options: Any) -> Path:
    """Write indented JSON through a temporary sibling, so no reader sees a partial file."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, **dumps_options), encoding="utf-8")
    temporary.replace(path)
    return path


def file_sha256(path: str | Path) -> str:
    """Hash an artifact exactly as stored, rather than re-serializing it."""

    digest = sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_scope(data: bytes) -> list[dict[str, str]]:
    """Parse scope CSV bytes into stripped strings; blank rows are ignored.

    Callers hash the same bytes they parse, so the rows validated are exactly
    the file content recorded in the manifest.
    """

    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline=""))
    fieldnames = {(name or "").strip() for name in (reader.fieldnames or [])}
    missing = [column for column in REQUIRED_COLUMNS if column not in fieldnames]
    if missing:
        raise ScopeError(f"Scope CSV is missing columns: {', '.join(missing)}")
    rows: list[dict[str, str]] = []
    for raw in reader:
        stripped = {(key or "").strip(): (value or "").strip() for key, value in raw.items() if key is not None}
        row = {column: stripped.get(column, "") for column in (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS)}
        if any(row.values()):
            rows.append(row)
    return rows


def _one_value(rows: list[dict[str, str]], field: str) -> str:
    values = {row[field] for row in rows if row[field]}
    if len(values) != 1:
        raise ScopeError(f"{field} must have exactly one non-empty value across rows; found {sorted(values)}")
    return next(iter(values))


def _parse_date(value: str, field: str, where: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ScopeError(f"{where}: {field} must be YYYY-MM-DD, got {value!r}") from exc


def _unit(value: str) -> str:
    return " ".join(value.split()).lower()


def _parse_line(row: dict[str, str], row_number: int) -> ScopeLine:
    where = f"Row {row_number}"
    try:
        level = int(row["hts_level"])
    except ValueError as exc:
        raise ScopeError(f"{where}: hts_level must be 6, 8, or 10") from exc
    if level not in VALID_LEVELS:
        raise ScopeError(
            f"{where}: hts_level {level} is not 6, 8, or 10 (HTS2/4 describe structure, not a product scope)"
        )
    code = row["hts_code"]
    if not re.fullmatch(r"\d+", code) or len(code) != level:
        raise ScopeError(f"{where}: hts_code must be exactly {level} digits without dots, got {code!r}")
    if row["decision"] not in VALID_DECISIONS:
        raise ScopeError(f"{where}: decision must be include or exclude, got {row['decision']!r}")
    if row["review_status"] not in VALID_STATUSES:
        raise ScopeError(f"{where}: review_status must be candidate, reviewed, or approved")
    start = _parse_date(row["valid_from"], "valid_from", where)
    end = _parse_date(row["valid_to"], "valid_to", where)
    if start > end:
        raise ScopeError(f"{where}: valid_from is later than valid_to")
    for field in ("hts_release", "description", "rationale", "source_url"):
        if not row[field]:
            raise ScopeError(f"{where}: {field} is required")
    second_unit = _unit(row.get("second_unit", ""))
    if second_unit and second_unit == _unit(row["quantity_unit"]):
        raise ScopeError(f"{where}: second_unit repeats quantity_unit; leave it blank when the HTS gives one unit")
    if second_unit and not row["quantity_unit"]:
        raise ScopeError(f"{where}: second_unit needs a quantity_unit (the schedule's first unit)")
    if row["review_status"] == "approved":
        if not row["reviewer"] or not row["approved_at_utc"]:
            raise ScopeError(f"{where}: approved rows require reviewer and approved_at_utc")
        try:
            datetime.fromisoformat(row["approved_at_utc"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ScopeError(f"{where}: approved_at_utc must be an ISO timestamp") from exc
    return ScopeLine(
        row_number=row_number,
        hts_code=code,
        hts_level=level,
        decision=row["decision"],
        valid_from=start,
        valid_to=end,
        quantity_unit=_unit(row["quantity_unit"]),
        hts_release=row["hts_release"],
        review_status=row["review_status"],
        second_unit=second_unit,
    )


def _check_overlaps(lines: Iterable[ScopeLine]) -> None:
    """One code may carry one decision per day; windows may not overlap."""

    by_code: dict[str, list[ScopeLine]] = {}
    for line in lines:
        by_code.setdefault(line.hts_code, []).append(line)
    for code, group in by_code.items():
        ordered = sorted(group, key=lambda line: (line.valid_from, line.valid_to))
        reach = ordered[0]
        for line in ordered[1:]:
            if line.valid_from <= reach.valid_to:
                kind = "conflicting decisions" if line.decision != reach.decision else "overlapping validity windows"
                raise ScopeError(
                    f"Rows {reach.row_number} and {line.row_number}: {kind} for HTS {code} "
                    f"({reach.decision} {reach.valid_from}..{reach.valid_to} vs "
                    f"{line.decision} {line.valid_from}..{line.valid_to}). "
                    "Give each period one decision, or remove a row."
                )
            if line.valid_to > reach.valid_to:
                reach = line


def _check_nested_exclusions(included: Iterable[ScopeLine], excluded: Iterable[ScopeLine]) -> None:
    """Reject exclusions that a query at the included level cannot subtract."""

    included = list(included)
    for out in excluded:
        for inside in included:
            if (
                inside.hts_level < out.hts_level
                and out.hts_code.startswith(inside.hts_code)
                and out.overlaps(inside.valid_from, inside.valid_to)
            ):
                raise ScopeError(
                    f"Row {out.row_number}: excluded HTS{out.hts_level} {out.hts_code} lies inside included "
                    f"HTS{inside.hts_level} {inside.hts_code} (row {inside.row_number}). A DataWeb query at "
                    f"HTS{inside.hts_level} cannot subtract it; include the remaining HTS{out.hts_level} lines instead."
                )


def _merged_windows(lines: Iterable[ScopeLine]) -> list[tuple[date, date]]:
    merged: list[tuple[date, date]] = []
    for start, end in sorted((line.valid_from, line.valid_to) for line in lines):
        if merged and start <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _uncovered(windows: list[tuple[date, date]], start: date, end: date) -> list[tuple[date, date]]:
    """Return sub-periods of ``start..end`` outside the merged ``windows``."""

    gaps: list[tuple[date, date]] = []
    cursor = start
    for window_start, window_end in windows:
        if window_end < cursor:
            continue
        if window_start > end:
            break
        if window_start > cursor:
            gaps.append((cursor, min(end, window_start - timedelta(days=1))))
        cursor = max(cursor, window_end + timedelta(days=1))
        if cursor > end:
            break
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def _sort_key(record: dict[str, str]) -> tuple[str, str, str, str]:
    return (record["decision"], record["hts_code"], record["valid_from"], record["valid_to"])


def _semantic_record(row: dict[str, str], line: ScopeLine) -> dict[str, str]:
    """The fields that change the measurement, normalized as the parsed line holds them."""

    return {
        **{field: row[field] for field in SEMANTIC_FIELDS},
        "hts_level": str(line.hts_level),
        "quantity_unit": line.quantity_unit,
        "valid_from": line.valid_from.isoformat(),
        "valid_to": line.valid_to.isoformat(),
        **({"second_unit": line.second_unit} if line.second_unit else {}),
    }


def _record(row: dict[str, str]) -> dict[str, str]:
    """Every column of a row; the optional ones only when filled, so older hashes stay valid."""

    return {
        **{column: row[column] for column in REQUIRED_COLUMNS},
        **{column: row[column] for column in OPTIONAL_COLUMNS if row.get(column)},
    }


def _analyse(rows: list[dict[str, str]], *, require_approved: bool) -> tuple[dict[str, Any], list[ScopeLine]]:
    if not rows:
        raise ScopeError("Scope CSV contains no records")
    identity = {field: _one_value(rows, field) for field in IDENTITY_FIELDS}
    for field in ("scope_id", "product_id"):
        if not SLUG_PATTERN.fullmatch(identity[field]):
            raise ScopeError(
                f"{field} must be a lowercase slug (letters, digits, '-' or '_'); got {identity[field]!r}"
            )
    if not re.fullmatch(r"[1-9]\d*", identity["scope_version"]):
        raise ScopeError("scope_version must be a positive integer such as 1 or 2")

    lines = [_parse_line(row, number) for number, row in enumerate(rows, start=2)]
    included = [line for line in lines if line.decision == "include"]
    excluded = [line for line in lines if line.decision == "exclude"]
    if not included:
        raise ScopeError("Scope must include at least one code")
    levels = {line.hts_level for line in included}
    if len(levels) != 1:
        raise ScopeError(f"Included query rows must use one HTS level; found {sorted(levels)}")
    special = [line for line in included if line.hts_code[:2] in SPECIAL_CHAPTERS]
    if special:
        listed = ", ".join(f"row {line.row_number} ({line.hts_code})" for line in special)
        raise ScopeError(
            f"Chapter 98/99 provisions cannot be included product lines: {listed}. "
            "They are special or temporary provisions; record them as exclusions or as policy treatment."
        )
    _check_overlaps(lines)
    _check_nested_exclusions(included, excluded)
    if require_approved and any(line.review_status != "approved" for line in lines):
        raise ScopeError("--require-approved requires every retained include/exclude row to be approved")
    # The checks above report rows in file order; everything below is in code order.
    included.sort(key=attrgetter("hts_code", "valid_from"))
    excluded.sort(key=attrgetter("hts_code", "valid_from"))

    units = {line.quantity_unit for line in included}
    quantity_comparable = len(units) == 1 and "" not in units
    second = {line.second_unit for line in included}
    windows = _merged_windows(included)
    coverage_start, coverage_end = windows[0][0], windows[-1][1]
    gaps = _uncovered(windows, coverage_start, coverage_end)
    summary = {
        **identity,
        "hts_level": next(iter(levels)),
        "hts_releases": sorted({line.hts_release for line in lines}),
        "included": [line.as_json() for line in included],
        "excluded": [line.as_json() for line in excluded],
        "included_codes": sorted({line.hts_code for line in included}),
        "excluded_codes": sorted({line.hts_code for line in excluded}),
        "coverage": {
            "start": coverage_start.isoformat(),
            "end": coverage_end.isoformat(),
            "gaps": [[gap_start.isoformat(), gap_end.isoformat()] for gap_start, gap_end in gaps],
        },
        "quantity_unit": next(iter(units)) if quantity_comparable else None,
        "quantity_comparable": quantity_comparable,
        # One second unit on every included line can be summed later; a mix cannot.
        "second_unit": next(iter(second)) if len(second) == 1 and "" not in second else None,
        "second_units": sorted(second - {""}),
        "record_count": len(lines),
        "all_records_approved": all(line.review_status == "approved" for line in lines),
        "scope_sha256": canonical_sha256(
            sorted((_semantic_record(row, line) for row, line in zip(rows, lines)), key=_sort_key)
        ),
        "record_sha256": canonical_sha256(
            sorted((_record(row) for row in rows), key=_sort_key)
        ),
    }
    return summary, included


def validate_scope(rows: list[dict[str, str]], *, require_approved: bool) -> dict[str, Any]:
    """Validate scope rows and return a manifest-ready summary with hashes."""

    return _analyse(rows, require_approved=require_approved)[0]


def _portable_path(target: Path, base: Path | None) -> str:
    """Prefer a path relative to the manifest so a teammate's copy still works."""

    if base is None:
        return target.as_posix()
    try:
        return Path(os.path.relpath(target, base)).as_posix()
    except ValueError:  # different drives on Windows
        return target.as_posix()


def build_manifest(
    scope_path: str | Path,
    *,
    require_approved: bool,
    manifest_path: str | Path | None = None,
) -> dict[str, Any]:
    """Validate a scope CSV and return the manifest record (nothing is written)."""

    source = Path(scope_path).resolve()
    data = source.read_bytes()
    summary = validate_scope(_parse_scope(data), require_approved=require_approved)
    base = Path(manifest_path).resolve().parent if manifest_path is not None else None
    return {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "source_scope": _portable_path(source, base),
        "source_file_sha256": sha256(data).hexdigest(),
        "validation": {"ok": True, "require_approved": require_approved},
        **summary,
    }


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScopeError(f"Cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ScopeError(f"{label} must be one JSON object: {path}")
    return value


def _continuity_status(history_evidence: str | Path | None, scope_sha256: str, base: Path) -> dict[str, Any]:
    """The HTS-continuity status shown on the approval card and kept in the manifest."""

    if history_evidence is None:
        return {"status": "unverified", "warning": CONTINUITY_WARNING}
    from .hts_history import HistoryEvidenceError, verify_history_evidence

    path = Path(history_evidence).resolve()
    try:
        evidence = verify_history_evidence(path, expected_scope_sha256=scope_sha256)
    except HistoryEvidenceError as exc:
        raise ScopeError(f"HTS historical continuity is not verified: {exc}") from exc
    return {
        "status": "verified",
        "evidence_path": _portable_path(path, base),
        "evidence_file_sha256": file_sha256(path),
        "evidence_sha256": evidence["evidence_sha256"],
    }


def build_approval_card(
    scope_path: str | Path,
    *,
    output_path: str | Path | None = None,
    history_evidence: str | Path | None = None,
) -> dict[str, Any]:
    """Build the immutable card the reviewer reads before approving.

    The card has no approval effect.  It binds the full reviewed CSV bytes,
    the measurement identity and the HTS-continuity status, so the reviewer
    approves exactly what the card showed.
    """

    source = Path(scope_path).resolve()
    data = source.read_bytes()
    rows = _parse_scope(data)
    statuses = {row["review_status"] for row in rows}
    if statuses != {"reviewed"}:
        raise ScopeError(
            "An approval card requires every retained row to have review_status=reviewed; "
            f"found {sorted(statuses)}"
        )
    summary = validate_scope(rows, require_approved=False)
    base = Path(output_path).resolve().parent if output_path is not None else source.parent
    card: dict[str, Any] = {
        "schema_version": APPROVAL_CARD_SCHEMA_VERSION,
        "artifact_type": "scope_approval_card",
        "created_at_utc": utc_now(),
        "scope_path": _portable_path(source, base),
        "scope_file_sha256": sha256(data).hexdigest(),
        "scope_sha256": summary["scope_sha256"],
        "record_sha256": summary["record_sha256"],
        "scope_id": summary["scope_id"],
        "scope_version": summary["scope_version"],
        "product_id": summary["product_id"],
        "product_name": summary["product_name"],
        "hts_level": summary["hts_level"],
        "hts_releases": summary["hts_releases"],
        "included_codes": summary["included_codes"],
        "excluded_codes": summary["excluded_codes"],
        "coverage": summary["coverage"],
        "quantity_unit": summary["quantity_unit"],
        "quantity_comparable": summary["quantity_comparable"],
        "second_unit": summary["second_unit"],
        "second_units": summary["second_units"],
        "continuity": _continuity_status(history_evidence, summary["scope_sha256"], base),
        "approval_effect": CARD_EFFECT,
    }
    if output_path is not None:
        target = Path(output_path).resolve()
        if target.exists():
            raise FileExistsError(f"Refusing to overwrite approval card: {target}")
        write_json_atomic(target, card)
    return card


def second_unit_text(second_unit: str | None, second_units: Iterable[str]) -> str:
    """How the card and status describe the schedule's second unit of quantity."""

    if second_unit:
        return f"{second_unit} on every included line (fetched only by a study that sets [quantity] unit = \"second\")"
    listed = list(second_units)
    if listed:
        return f"{', '.join(listed)} on some included lines only (cannot be summed across the scope)"
    return "none"


def format_approval_card(card: dict[str, Any]) -> str:
    """The plain-text card shown to the reviewer; an agent may show the same text."""

    coverage = card["coverage"]
    continuity = card["continuity"]
    gaps = "; ".join(f"{start}..{end}" for start, end in coverage["gaps"]) or "none"
    quantity = card["quantity_unit"] if card["quantity_comparable"] else "not comparable (value-only results)"
    second = second_unit_text(card.get("second_unit"), card.get("second_units") or [])
    if continuity["status"] == "verified":
        continuity_text = f"verified (evidence {continuity['evidence_sha256'][:12]})"
    else:
        continuity_text = f"NOT VERIFIED - {continuity['warning']}"
    return "\n".join(
        [
            "Scope approval card (reading it approves nothing)",
            f"  product       {card['product_name']} ({card['product_id']})",
            f"  scope         {card['scope_id']} v{card['scope_version']}, HTS{card['hts_level']}, "
            f"releases {', '.join(card['hts_releases'])}",
            f"  include       {', '.join(card['included_codes'])}",
            f"  exclude       {', '.join(card['excluded_codes']) or 'none'}",
            f"  coverage      {coverage['start']}..{coverage['end']}, gaps: {gaps}",
            f"  quantity      {quantity}",
            f"  second unit   {second}",
            f"  continuity    {continuity_text}",
            f"  scope_sha256  {card['scope_sha256']}",
        ]
    )


def _verify_approval_card(
    card_path: Path,
    *,
    expected_scope_sha256: str,
    expected_card_file_sha256: str | None = None,
) -> dict[str, Any]:
    if expected_card_file_sha256 is not None and file_sha256(card_path) != expected_card_file_sha256:
        raise ScopeError("Approval card bytes do not match approval_card_sha256")
    card = _read_json(card_path, label="approval card")
    if card.get("schema_version") != APPROVAL_CARD_SCHEMA_VERSION or card.get("artifact_type") != "scope_approval_card":
        raise ScopeError(f"Unsupported approval card schema: {card_path}; rebuild it with scope-validate --approval-card")
    if card.get("scope_sha256") != expected_scope_sha256:
        raise ScopeError("Approval card belongs to a different scope_sha256")
    if card.get("approval_effect") != CARD_EFFECT:
        raise ScopeError("Approval card does not preserve the no-approval boundary")
    source = Path(str(card.get("scope_path") or ""))
    source = (source if source.is_absolute() else card_path.parent / source).resolve()
    if not source.is_file() or file_sha256(source) != card.get("scope_file_sha256"):
        raise ScopeError("Reviewed scope referenced by the approval card is missing or changed")
    rows = _parse_scope(source.read_bytes())
    if {row["review_status"] for row in rows} != {"reviewed"}:
        raise ScopeError("Approval card source is no longer a fully reviewed scope")
    summary = validate_scope(rows, require_approved=False)
    if summary["scope_sha256"] != card.get("scope_sha256") or summary["record_sha256"] != card.get("record_sha256"):
        raise ScopeError("Approval card hashes no longer match its reviewed scope")
    return card


def _write_approved_scope(
    rows: list[dict[str, str]],
    path: Path,
    *,
    reviewer: str,
    approved_at_utc: str,
) -> None:
    if path.exists():
        raise FileExistsError(f"Refusing to overwrite approved scope: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.StringIO(newline="")
    # Optional columns are kept only when the reviewed scope filled them.
    optional = [column for column in OPTIONAL_COLUMNS if any(row.get(column) for row in rows)]
    writer = csv.DictWriter(buffer, fieldnames=[*REQUIRED_COLUMNS, *optional], lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    for original in rows:
        writer.writerow(
            {
                **original,
                "review_status": "approved",
                "reviewer": reviewer,
                "approved_at_utc": approved_at_utc,
            }
        )
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(buffer.getvalue(), encoding="utf-8")
    temporary.replace(path)


def _interactive_terminal() -> bool:
    """True only when stdin is a real terminal; a pipe, a file or the NUL device is refused.

    On Windows ``isatty()`` is also true for NUL, so the console handle is
    checked too.  Git Bash (mintty) is not a console: use PowerShell, cmd, or
    ``winpty python run.py scope-approve ...``.
    """

    if sys.stdin is None or not sys.stdin.isatty():
        return False
    if os.name != "nt":
        return True
    import ctypes
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(sys.stdin.fileno())
    except (OSError, ValueError):
        return False
    mode = ctypes.c_uint32()
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


def _approval_record_ok(approval: dict[str, Any]) -> bool:
    """A known approval method with its own boundary; a chat approval also carries the user's words."""

    method = approval.get("method")
    if method not in APPROVAL_RECORDS or approval.get("procedural_boundary") != APPROVAL_RECORDS[method]:
        return False
    return method != CHAT_APPROVAL_METHOD or bool(str(approval.get("user_confirmation") or "").strip())


def _typed(prompt: Callable[[str], str], text: str) -> str:
    try:
        return prompt(text).strip()
    except EOFError as exc:
        raise ScopeError("No confirmation was typed; nothing was written") from exc


def approve_scope(
    reviewed_scope: str | Path,
    *,
    approval_card: str | Path,
    approved_scope: str | Path,
    manifest_path: str | Path,
    reviewer: str,
    history_evidence: str | Path | None = None,
    continuity_unverified: bool = False,
    prompt: Callable[[str], str] = input,
    out: Callable[[str], Any] = print,
    interactive: bool | None = None,
    chat_confirmation: str | None = None,
    confirm_prefix: str | None = None,
) -> dict[str, Any]:
    """Freeze a reviewed scope after the user confirms it, in a terminal or in chat.

    Terminal (default): the command shows the approval card and asks the
    reviewer to type the first 8 characters of ``scope_sha256`` (and
    ``UNVERIFIED`` when HTS continuity has no archive evidence).  It refuses a
    non-interactive stdin, which is how an agent's shell runs.

    Chat: the agent showed the card in the conversation and the user approved
    it there. The agent passes the user's exact words (``chat_confirmation``)
    and the 8-character prefix the card showed (``confirm_prefix``);
    ``continuity_unverified`` stands for the user's acknowledgement of the
    unverified-continuity line on that card. The manifest records the method
    and the words, and reports say the scope was approved in chat.

    Either way this is a procedural boundary, not a signature: it cannot prove
    who typed. In chat mode it rests on the agent following the rule.
    """

    reviewer = " ".join(str(reviewer or "").split())
    if not reviewer:
        raise ScopeError("scope-approve requires --reviewer with the reviewer's name")
    chat = None if chat_confirmation is None else " ".join(str(chat_confirmation).split())
    if chat is not None and not chat:
        raise ScopeError("--chat-confirmation needs the user's exact words of approval")
    if (confirm_prefix is not None) != (chat is not None):
        raise ScopeError("--chat-confirmation and --confirm go together (the user's words and the prefix the card showed)")
    reviewed_path = Path(reviewed_scope).resolve()
    card_path = Path(approval_card).resolve()
    approved_path = Path(approved_scope).resolve()
    manifest = Path(manifest_path).resolve()
    if approved_path == reviewed_path:
        raise ScopeError("Approved scope must be a new file; the reviewed input remains immutable")
    if approved_path.exists() or manifest.exists():
        existing = approved_path if approved_path.exists() else manifest
        raise FileExistsError(f"Refusing to overwrite approval output: {existing}")
    if (history_evidence is None) == (not continuity_unverified):
        raise ScopeError(
            "HTS continuity: pass --history-evidence <hts-history.json>, or --continuity-unverified to approve "
            "with continuity explicitly marked as not verified (not both)"
        )

    reviewed_data = reviewed_path.read_bytes()
    reviewed_rows = _parse_scope(reviewed_data)
    statuses = {row["review_status"] for row in reviewed_rows}
    if statuses != {"reviewed"}:
        raise ScopeError(
            "scope-approve requires every retained source row to have review_status=reviewed; "
            f"found {sorted(statuses)}"
        )
    summary = validate_scope(reviewed_rows, require_approved=False)
    card_file_hash = file_sha256(card_path)
    card = _verify_approval_card(
        card_path,
        expected_scope_sha256=summary["scope_sha256"],
        expected_card_file_sha256=card_file_hash,
    )
    if card.get("scope_file_sha256") != sha256(reviewed_data).hexdigest():
        raise ScopeError("Approval card was created for different reviewed-scope bytes")
    continuity = _continuity_status(history_evidence, summary["scope_sha256"], manifest.parent)
    shown = card.get("continuity") or {}
    if continuity["status"] != shown.get("status") or continuity.get("evidence_sha256") != shown.get("evidence_sha256"):
        raise ScopeError(
            "The approval card shows a different HTS-continuity status; rebuild it with "
            "scope-validate --approval-card [--history-evidence] so the reviewer sees what is approved"
        )

    if chat is not None:
        if str(confirm_prefix).strip().lower() != summary["scope_sha256"][:8]:
            raise ScopeError(
                "--confirm does not match the first 8 characters of scope_sha256 on this card; "
                "show the user the current card and ask again. Nothing was written"
            )
    else:
        if not (_interactive_terminal() if interactive is None else interactive):
            raise ScopeError(
                "scope-approve must be run by the reviewer in an interactive terminal, or with "
                "--chat-confirmation after the user approved the card in chat. Nothing was written"
            )
        out(format_approval_card(card))
        typed = _typed(prompt, f"Type the first 8 characters of scope_sha256 to approve as {reviewer!r}: ")
        if typed != summary["scope_sha256"][:8]:
            raise ScopeError("Confirmation did not match scope_sha256; nothing was written")
        if continuity_unverified:
            typed = _typed(prompt, "HTS continuity is NOT verified. Type UNVERIFIED to approve anyway: ")
            if typed != "UNVERIFIED":
                raise ScopeError("Unverified continuity was not acknowledged; nothing was written")

    approved_at = utc_now()
    _write_approved_scope(reviewed_rows, approved_path, reviewer=reviewer, approved_at_utc=approved_at)
    record = build_manifest(approved_path, require_approved=True, manifest_path=manifest)
    if record["scope_sha256"] != summary["scope_sha256"]:
        raise ScopeError("Internal error: approval metadata changed the semantic scope hash")
    if chat is None:
        approval = {
            "method": APPROVAL_METHOD,
            "procedural_boundary": APPROVAL_BOUNDARY,
            "verified_claim": "the scope_sha256 prefix was typed at an interactive terminal after the card was shown",
            "not_verified": "reviewer identity and authority are not cryptographically authenticated",
        }
    else:
        approval = {
            "method": CHAT_APPROVAL_METHOD,
            "procedural_boundary": CHAT_APPROVAL_BOUNDARY,
            "verified_claim": "the agent passed the scope_sha256 prefix the card showed and the user's words of approval",
            "not_verified": "that the user typed these words; the agent ran scope-approve and recorded them as given",
            "user_confirmation": chat,
        }
    record["approval"] = {
        **approval,
        "approval_card_path": _portable_path(card_path, manifest.parent),
        "approval_card_sha256": card_file_hash,
        "reviewer": reviewer,
        "approved_at_utc": approved_at,
    }
    record["hts_history"] = {
        **continuity,
        "continuity_verified": continuity["status"] == "verified",
        "acknowledged_unverified": continuity["status"] != "verified",
    }
    write_manifest(record, manifest)
    return {**record, "manifest_path": str(manifest), "approved_scope_path": str(approved_path)}


def write_manifest(manifest: dict[str, Any], manifest_path: str | Path) -> Path:
    """Write a manifest once; approved scopes are versioned, never overwritten."""

    path = Path(manifest_path).resolve()
    approval = manifest.get("approval") or {}
    history = manifest.get("hts_history") or {}
    if (
        not _approval_record_ok(approval)
        or not (history.get("continuity_verified") or history.get("acknowledged_unverified"))
    ):
        raise ScopeError(
            "A frozen manifest is written only by scope-approve, after the user's confirmation in a terminal or in chat, "
            "with HTS continuity verified or explicitly acknowledged as unverified"
        )
    if path.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing scope manifest: {path}. Save changes as a new scope_version."
        )
    return write_json_atomic(path, manifest)


def _resolve(value: Any, base: Path) -> Path:
    path = Path(str(value or ""))
    return (path if path.is_absolute() else base / path).resolve()


def verify_scope_manifest(manifest_path: str | Path) -> ApprovedScope:
    """Re-verify a frozen, approved scope before it may drive a query.

    Raises :class:`ScopeGateError` when the manifest is missing or was not
    written by scope-approve, when the scope CSV, card or history evidence
    changed after freezing, or when the recomputed hashes differ.
    """

    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise ScopeGateError(f"Scope manifest not found: {path}. The reviewer approves a scope with scope-approve.")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScopeGateError(f"Scope manifest is not valid JSON: {path}") from exc
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ScopeGateError(
            f"Scope manifest {path} uses schema {manifest.get('schema_version')!r}; "
            f"re-create it with scope-approve (schema {SCHEMA_VERSION})."
        )
    validation = manifest.get("validation") or {}
    if not (validation.get("ok") and validation.get("require_approved") and manifest.get("all_records_approved")):
        raise ScopeGateError(f"Scope manifest {path} is not an approved freeze; the reviewer runs scope-approve.")
    scope_path = _resolve(manifest.get("source_scope"), path.parent)
    if not scope_path.is_file():
        raise ScopeGateError(f"Scope CSV referenced by {path} is missing: {scope_path}")
    data = scope_path.read_bytes()
    if sha256(data).hexdigest() != manifest.get("source_file_sha256"):
        raise ScopeGateError(
            f"Scope CSV {scope_path} changed after it was frozen. Approved scopes are immutable: "
            "save the change as a new scope_version and approve that."
        )
    try:
        rows = _parse_scope(data)
        summary, included = _analyse(rows, require_approved=True)
    except ScopeError as exc:
        raise ScopeGateError(f"Frozen scope {scope_path} no longer passes validation: {exc}") from exc
    for key in ("scope_sha256", "record_sha256"):
        if summary[key] != manifest.get(key):
            raise ScopeGateError(f"{key} recorded in {path} does not match {scope_path}; approve a new scope_version.")
    approval = manifest.get("approval") or {}
    if not _approval_record_ok(approval):
        raise ScopeGateError("Frozen scope carries no scope-approve record; hand-edited approval fields do not count")
    try:
        card = _verify_approval_card(
            _resolve(approval.get("approval_card_path"), path.parent),
            expected_scope_sha256=summary["scope_sha256"],
            expected_card_file_sha256=str(approval.get("approval_card_sha256") or ""),
        )
    except (OSError, ScopeError) as exc:
        raise ScopeGateError(f"Approval card verification failed: {exc}") from exc
    if any(
        row["reviewer"] != approval.get("reviewer") or row["approved_at_utc"] != approval.get("approved_at_utc")
        for row in rows
    ):
        raise ScopeGateError("Approved scope rows do not carry the approval record's reviewer and timestamp")

    history = manifest.get("hts_history") or {}
    continuity_verified = history.get("continuity_verified") is True
    if (card.get("continuity") or {}).get("status") != ("verified" if continuity_verified else "unverified"):
        raise ScopeGateError("HTS-continuity status differs between the approval card and the frozen manifest")
    if continuity_verified:
        from .hts_history import HistoryEvidenceError, verify_history_evidence

        evidence_path = _resolve(history.get("evidence_path"), path.parent)
        try:
            if file_sha256(evidence_path) != history.get("evidence_file_sha256"):
                raise HistoryEvidenceError("HTS history evidence file changed after approval")
            evidence = verify_history_evidence(evidence_path, expected_scope_sha256=summary["scope_sha256"])
            if evidence.get("evidence_sha256") != history.get("evidence_sha256"):
                raise HistoryEvidenceError("HTS history evidence identity differs from the frozen manifest")
        except (OSError, HistoryEvidenceError) as exc:
            raise ScopeGateError(f"HTS historical continuity verification failed: {exc}") from exc
    elif history.get("acknowledged_unverified") is not True:
        raise ScopeGateError("Frozen scope has neither HTS continuity evidence nor the reviewer's acknowledgement")
    return _approved_scope(
        summary, included, path, continuity_verified=continuity_verified, approval_method=approval.get("method")
    )


def read_unapproved_scope(scope_path: str | Path) -> ApprovedScope:
    """Read a candidate or reviewed scope for a labeled legacy replay only.

    The result carries the codes and identity of the scope but never passes
    :func:`verify_scope_manifest`, so it cannot drive a plan or acquisition.
    """

    path = Path(scope_path).resolve()
    summary, included = _analyse(_parse_scope(path.read_bytes()), require_approved=False)
    return _approved_scope(summary, included, path, continuity_verified=False)


def _approved_scope(
    summary: dict[str, Any],
    included: list[ScopeLine],
    path: Path,
    *,
    continuity_verified: bool,
    approval_method: str | None = None,
) -> ApprovedScope:
    return ApprovedScope(
        approval_method=approval_method,
        scope_id=summary["scope_id"],
        scope_version=summary["scope_version"],
        product_id=summary["product_id"],
        product_name=summary["product_name"],
        hts_level=summary["hts_level"],
        included=tuple(included),
        quantity_unit=summary["quantity_unit"],
        scope_sha256=summary["scope_sha256"],
        manifest_path=path,
        continuity_verified=continuity_verified,
        second_unit=summary["second_unit"],
        second_units=tuple(summary["second_units"]),
    )


def run(
    scope: str | Path,
    *,
    require_approved: bool,
    manifest: str | Path | None = None,
    approval_card: str | Path | None = None,
    history_evidence: str | Path | None = None,
) -> int:
    """Validate a scope and optionally write a no-approval-effect review card."""

    try:
        if manifest is not None:
            raise ScopeError("scope-validate cannot approve; the reviewer runs scope-approve")
        record = build_manifest(scope, require_approved=require_approved)
        if approval_card is not None:
            card = build_approval_card(scope, output_path=approval_card, history_evidence=history_evidence)
            record = {**record, "approval_card_path": str(Path(approval_card).resolve()), "approval_card": card}
    except (OSError, ScopeError, FileExistsError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return 0


def add_arguments(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Declare the scope-validate options once, for this module and ``run.py scope-validate``."""

    parser.add_argument("--scope", type=Path, required=True, help="Reviewed scope CSV")
    parser.add_argument("--require-approved", action="store_true", help="Reject any row that is not approved")
    parser.add_argument("--manifest", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--approval-card", type=Path, help="Write the immutable card the reviewer reads before approving")
    parser.add_argument(
        "--history-evidence", type=Path, help="hts-history JSON; without it the card says continuity is unverified"
    )
    return parser


def build_parser() -> argparse.ArgumentParser:
    return add_arguments(argparse.ArgumentParser(description="Validate an HTS scope and optionally write its approval card"))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(
        args.scope,
        require_approved=args.require_approved,
        manifest=args.manifest,
        approval_card=args.approval_card,
        history_evidence=args.history_evidence,
    )


if __name__ == "__main__":
    raise SystemExit(main())
