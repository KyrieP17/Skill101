"""Tariff timelines: dated tariff measures a study can show next to the trade data.

Two layers, both optional and both plain CSV under ``config/tariff_timelines/``:

* ``<scope_id>.csv``: measures on HTS lines (for example a Section 301 rate on
  one HTS10 line).  ``applies_to`` names HTS8 or HTS10 codes; blank means the
  whole scope.
* ``_country.csv``: measures on origins regardless of product (for example an
  additional duty on every product of one country).

A study names them in ``[timeline] file`` and ``[timeline] country``.  The rows
enter ``study_sha256`` only, so a timeline never changes a query plan.

Rows are research notes, not a tariff database.  ``review_status`` is
``draft`` until the user checks a row and writes ``user_checked`` themselves;
a row whose ``source_url`` is not a primary source (the HTS archive, the
Federal Register, USTR or CBP) is reported as 待核对.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path
import re
from typing import Any, Iterable
from urllib.parse import urlparse


TIMELINE_COLUMNS = (
    "date",
    "date_type",
    "measure",
    "rate_change",
    "applies_to",
    "origins",
    "source",
    "source_url",
    "review_status",
    "notes",
)
DATE_TYPES = ("announced", "effective")
REVIEW_STATUSES = ("draft", "user_checked")
PRIMARY_SOURCE_HOSTS = ("hts.usitc.gov", "federalregister.gov", "ustr.gov", "cbp.gov")
CODE_PATTERN = re.compile(r"\d{8}(\d{2})?")


class TimelineError(ValueError):
    """A tariff timeline file is invalid."""


def is_primary_source(url: str) -> bool:
    """True when ``url`` is on the HTS archive, the Federal Register, USTR or CBP."""

    host = (urlparse(str(url or "").strip()).hostname or "").lower()
    return any(host == name or host.endswith("." + name) for name in PRIMARY_SOURCE_HOSTS)


def split_list(value: Any) -> list[str]:
    """``"a; b"`` -> ``["a", "b"]``; blank -> ``[]``."""

    return [item.strip() for item in str(value or "").split(";") if item.strip()]


def read_timeline(path: str | Path, *, layer: str) -> list[dict[str, str]]:
    """Read and validate one timeline CSV; every row gets its ``layer``.

    ``layer`` is ``"product"`` (rows may name HTS8/HTS10 codes) or
    ``"country"`` (rows must name origins and no codes).
    """

    path = Path(path)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = tuple(reader.fieldnames or ())
            rows = list(reader)
    except FileNotFoundError as exc:
        raise TimelineError(f"Timeline file not found: {path}") from exc
    if header != TIMELINE_COLUMNS:
        raise TimelineError(f"{path.name}: columns must be exactly {list(TIMELINE_COLUMNS)}, got {list(header)}")
    records: list[dict[str, str]] = []
    for number, row in enumerate(rows, start=2):
        where = f"{path.name} line {number}"
        record = {column: str(row.get(column) or "").strip() for column in TIMELINE_COLUMNS}
        try:
            date.fromisoformat(record["date"])
        except ValueError as exc:
            raise TimelineError(f"{where}: date must be YYYY-MM-DD, got {record['date']!r}") from exc
        if record["date_type"] not in DATE_TYPES:
            raise TimelineError(f"{where}: date_type must be one of {list(DATE_TYPES)}")
        if not record["measure"]:
            raise TimelineError(f"{where}: measure is required")
        if record["review_status"] not in REVIEW_STATUSES:
            raise TimelineError(f"{where}: review_status must be one of {list(REVIEW_STATUSES)}")
        if not record["source"]:
            raise TimelineError(f"{where}: source is required (write who or what the date came from)")
        codes = split_list(record["applies_to"])
        bad = [code for code in codes if not CODE_PATTERN.fullmatch(code)]
        if bad:
            raise TimelineError(f"{where}: applies_to takes HTS8 or HTS10 digits separated by ';', got {bad}")
        if layer == "country":
            if codes:
                raise TimelineError(f"{where}: a country-layer row applies to origins, not HTS codes")
            if not split_list(record["origins"]):
                raise TimelineError(f"{where}: a country-layer row must name its origins")
        elif layer != "product":
            raise TimelineError(f"Unknown timeline layer {layer!r}")
        records.append({**record, "layer": layer})
    if not records:
        raise TimelineError(f"{path.name} has no rows")
    return records


def check_codes(records: Iterable[dict[str, str]], included_codes: Iterable[str]) -> None:
    """Every ``applies_to`` code must be one of the scope's codes, a parent or a child of one."""

    included = [str(code) for code in included_codes]
    for record in records:
        for code in split_list(record.get("applies_to")):
            if not any(code.startswith(item) or item.startswith(code) for item in included):
                raise TimelineError(
                    f"Timeline row {record['date']} {record['measure']!r} applies to {code}, "
                    f"which is outside the scope's codes {included}"
                )


def annotate(records: Iterable[dict[str, str]]) -> list[dict[str, Any]]:
    """Add ``primary_source`` and ``needs_check`` and sort by date (stable)."""

    rows = [
        {
            **record,
            "primary_source": is_primary_source(record.get("source_url", "")),
            "user_checked": record.get("review_status") == "user_checked",
        }
        for record in records
    ]
    for row in rows:
        row["needs_check"] = not row["primary_source"]
    return sorted(rows, key=lambda row: row["date"])
