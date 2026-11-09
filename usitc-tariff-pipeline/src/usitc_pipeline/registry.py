"""Tariff registry: chapter 99 measures read from primary sources, and a read-only timeline check.

Three CSV files under ``config/tariff_registry/`` (decisions.md D-026):

* ``sources.csv``: one row per primary source that was read, a Federal Register
  document or an HTS archive file, with its URL and the SHA-256 of what was read.
* ``measures.csv``: one row per dated change of a measure (a chapter 99 heading
  or range, the change, origins, the sources that state it, and ``status``:
  ``verified`` when the date, the change and the headings were read in those
  sources, else ``待核对`` with the open point in ``notes``).
* ``coverage.csv``: for a measure and an HTS6 code, ``covered``, ``exempt``,
  ``not_covered`` or ``suspended``, with the HTS note that says so.

The registry never changes a study.  Study timelines stay the study's own input
(they enter ``study_sha256``); ``registry-check`` only reports which timeline
rows agree with the registry, which differ in date, which it does not hold,
and which registry measures that reach the study's codes have no timeline row.
Pure Python: no pandas, so it runs wherever ``status`` runs.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
import re
from typing import Any, Iterable

from .timeline import is_primary_source, split_list


SOURCE_COLUMNS = (
    "source_id", "kind", "citation", "title", "document_number", "publication_date",
    "signing_date", "url", "sha256", "retrieved", "notes",
)
MEASURE_COLUMNS = (
    "measure_id", "date", "date_type", "authority", "headings", "change", "origins",
    "products", "sources", "hts_note", "status", "notes",
)
COVERAGE_COLUMNS = ("measure_id", "hts", "coverage", "evidence", "status", "notes")
SOURCE_KINDS = ("federal_register", "hts_archive")
DATE_TYPES = ("announced", "signed", "effective", "ended")
PRODUCTS = ("all", "list")
STATUSES = ("verified", "待核对")
COVERAGES = ("covered", "exempt", "not_covered", "suspended")
HEADING = re.compile(r"9903\.(\d{2})\.(\d{2})(?:\s*[-–±]\s*(?:(?:9903\.)?(\d{2}))?\.(\d{2}))?")
HTS6 = re.compile(r"\d{4}\.\d{2}")
SHA256 = re.compile(r"[0-9a-f]{64}")
ID = re.compile(r"[a-z0-9][a-z0-9-]*")


class RegistryError(ValueError):
    """A registry file is invalid."""


@dataclass(frozen=True)
class Registry:
    directory: Path
    sources: dict[str, dict[str, str]]
    measures: list[dict[str, Any]]
    coverage: dict[tuple[str, str], dict[str, str]] = field(default_factory=dict)

    def measure(self, measure_id: str) -> dict[str, Any]:
        return next(row for row in self.measures if row["measure_id"] == measure_id)


def heading_ranges(text: str) -> list[tuple[int, int]]:
    """``"9903.01.43–.62; 9903.02.02-9903.02.73"`` -> [(143, 162), (202, 273)] (chapter 99 keys)."""

    ranges = []
    for match in HEADING.finditer(str(text or "")):
        group, item, end_group, end_item = match.groups()
        start = int(group) * 100 + int(item)
        end = start if end_item is None else int(end_group or group) * 100 + int(end_item)
        ranges.append((min(start, end), max(start, end)))
    return ranges


def headings_overlap(left: Iterable[tuple[int, int]], right: Iterable[tuple[int, int]]) -> bool:
    right = list(right)
    return any(a <= d and c <= b for a, b in left for c, d in right)


def _origins(text: str) -> tuple[bool, set[str], set[str]]:
    """(applies to every origin, named origins, origins excluded)."""

    text = str(text or "").lower()
    named, excluded = set(), set()
    wildcard = text.startswith("all") or "economies" in text
    head, tail = (re.split(r"\bexcept\b", text, maxsplit=1) + [""])[:2]
    for part in re.split(r"[;,()]|\band\b|\bincl\.", tail if wildcard else head):
        name = part.strip()
        if name:
            (excluded if wildcard else named).add(name)
    return wildcard, named, excluded


def origins_overlap(left: str, right: str) -> bool:
    a_all, a_named, a_out = _origins(left)
    b_all, b_named, b_out = _origins(right)
    if a_all and b_all:
        return True
    if a_all:
        return bool(b_named - a_out)
    if b_all:
        return bool(a_named - b_out)
    return bool(a_named & b_named)


def _read(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = tuple(reader.fieldnames or ())
            rows = [{column: str(row.get(column) or "").strip() for column in columns} for row in reader]
    except FileNotFoundError as exc:
        raise RegistryError(f"Registry file not found: {path}") from exc
    if header != columns:
        raise RegistryError(f"{path.name}: columns must be exactly {list(columns)}, got {list(header)}")
    if not rows:
        raise RegistryError(f"{path.name} has no rows")
    return rows


def _iso(value: str, where: str, *, blank: bool = False) -> None:
    if blank and not value:
        return
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise RegistryError(f"{where}: dates are YYYY-MM-DD, got {value!r}") from exc


def _source_ids(value: str) -> list[str]:
    """``"fr-90-9121; hts-2026rev22-ch99 note 2(s)"`` -> the leading id of each item."""

    return [item.split()[0] for item in split_list(value)]


def load_registry(directory: str | Path) -> Registry:
    """Read and validate ``sources.csv``, ``measures.csv`` and ``coverage.csv``."""

    directory = Path(directory)
    sources: dict[str, dict[str, str]] = {}
    for number, row in enumerate(_read(directory / "sources.csv", SOURCE_COLUMNS), start=2):
        where = f"sources.csv line {number}"
        if not ID.fullmatch(row["source_id"]) or row["source_id"] in sources:
            raise RegistryError(f"{where}: source_id must be a unique lowercase slug, got {row['source_id']!r}")
        if row["kind"] not in SOURCE_KINDS:
            raise RegistryError(f"{where}: kind must be one of {list(SOURCE_KINDS)}")
        if not is_primary_source(row["url"]):
            raise RegistryError(f"{where}: url must be on the HTS archive, the Federal Register, USTR or CBP")
        if not SHA256.fullmatch(row["sha256"]):
            raise RegistryError(f"{where}: sha256 must be 64 lowercase hex digits (the bytes that were read)")
        if not row["citation"]:
            raise RegistryError(f"{where}: citation is required")
        for column in ("publication_date", "signing_date"):
            _iso(row[column], f"{where} {column}", blank=True)
        _iso(row["retrieved"], f"{where} retrieved")
        sources[row["source_id"]] = row

    measures: list[dict[str, Any]] = []
    seen: set[str] = set()
    for number, row in enumerate(_read(directory / "measures.csv", MEASURE_COLUMNS), start=2):
        where = f"measures.csv line {number}"
        if not ID.fullmatch(row["measure_id"]) or row["measure_id"] in seen:
            raise RegistryError(f"{where}: measure_id must be a unique lowercase slug, got {row['measure_id']!r}")
        seen.add(row["measure_id"])
        _iso(row["date"], where)
        if row["date_type"] not in DATE_TYPES:
            raise RegistryError(f"{where}: date_type must be one of {list(DATE_TYPES)}")
        if row["products"] not in PRODUCTS:
            raise RegistryError(f"{where}: products must be one of {list(PRODUCTS)}")
        if row["status"] not in STATUSES:
            raise RegistryError(f"{where}: status must be one of {list(STATUSES)}")
        for column in ("authority", "change", "origins"):
            if not row[column]:
                raise RegistryError(f"{where}: {column} is required")
        items = split_list(row["headings"])
        ranges = heading_ranges(row["headings"])
        if len(ranges) != len(items) or any(not HEADING.fullmatch(item) for item in items):
            raise RegistryError(f"{where}: headings are 9903.xx.xx or 9903.xx.xx-9903.yy.yy separated by ';'")
        ids = _source_ids(row["sources"])
        if not ids:
            raise RegistryError(f"{where}: sources is required (source_id values from sources.csv)")
        unknown = [item for item in ids if item not in sources]
        if unknown:
            raise RegistryError(f"{where}: unknown source ids {unknown}")
        if row["status"] == "待核对" and not row["notes"]:
            raise RegistryError(f"{where}: a 待核对 row says in notes what is still open")
        measures.append({**row, "heading_ranges": ranges, "source_ids": ids})

    coverage: dict[tuple[str, str], dict[str, str]] = {}
    for number, row in enumerate(_read(directory / "coverage.csv", COVERAGE_COLUMNS), start=2):
        where = f"coverage.csv line {number}"
        if row["measure_id"] not in seen:
            raise RegistryError(f"{where}: unknown measure_id {row['measure_id']!r}")
        if not HTS6.fullmatch(row["hts"]):
            raise RegistryError(f"{where}: hts is an HTS6 code like 8507.60, got {row['hts']!r}")
        if row["coverage"] not in COVERAGES:
            raise RegistryError(f"{where}: coverage must be one of {list(COVERAGES)}")
        if row["status"] not in STATUSES:
            raise RegistryError(f"{where}: status must be one of {list(STATUSES)}")
        ids = _source_ids(row["evidence"])
        if not ids or any(item not in sources for item in ids):
            raise RegistryError(f"{where}: evidence starts with a source_id from sources.csv")
        key = (row["measure_id"], row["hts"])
        if key in coverage:
            raise RegistryError(f"{where}: repeats {key}")
        coverage[key] = row
    return Registry(directory=directory, sources=sources, measures=sorted(measures, key=lambda row: row["date"]), coverage=coverage)


def _digits(code: str) -> str:
    return re.sub(r"\D", "", str(code))


def scope_coverage(registry: Registry, measure: dict[str, Any], codes: Iterable[str]) -> dict[str, str] | None:
    """The coverage row of ``measure`` for the scope's codes (an HTS6 row matches any finer code)."""

    codes = [_digits(code) for code in codes]
    for (measure_id, hts), row in registry.coverage.items():
        digits = _digits(hts)
        if measure_id == measure["measure_id"] and any(code.startswith(digits) or digits.startswith(code) for code in codes):
            return row
    return None


