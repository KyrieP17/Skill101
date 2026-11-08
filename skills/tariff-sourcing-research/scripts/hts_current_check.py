#!/usr/bin/env python3
"""Compare a scope's included HTS lines with the current HTS release.

Run this before every DataWeb acquisition. It looks up each included line that
is valid on ``--as-of`` in the public HTS search and reports whether the code
still exists and whether its official description, first quantity unit and
second quantity unit (if the schedule gives one) still match the scope.

The live HTS search shows only the current release, so this check sees changes
made since the scope was reviewed. It is not historical evidence (that comes
from archive edition files via ``hts-history``) and it is not approval. It
never reads a DataWeb token.

Exit status: 0 when nothing changed, 1 when a line needs review, 2 on error.
"""

from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timezone
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Callable


def _load_resolver():
    path = Path(__file__).with_name("hts_scope_resolver.py")
    spec = importlib.util.spec_from_file_location("hts_scope_resolver", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


resolver = _load_resolver()

Fetch = Callable[[str], tuple[str, list[dict[str, Any]]]]


def dotted(code: str) -> str:
    """Return the HTS display form: 850760 -> 8507.60, 8507600010 -> 8507.60.00.10."""

    return ".".join(part for part in (code[:4], code[4:6], code[6:8], code[8:10]) if part)


def _normal(value: Any) -> str:
    return resolver.clean_text(value).rstrip(":").strip().casefold()


def _first_unit(row: dict[str, Any]) -> str:
    units = row.get("units")
    if isinstance(units, list):
        units = units[0] if units else ""
    return _normal(units)


def _second_unit(row: dict[str, Any]) -> str:
    units = row.get("units")
    return _normal(units[1]) if isinstance(units, list) and len(units) > 1 else ""


def read_included(scope_path: Path, as_of: date) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """Split included rows into those valid on ``as_of`` and the rest."""

    with scope_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if (row.get("decision") or "").strip() == "include"]
    if not rows:
        raise ValueError(f"Scope has no included rows: {scope_path}")
    current, skipped = [], []
    for row in rows:
        start = date.fromisoformat(row["valid_from"].strip())
        end = date.fromisoformat(row["valid_to"].strip())
        (current if start <= as_of <= end else skipped).append(row)
    return current, skipped


def check_line(row: dict[str, str], fetch: Fetch) -> dict[str, Any]:
    """Look up one included scope line in the current release."""

    code = resolver.code_digits(row["hts_code"])
    level = len(code)
    url, results = fetch(dotted(code))
    lines = [item for item in results if resolver.code_digits(item.get("htsno")).startswith(code)]
    exact = [item for item in lines if resolver.code_digits(item.get("htsno")) == code]
    # An undivided code has no line of its own: the single line at the next
    # level down carries its text (847130 is shown as 8471.30.01.00, 850760 as
    # 8507.60.00 with statistical lines beneath it).
    if not exact and lines:
        shortest = min(len(resolver.code_digits(item.get("htsno"))) for item in lines)
        top = [item for item in lines if len(resolver.code_digits(item.get("htsno"))) == shortest]
        exact = top if len(top) == 1 else []
    children = [item for item in lines if len(resolver.code_digits(item.get("htsno"))) == 10]
    unit_rows = children if level < 10 and children else exact

    findings: list[str] = []
    current_line = None
    if not lines:
        findings.append("code_missing: no current HTS line starts with this code")
    elif not exact:
        findings.append("no_single_line: several current lines and none carries this code's own text")
    else:
        current_line = exact[0]
        if _normal(current_line.get("description")) != _normal(row.get("description")):
            findings.append("description_changed")
    if lines:
        units = sorted({_first_unit(item) for item in unit_rows} - {""})
        expected = _normal(row.get("quantity_unit"))
        if len(units) > 1:
            findings.append(f"units_mixed: {units}")
        elif units and expected and units[0] != expected:
            findings.append(f"unit_changed: scope {expected!r}, current {units[0]!r}")
        # A second unit counts only when every line under the code has the same one.
        seconds = {_second_unit(item) for item in unit_rows}
        current_second = next(iter(seconds)) if len(seconds) == 1 else ""
        recorded_second = _normal(row.get("second_unit"))
        if current_second != recorded_second:
            findings.append(f"second_unit_changed: scope {recorded_second!r}, current {current_second!r}")

    return {
        "hts_code": code,
        "hts_level": level,
        "valid_from": row["valid_from"],
        "valid_to": row["valid_to"],
        "scope_description": row.get("description", ""),
        "scope_quantity_unit": row.get("quantity_unit", ""),
        "scope_second_unit": row.get("second_unit", ""),
        "search_url": url,
        "current_line": None
        if current_line is None
        else {"htsno": current_line.get("htsno"), "description": resolver.clean_text(current_line.get("description"))},
        "current_children": [
            {
                "htsno": item.get("htsno"),
                "description": resolver.clean_text(item.get("description")),
                "units": resolver.units_text(item.get("units")),
            }
            for item in children
        ],
        "findings": findings,
    }


def build_report(scope_path: Path, as_of: date, fetch: Fetch, *, endpoint: str) -> dict[str, Any]:
    current, skipped = read_included(scope_path, as_of)
    checked = [check_line(row, fetch) for row in current]
    return {
        "schema_version": 1,
        "artifact_type": "hts_current_release_check",
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "as_of": as_of.isoformat(),
        "scope_path": str(scope_path),
        "scope_file_sha256": sha256(scope_path.read_bytes()).hexdigest(),
        "endpoint": endpoint,
        "ok": bool(checked) and not any(item["findings"] for item in checked),
        "checked": checked,
        "not_valid_on_as_of": [
            {"hts_code": row["hts_code"], "valid_from": row["valid_from"], "valid_to": row["valid_to"]} for row in skipped
        ],
        "note": "Current release only: not historical evidence and not approval.",
        "dataweb_token_used": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Check a scope's included HTS lines against the current HTS release")
    parser.add_argument("--scope", type=Path, required=True, help="Reviewed or approved scope CSV")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None, help="Date whose valid lines are checked (default: today)")
    parser.add_argument("--output", type=Path, help="Write the JSON report here")
    parser.add_argument("--force", action="store_true", help="Replace an existing report")
    parser.add_argument("--endpoint", default=resolver.DEFAULT_ENDPOINT, help="Override only for testing or an official endpoint change")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    as_of = args.as_of or date.today()
    try:
        report = build_report(
            args.scope.resolve(),
            as_of,
            lambda keyword: resolver.fetch_results(args.endpoint, keyword, args.timeout),
            endpoint=args.endpoint,
        )
        if not report["checked"]:
            raise ValueError(f"No included scope line is valid on {as_of.isoformat()}; nothing to compare")
        if args.output:
            resolver.write_json(args.output.resolve(), report, force=args.force)
    except (OSError, RuntimeError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
