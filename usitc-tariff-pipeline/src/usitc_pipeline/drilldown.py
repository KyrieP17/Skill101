"""Drill-down menu: is the next study worth doing at HTS8 or HTS10?

The verdict is judged only on the product and its rates (decisions.md D-010),
never on the trade data: the lines at each finer level, their units, the HTS8
base rate, chapter 99 rows in the tariff timeline that cover only some lines,
and line splits inside the study period.

Inputs are files already on disk: a cached response of the HTS REST search the
scope resolver uses (``https://hts.usitc.gov/reststop/search?keyword=<HTS6>``,
saved in the scope's ``history/``), the approved scope's lines with their
validity windows, and the study's tariff timeline.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Sequence

from .timeline import split_list


VERDICT_SAME = "和上一层相同，不用下钻"
VERDICT_DRILL = "税率或附加税在这一层分开，建议下钻"
VERDICT_STATISTICAL = "只有统计拆分，按需"
LEVELS = (8, 10)
MENU_COLUMNS = (
    "parent", "level", "line_count", "lines", "units", "hts8_base_rate", "base_rate_split",
    "partial_duty", "partial_duty_rows", "splits_in_period", "verdict",
)


class DrilldownError(ValueError):
    """The cached HTS structure cannot be used."""


@dataclass(frozen=True)
class ScopeCode:
    """The part of a scope line the menu needs."""

    hts_code: str
    valid_from: date | None
    valid_to: date | None
    units: tuple[str, ...] = ()


def _digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _dotted(code: str) -> str:
    if len(code) == 6:
        return f"{code[:4]}.{code[4:]}"
    if len(code) == 8:
        return f"{code[:4]}.{code[4:6]}.{code[6:]}"
    return f"{code[:4]}.{code[4:6]}.{code[6:8]}.{code[8:]}"


def _unit(value: Any) -> str:
    return str(value or "").strip().lower()


def read_structure(path: str | Path) -> list[dict[str, Any]]:
    """Read a cached HTS REST search response (a JSON list of HTS rows)."""

    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DrilldownError(f"Cannot read the HTS structure {path}: {exc}") from exc
    if not isinstance(data, list) or not data or not all(isinstance(row, dict) and "htsno" in row for row in data):
        raise DrilldownError(f"{path} is not an HTS REST search response (a JSON list of rows with htsno)")
    return data


def _hts8_rate(current: dict[str, dict[str, Any]], code8: str) -> str:
    """The HTS8 general rate; when an HTS8 line has one statistical line, the search
    returns only that HTS10 row (e.g. 8517.13.00.00) and the rate sits on it."""

    rate = str((current.get(code8) or {}).get("general") or "").strip()
    if rate:
        return rate
    found = {
        str(row.get("general") or "").strip() for key, row in current.items() if len(key) == 10 and key.startswith(code8)
    }
    found.discard("")
    return found.pop() if len(found) == 1 else ""


def _active(window: tuple[date | None, date | None], day: date) -> bool:
    start, end = window
    return (start is None or start <= day) and (end is None or day <= end)


def _window(codes: Iterable[ScopeCode]) -> tuple[date | None, date | None]:
    codes = list(codes)
    starts = [code.valid_from for code in codes]
    ends = [code.valid_to for code in codes]
    return (None if None in starts else min(starts), None if None in ends else max(ends))


def build_menu(
    structure: Sequence[dict[str, Any]],
    scope_codes: Sequence[ScopeCode],
    timeline_rows: Iterable[dict[str, Any]] = (),
    *,
    period_start: date,
    period_end: date,
) -> list[dict[str, Any]]:
    """One row per HTS6 parent and finer level (HTS8, HTS10), with a verdict."""

    current = {
        _digits(row.get("htsno")): row
        for row in structure
        if len(_digits(row.get("htsno"))) in LEVELS
    }
    product_rows = [
        row for row in timeline_rows
        if split_list(row.get("applies_to")) and str(row.get("date_type")) == "effective"
    ]
    scope_level = max(len(code.hts_code) for code in scope_codes)
    menu: list[dict[str, Any]] = []
    for parent in sorted({code.hts_code[:6] for code in scope_codes}):
        children = [code for code in scope_codes if code.hts_code.startswith(parent)]
        above_count = 1
        for level in LEVELS:
            lines: dict[str, tuple[date | None, date | None]] = {}
            if level <= scope_level:
                for prefix in sorted({code.hts_code[:level] for code in children}):
                    lines[prefix] = _window(code for code in children if code.hts_code.startswith(prefix))
            else:
                for prefix in sorted({code.hts_code for code in children}):
                    for code in sorted(current):
                        if len(code) == level and code.startswith(prefix):
                            lines[code] = _window(item for item in children if item.hts_code == prefix)
            if not lines:
                continue

            units: dict[str, None] = {}
            for code in lines:
                for key, row in current.items():
                    if len(key) == 10 and key.startswith(code):
                        units.update(dict.fromkeys(_unit(unit) for unit in row.get("units") or []))
                for item in children:
                    if item.hts_code.startswith(code) or code.startswith(item.hts_code):
                        units.update(dict.fromkeys(_unit(unit) for unit in item.units))
            units.pop("", None)

            hts8 = sorted({code[:8] for code in lines})
            rates = {code: _hts8_rate(current, code) for code in hts8}
            rate_text = "; ".join(
                f"{_dotted(code)}: {rate or '不在现行 HTS 结构里'}" for code, rate in rates.items()
            )
            base_split = level == 8 and len({rate for rate in rates.values() if rate}) > 1

            partial: list[str] = []
            for row in product_rows:
                day = date.fromisoformat(str(row["date"]))
                active = [code for code, window in lines.items() if _active(window, day)] or list(lines)
                applies = split_list(row.get("applies_to"))
                covered = [code for code in active if any(code.startswith(prefix) for prefix in applies)]
                if 0 < len(covered) < len(active):
                    partial.append(
                        f"{row['date']} {row.get('measure', '')}: "
                        + ", ".join(_dotted(code) for code in covered)
                        + f"（同期 {len(active)} 行中的 {len(covered)} 行）"
                    )

            splits: list[str] = []
            for code, (start, end) in lines.items():
                if start is not None and period_start < start <= period_end:
                    splits.append(f"{_dotted(code)} 从 {start.isoformat()}")
                if end is not None and period_start <= end < period_end:
                    splits.append(f"{_dotted(code)} 到 {end.isoformat()}")

            if base_split or partial:
                verdict = VERDICT_DRILL
            elif len(lines) > above_count:
                verdict = VERDICT_STATISTICAL
            else:
                verdict = VERDICT_SAME
            menu.append({
                "parent": _dotted(parent),
                "level": f"HTS{level}",
                "line_count": len(lines),
                "lines": "; ".join(_dotted(code) for code in lines),
                "units": "; ".join(units),
                "hts8_base_rate": rate_text,
                "base_rate_split": base_split,
                "partial_duty": bool(partial),
                "partial_duty_rows": " | ".join(partial),
                "splits_in_period": "; ".join(splits),
                "verdict": verdict,
            })
            above_count = len(lines)
    return menu