def _brief(measure: dict[str, Any], registry: Registry) -> dict[str, Any]:
    return {
        "measure_id": measure["measure_id"],
        "date": measure["date"],
        "date_type": measure["date_type"],
        "headings": measure["headings"],
        "change": measure["change"],
        "origins": measure["origins"],
        "status": measure["status"],
        "sources": [registry.sources[item]["citation"] for item in measure["source_ids"]],
    }


def check_timeline(
    registry: Registry, records: Iterable[dict[str, str]], codes: Iterable[str], *, since: str | None = None
) -> dict[str, Any]:
    """Compare timeline rows with the registry for a scope's codes; read-only.

    A row ``matches`` a measure on the same date whose origins overlap and whose
    headings overlap (or one side names none); ``date_differs`` when only the
    headings and origins agree; else ``not_in_registry``.  Registry measures
    that reach the codes (coverage ``covered``, ``exempt`` or ``suspended``, or
    ``products = all`` with no coverage row) and that no row matched are
    ``missing``; with ``since`` (the study's first day), earlier unmatched
    measures are listed apart as ``before_study_period``.
    """

    codes = list(codes)
    rows = []
    used: set[str] = set()
    for record in records:
        ranges = heading_ranges(record.get("measure", ""))
        candidates = [m for m in registry.measures if origins_overlap(record.get("origins", ""), m["origins"])]
        same_day = [
            m for m in candidates
            if m["date"] == record["date"] and (not ranges or not m["heading_ranges"] or headings_overlap(ranges, m["heading_ranges"]))
        ]
        if same_day:
            verdict, found = "match", same_day
        else:
            found = [m for m in candidates if ranges and headings_overlap(ranges, m["heading_ranges"])]
            verdict = "date_differs" if found else "not_in_registry"
            if found:
                target = date.fromisoformat(record["date"])
                found = sorted(found, key=lambda m: abs((date.fromisoformat(m["date"]) - target).days))[:2]
        used.update(m["measure_id"] for m in found)
        rows.append(
            {
                "date": record["date"],
                "date_type": record.get("date_type", ""),
                "layer": record.get("layer", ""),
                "measure": record.get("measure", ""),
                "verdict": verdict,
                "registry": [
                    {**_brief(m, registry), "coverage": (scope_coverage(registry, m, codes) or {}).get("coverage", "")}
                    for m in found
                ],
            }
        )
    missing, not_covered, earlier = [], [], []
    for measure in registry.measures:
        row = scope_coverage(registry, measure, codes)
        if row is None and measure["products"] == "list":
            continue
        if row is not None and row["coverage"] == "not_covered":
            not_covered.append({**_brief(measure, registry), "evidence": row["evidence"]})
            continue
        if measure["measure_id"] in used:
            continue
        if since and measure["date"] < since:
            earlier.append(measure["measure_id"])
            continue
        missing.append(
            {
                **_brief(measure, registry),
                "coverage": row["coverage"] if row else "not recorded",
                "coverage_evidence": row["evidence"] if row else "",
                "coverage_status": row["status"] if row else "待核对",
            }
        )
    verdicts = [row["verdict"] for row in rows]
    return {
        "registry": summary(registry),
        "scope_codes": codes,
        "timeline_rows": rows,
        "missing_from_timeline": missing,
        "checked_not_covered": not_covered,
        "before_study_period": earlier,
        "counts": {
            "match": verdicts.count("match"),
            "date_differs": verdicts.count("date_differs"),
            "not_in_registry": verdicts.count("not_in_registry"),
            "missing_from_timeline": len(missing),
        },
        "note": "Read-only. The registry never edits a timeline; changing a timeline CSV changes study_sha256 and needs a new analysis run.",
    }


def summary(registry: Registry) -> dict[str, Any]:
    open_measures = [m["measure_id"] for m in registry.measures if m["status"] != "verified"]
    open_coverage = [f"{key[0]} {key[1]}" for key, row in registry.coverage.items() if row["status"] != "verified"]
    return {
        "directory": registry.directory.as_posix(),
        "sources": len(registry.sources),
        "measures": len(registry.measures),
        "coverage_rows": len(registry.coverage),
        "measures_待核对": open_measures,
        "coverage_待核对": open_coverage,
    }
