"""``brief-check``: what a brief must pass before ``report-pdf`` prints it (D-024).

Errors (``report-pdf`` refuses the brief):

* ``title``: the brief starts with one ``# Title``.
* ``sections``: the six sections of deliverables.md, in order (key
  findings, tariff timeline, body, questions, drill-down menu, notes),
  recognised by keywords in their ``##`` headings.  The HTS4 map left the
  brief (D-025): an HTS4 section is a warning, like any other extra section.
* ``image``: every embedded image is a chart in ``chart_manifest.json``.
* ``label``: evidence labels are ``**[Measured]**``, ``**[Derived]**``,
  ``**[Inference]**`` or ``**[Unknown]**`` (combined with " / ").
* ``source``: every cited ``.csv`` / ``.json`` is a validated output of this
  analysis (listed in ``analysis_manifest.json`` or ``chart_manifest.json``).
* ``unsourced`` / ``unlabeled``: a paragraph, list item or table with a
  number in a unit (%, 个百分点, 亿美元, 美元/部, 倍 ...) cites a source and
  carries a label.  A table takes both from the label line right after it.
* ``untraced``: each such number appears, after rounding to the digits
  shown, in a file the block cites (a % may be a fraction column).  A number
  no table holds goes into ``[[derived]]`` (``tables/calculations.csv``).
* ``hash``: a hash in the brief is a prefix of this study's scope, plan or
  study hash.
* ``causal``: no hedged cause ("可能是", "probably"), and no causal verb
  ("因为", "导致", "due to", ...) outside a question.
* ``mention``: with ``tables/mention_list.csv``, every item appears.

Warnings: the key findings are not 4-6 numbered items.  Every numeric token is
counted as parsed, ambiguous, or skipped; an adjacent but unknown unit is an
error instead of being silently skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np
import pandas as pd


BRIEF = "brief.md"
ALLOWED_LABELS = ("Measured", "Derived", "Inference", "Unknown")
SECTIONS = (
    ("findings", ("发现", "finding")),
    ("timeline", ("时间线", "timeline")),
    ("body", ("主体", "body", "scope total")),
    ("questions", ("问题", "question")),
    ("drilldown", ("下钻", "drill")),
    ("notes", ("口径", "说明", "notes", "measures")),
)
# Classification order: specific keys first, so "Drill-down (HTS8)" is not the body.
CLASSIFY = ("findings", "timeline", "questions", "drilldown", "notes", "body")
HEDGES = ("可能是", "也许是", "或许是", "大概是", "probably", "likely", "possibly", "may be", "suggests")
CAUSAL = (
    "可能因为", "可能由于", "因为", "由于", "导致", "归因", "归功于", "得益于", "受益于", "源于", "驱动", "推动",
    "引发", "造成", "迫使", "促使", "产能转移", "供应链转移", "转产", "搬迁",
    "because", "due to", "caused", "driven", "led to", "leads to", "as a result of", "thanks to",
    "attributable", "owing to", "resulted in", "relocat", "shifted production", "moved production",
)

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
TABLE_LINE = re.compile(r"^\s*\|")
TABLE_RULE = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
LABEL = re.compile(r"\*\*\[([^\]]+)\]\*\*")
SOURCE = re.compile(r"`(?:\./)?([^`\s]+\.(?:csv|json|png))`")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)\s]+)\)")
HEX = re.compile(r"`(?:[a-z]+-)?([0-9a-f]{8,64})`")
# Text that holds digits but no measured number: code spans, images, dates,
# months and month ranges, HTS and chapter 99 codes.
BLANK = (
    re.compile(r"`[^`]*`"),
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),
    re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)"),
    re.compile(r"(?<!\d)\d{4}-\d{2}(?:\.\.(?:\d{4}-)?\d{2})?(?!\d)"),
    re.compile(r"(?<![\d.])\d{4}\.\d{2}(?:\.\d{2}){0,2}(?![\d])"),
)
NUMBER = re.compile(r"(?<![A-Za-z0-9_.,])(?P<sign>[+\-−±]?)(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)(?!\d)")
CURRENCY_PREFIX = re.compile(r"(?:US\$|\$|USD)\s*[+\-−]?$", re.IGNORECASE)
UNKNOWN_CURRENCY = r"(?:EUR|GBP|JPY|CNY|RMB|CAD|AUD|CHF|KRW|TWD|HKD|SGD|INR|MXN|VND)"
UNKNOWN_PREFIX_UNIT = re.compile(rf"(?:\b{UNKNOWN_CURRENCY}|[A-Z]{{1,3}}\$)\s*[+\-−]?$")
# Kinds of table cells a shown number can come from (from the column name, see column_kind).
PCT, PP, USD, QUANTITY, UNIT_VALUE, RATIO = "pct", "pp", "usd", "quantity", "unit_value", "ratio"
# unit text -> (scales the shown number may have been divided by, cell kinds it may come from); longest first.
UNITS: tuple[tuple[str, tuple[float, ...], frozenset[str]], ...] = tuple(sorted(
    ((text, scales, frozenset(kinds))
    for text, scales, kinds in (
        ("个百分点", (1.0, 0.01), {PP}), ("百分点", (1.0, 0.01), {PP}), ("pp", (1.0, 0.01), {PP}),
        ("%", (1.0, 0.01), {PCT}), ("％", (1.0, 0.01), {PCT}),
        ("亿美元", (1e8,), {USD}), ("百万美元", (1e6,), {USD}), ("千美元", (1e3,), {USD}), ("万美元", (1e4,), {USD}),
        ("亿部", (1e8,), {QUANTITY}), ("亿件", (1e8,), {QUANTITY}), ("亿", (1e8,), {USD, QUANTITY}),
        ("万吨", (1e7, 1e4), {QUANTITY}), ("万部", (1e4,), {QUANTITY}), ("万件", (1e4,), {QUANTITY}),
        ("万", (1e4,), {USD, QUANTITY}), ("百万", (1e6,), {USD, QUANTITY}),
        ("USD per kilogram", (1.0,), {UNIT_VALUE}), ("USD/kilogram", (1.0,), {UNIT_VALUE}),
        ("USD per unit", (1.0,), {UNIT_VALUE}), ("USD/unit", (1.0,), {UNIT_VALUE}),
        ("USD per piece", (1.0,), {UNIT_VALUE}), ("USD/piece", (1.0,), {UNIT_VALUE}),
        ("USD per kg", (1.0,), {UNIT_VALUE}), ("USD/kg", (1.0,), {UNIT_VALUE}),
        ("US$ per unit", (1.0,), {UNIT_VALUE}), ("US$/unit", (1.0,), {UNIT_VALUE}),
        ("per kilogram", (1.0,), {UNIT_VALUE}), ("/kilogram", (1.0,), {UNIT_VALUE}),
        ("per unit", (1.0,), {UNIT_VALUE}), ("/unit", (1.0,), {UNIT_VALUE}),
        ("per piece", (1.0,), {UNIT_VALUE}), ("/piece", (1.0,), {UNIT_VALUE}),
        ("per kg", (1.0,), {UNIT_VALUE}), ("/kg", (1.0,), {UNIT_VALUE}),
        ("美元/部", (1.0,), {UNIT_VALUE}), ("美元/件", (1.0,), {UNIT_VALUE}), ("美元/千克", (1.0,), {UNIT_VALUE}),
        ("美元/公斤", (1.0,), {UNIT_VALUE}), ("美元/吨", (1.0,), {UNIT_VALUE}),
        ("USD billion", (1e9,), {USD}), ("USD bn", (1e9,), {USD}),
        ("billion USD", (1e9,), {USD}), ("bn USD", (1e9,), {USD}),
        ("USD million", (1e6,), {USD}), ("USD mn", (1e6,), {USD}),
        ("million USD", (1e6,), {USD}), ("mn USD", (1e6,), {USD}),
        ("美元", (1.0,), {USD, UNIT_VALUE}), ("USD", (1.0,), {USD, UNIT_VALUE}),
        ("倍", (1.0,), {RATIO}), ("×", (1.0,), {RATIO}), ("x", (1.0,), {RATIO}),
        ("billion", (1e9,), {USD}), ("bn", (1e9,), {USD}), ("million", (1e6,), {USD, QUANTITY}), ("mn", (1e6,), {USD, QUANTITY}),
        ("千克", (1.0,), {QUANTITY}), ("公斤", (1.0,), {QUANTITY}), ("kg", (1.0,), {QUANTITY}),
        ("吨", (1e3, 1.0), {QUANTITY}), ("部", (1.0,), {QUANTITY}), ("件", (1.0,), {QUANTITY}),
    )), key=lambda item: len(item[0]), reverse=True)
)
UNIT = re.compile(r"\s*(" + "|".join(re.escape(text) for text, _, _ in UNITS) + r")(?![A-Za-z]|(?<=部)分)", re.IGNORECASE)
SCALES = {text: scales for text, scales, _ in UNITS}
KINDS = {text: kinds for text, _, kinds in UNITS}
CANONICAL_UNIT = {text.casefold(): text for text, _, _ in UNITS}
# Adjacent measurement-looking text that is not in ``UNITS`` is not silently
# treated as a count.  Long ordinary nouns ("countries", "lines") remain
# intentional unitless counts, while currency/short unit tokens and per-X
# phrases are surfaced for correction.
UNKNOWN_SUFFIX_UNIT = re.compile(
    rf"\s*(?P<unit>(?:(?i:per)\s+[A-Za-z][A-Za-z-]*|/[A-Za-z][A-Za-z-]*|\b{UNKNOWN_CURRENCY}\b|"
    r"\b(?:LB|LBS|OZ|GM|MT|EA|PC|PCS|GB|MB|TB|GBPS|KWH|MWH|KW|MW)\b))",
    re.IGNORECASE,
)
# Cells that can back any unit: free text (a timeline's "+10%") and the generic
# value columns of the pipeline's own brief tables.
ANY = "any"
SEPARATOR = re.compile(r"^\s*(?:→|->|–|—|~|～|-|至|到|对|vs\.?)\s*$")
HEADER_UNIT = re.compile(r"[（(]([^（）()]*)[）)]\s*$")


class BriefCheckError(ValueError):
    """The brief cannot be checked (missing, unreadable)."""


@dataclass
class Number:
    text: str
    value: float
    decimals: int
    unit: str | None
    start: int
    end: int
    ambiguous_unit: str | None = None


@dataclass
class Block:
    kind: str  # paragraph, item, table, heading
    line: int
    lines: list[tuple[int, str]]
    section: str | None
    level: int = 0  # heading level
    marker: str = ""  # list marker: "-", "1." ...
    labels: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(text for _, text in self.lines)


def classify(heading: str) -> str | None:
    lowered = heading.casefold()
    keys = dict(SECTIONS)
    for key in CLASSIFY:
        if any(word in lowered for word in keys[key]):
            return key
    return None


def parse_blocks(text: str) -> list[Block]:
    blocks: list[Block] = []
    current: Block | None = None
    section: str | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            current = None
            continue
        heading = HEADING.match(stripped) if stripped.startswith("#") else None
        if heading:
            level = len(heading.group(1))
            if level == 2:
                section = classify(heading.group(2))
            blocks.append(Block("heading", number, [(number, heading.group(2))], section, level=level))
            current = None
            continue
        if TABLE_LINE.match(line):
            if current is None or current.kind != "table":
                current = Block("table", number, [], section)
                blocks.append(current)
            current.lines.append((number, stripped))
            continue
        item = LIST_ITEM.match(line)
        if item:
            current = Block("item", number, [(number, item.group(3))], section, marker=item.group(2))
            blocks.append(current)
            continue
        if current is not None and current.kind in ("paragraph", "item"):
            current.lines.append((number, stripped))
            continue
        current = Block("paragraph", number, [(number, stripped)], section)
        blocks.append(current)
    for block in blocks:
        if block.kind != "heading":
            block.labels = LABEL.findall(block.text)
            block.sources = [path.removeprefix("./") for path in SOURCE.findall(block.text)]
    for index, block in enumerate(blocks[:-1]):
        after = blocks[index + 1]
        if block.kind == "table" and after.kind == "paragraph" and after.text.lstrip().startswith("**["):
            block.labels += after.labels
            block.sources += after.sources
    return blocks


def _blank(text: str) -> str:
    for pattern in BLANK:
        text = pattern.sub(lambda match: " " * len(match.group(0)), text)
    return text


def numbers(text: str, default_unit: str | None = None) -> list[Number]:
    """Numbers in ``text`` with their unit; a number before "→", "–" ... takes the next one's unit."""

    clean = _blank(text)
    found: list[Number] = []
    for match in NUMBER.finditer(clean):
        raw = match.group("num")
        value = float(raw.replace(",", ""))
        if match.group("sign") in ("-", "−"):
            value = -value
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        unit_match = UNIT.match(clean, match.end())
        unit = CANONICAL_UNIT[unit_match.group(1).casefold()] if unit_match else None
        end = unit_match.end() if unit_match else match.end()
        currency_prefix = CURRENCY_PREFIX.search(clean[: match.start()])
        if unit is None and currency_prefix:
            unit = "USD"
        ambiguous_unit = None
        if unit is None:
            unknown_prefix = UNKNOWN_PREFIX_UNIT.search(clean[: match.start()])
            unknown_suffix = UNKNOWN_SUFFIX_UNIT.match(clean, match.end())
            if unknown_prefix:
                ambiguous_unit = unknown_prefix.group(0).strip()
            elif unknown_suffix:
                ambiguous_unit = unknown_suffix.group("unit").strip()
                end = unknown_suffix.end()
        found.append(Number(text[match.start():end].strip(), value, decimals, unit, match.start(), end, ambiguous_unit))
    for index in range(len(found) - 2, -1, -1):
        this, after = found[index], found[index + 1]
        if this.unit is None and after.unit and SEPARATOR.match(clean[this.end:after.start]):
            this.unit = after.unit
    if default_unit:
        for item in found:
            item.unit = item.unit or default_unit
    return found


def _cells(line: str) -> list[str]:
    body = line.strip()
    body = body[1:] if body.startswith("|") else body
    body = body[:-1] if body.endswith("|") and not body.endswith("\\|") else body
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", body)]


def header_unit(header: str) -> str | None:
    """A column's unit: in trailing brackets ("进口额（亿美元）", "Value (USD bn)"), or the whole header ("美元/部")."""

    text = header.strip()
    bracket = HEADER_UNIT.search(text)
    if bracket:
        found = [CANONICAL_UNIT[match.group(1).casefold()] for match in UNIT.finditer(bracket.group(1))]
        if found:
            return max(found, key=lambda unit: max(SCALES[unit]))
    whole = UNIT.fullmatch(text)
    return CANONICAL_UNIT[whole.group(1).casefold()] if whole else None


def block_numbers(block: Block) -> list[tuple[int, Number]]:
    if block.kind != "table":
        result: list[tuple[int, Number]] = []
        for line, text in block.lines:
            result += [(line, item) for item in numbers(text)]
        return result
    rows = [(line, _cells(text)) for line, text in block.lines if not TABLE_RULE.match(text)]
    if not rows:
        return []
    units = [header_unit(cell) for cell in rows[0][1]]
    result = []
    for line, cells in rows[1:]:
        for column, cell in enumerate(cells):
            result += [(line, item) for item in numbers(cell, units[column] if column < len(units) else None)]
    return result


def column_kind(name: str) -> str | None:
    """Which kind of number a column holds, from its name; None for periods, codes and counts."""

    name = name.casefold()
    if name.endswith("_pp"):
        return PP
    if name.endswith("_pct") or "share" in name or "rate" in name:
        return PCT
    if name.startswith("value_change") and name.endswith("_usd"):
        return USD  # value_change_from_unit_value_usd is dollars of value change, not a unit value
    if "unit_value" in name:
        return UNIT_VALUE
    if name.endswith("_usd"):
        return USD
    if "quantity" in name:
        return QUANTITY
    if name == "ratio" or name.endswith("_ratio"):
        return RATIO
    return None


def _row_kind(unit: str) -> str:
    """calculations.csv: the kind of value, base_value and change from the row's unit."""

    if unit == "USD":
        return USD
    if unit == "pct":
        return PCT
    if unit.startswith("USD per"):
        return UNIT_VALUE
    return QUANTITY


class _Index:
    """Sorted absolute values of the numbers in each cited file, by cell kind."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.cache: dict[str, dict[str, np.ndarray]] = {}

    def values(self, relative: str) -> dict[str, np.ndarray]:
        if relative not in self.cache:
            path = self.output_dir / relative
            found: dict[str, list[float]] = {}
            if path.suffix == ".json":
                self._walk(json.loads(path.read_text(encoding="utf-8")), None, found)
            else:
                frame = pd.read_csv(path, dtype=str, keep_default_na=False)
                if {"unit", "value", "base_value", "change"} <= set(frame.columns):  # calculations.csv
                    for _, row in frame.iterrows():
                        kind = _row_kind(row["unit"])
                        for column, cell_kind in (("value", kind), ("base_value", kind), ("growth_pct", PCT), ("ratio", RATIO),
                                                  ("change", PP if kind == PCT else kind)):
                            self._number(row.get(column, ""), cell_kind, found)
                    frame = frame.drop(columns=[c for c in ("value", "base_value", "change", "growth_pct", "ratio") if c in frame])
                for column in frame.columns:
                    kind = column_kind(column)
                    generic = column in ("value", "reference")
                    for cell in frame[column]:
                        self._add(cell, ANY if generic else kind, found)
            self.cache[relative] = {kind: np.unique(np.abs(np.array(values, dtype=float))) for kind, values in found.items()}
        return self.cache[relative]

    def _walk(self, value: Any, key: str | None, found: dict[str, list[float]]) -> None:
        if isinstance(value, dict):
            for name, item in value.items():
                self._walk(item, str(name), found)
        elif isinstance(value, list):
            for item in value:
                self._walk(item, key, found)
        elif isinstance(value, bool) or value is None:
            return
        elif isinstance(value, (int, float)):
            self._number(value, column_kind(key or ""), found)
        else:
            self._add(str(value), column_kind(key or ""), found)

    @staticmethod
    def _number(value: Any, kind: str | None, found: dict[str, list[float]]) -> None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return
        if kind is not None and np.isfinite(number):
            found.setdefault(kind, []).append(number)

    @classmethod
    def _add(cls, cell: str, kind: str | None, found: dict[str, list[float]]) -> None:
        try:
            float(cell)
        except ValueError:
            # Free text: every number in it may back any unit ("+10% -> +20%", an anomaly's observation),
            # except the digits of dates and codes.
            for match in NUMBER.finditer(_blank(cell)):
                found.setdefault(ANY, []).append(float(match.group("num").replace(",", "")))
            return
        cls._number(cell, kind, found)


def traced(number: Number, pools: Iterable[dict[str, np.ndarray]]) -> bool:
    """True when a cited cell of a fitting kind rounds to ``number`` at the digits shown, in one of its unit's scales."""

    half = 0.5 * 10 ** (-number.decimals) * 1.001
    kinds = KINDS[number.unit] | {ANY}
    for scale in SCALES[number.unit]:
        target, tolerance = abs(number.value) * scale, half * scale + 1e-12
        for pool in pools:
            for kind in kinds:
                values = pool.get(kind)
                if values is not None and values.size:
                    position = int(np.searchsorted(values, target - tolerance, side="left"))
                    if position < values.size and values[position] <= target + tolerance:
                        return True
    return False


def _causal(text: str) -> list[str]:
    clean = re.sub(r"`[^`]*`", " ", text)
    lowered = clean.casefold()
    found: list[str] = []
    for word in HEDGES:
        if word in lowered:
            found.append(word)
    for word in CAUSAL:
        for match in re.finditer(re.escape(word), lowered):
            rest = re.search(r"[。！？!?；;]|\.(?:\s|$)", clean[match.end():])
            if not (rest and rest.group(0) in "？?"):
                found.append(word)
                break
    return found


def validated_sources(manifest: dict[str, Any], chart_manifest: dict[str, Any]) -> set[str]:
    paths = {"analysis_manifest.json", "chart_manifest.json"}
    for record in (manifest.get("outputs") or {}).values():
        if isinstance(record, dict) and record.get("path"):
            paths.add(str(record["path"]))
    for chart in chart_manifest.get("charts") or []:
        for record in [chart.get("png"), chart.get("chart_data"), *(chart.get("supplemental_data") or [])]:
            if isinstance(record, dict) and record.get("path"):
                paths.add(str(record["path"]))
    return paths


def check_brief(output_dir: str | Path, *, manifest: dict[str, Any], chart_manifest: dict[str, Any]) -> dict[str, Any]:
    """Check ``brief.md`` in a validated analysis directory; returns the full report."""

    output_dir = Path(output_dir)
    path = output_dir / BRIEF
    if not path.is_file():
        raise BriefCheckError(f"No {BRIEF} in {output_dir}; write the brief first (deliverables.md)")
    text = path.read_text(encoding="utf-8")
    blocks = parse_blocks(text)
    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    def error(check: str, line: int | None, message: str) -> None:
        errors.append({"check": check, "line": line, "message": message})

    headings = [block for block in blocks if block.kind == "heading"]
    first = next((block for block in blocks), None)
    if not first or first.kind != "heading" or first.level != 1 or sum(1 for b in headings if b.level == 1) != 1:
        error("title", first.line if first else None, f"{BRIEF} needs a level-1 heading (# Title) first, and only one")

    found_sections = [(block.line, classify(block.text)) for block in headings if block.level == 2]
    order = [key for key, _ in SECTIONS]
    seen = [key for _, key in found_sections if key]
    missing = [key for key in order if key not in seen]
    if missing:
        error("sections", None, f"missing sections: {missing} (deliverables.md: {order})")
    if seen != sorted(seen, key=order.index) or len(set(seen)) != len(seen):
        error("sections", None, f"sections must appear once each in this order: {order}; found {seen}")
    for line, key in found_sections:
        if key is None:
            warnings.append({"check": "sections", "line": line, "message": "a ## section that is none of the six"})

    charts = {str(chart["png"]["path"]) for chart in chart_manifest.get("charts") or []}
    for number, line in enumerate(text.splitlines(), start=1):
        for src in IMAGE.findall(line):
            if src.replace("\\", "/").removeprefix("./") not in charts:
                error("image", number, f"{BRIEF} embeds {src!r}, which is not a validated chart in chart_manifest.json")

    hashes = [str(manifest.get(key) or "") for key in ("scope_sha256", "plan_sha256", "study_sha256")]
    for number, line in enumerate(text.splitlines(), start=1):
        for value in HEX.findall(line):
            if re.search(r"[a-f]", value) and not any(full.startswith(value) for full in hashes if full):
                error("hash", number, f"`{value}` is not a prefix of this study's scope, plan or study hash")

    allowed = validated_sources(manifest, chart_manifest)
    index = _Index(output_dir)
    counts = {
        "numeric_tokens": 0, "parsed": 0, "traced": 0, "ambiguous": 0, "skipped": 0,
        # Backward-compatible fields kept for existing report consumers.
        "checked": 0, "untraced": 0, "unsourced": 0, "without_unit": 0,
    }
    cited: set[str] = set()
    for block in blocks:
        if block.kind == "heading":
            continue
        for label in block.labels:
            parts = [part.strip() for part in label.split("/")]
            if any(part not in ALLOWED_LABELS for part in parts):
                error("label", block.line, f"unknown evidence label [{label}]; use {' / '.join(ALLOWED_LABELS)}")
        sources = []
        for source in dict.fromkeys(block.sources):
            data = source[:-4] + ".csv" if source.endswith(".png") else source
            if data not in allowed:
                error("source", block.line, f"`{source}` is not a validated output of this analysis")
            else:
                sources.append(data)
                cited.add(data)
        found = block_numbers(block)
        measured = [(line, item) for line, item in found if item.unit]
        ambiguous = [(line, item) for line, item in found if item.unit is None and item.ambiguous_unit]
        skipped = len(found) - len(measured) - len(ambiguous)
        counts["numeric_tokens"] += len(found)
        counts["parsed"] += len(measured)
        counts["ambiguous"] += len(ambiguous)
        counts["skipped"] += skipped
        counts["without_unit"] += len(ambiguous) + skipped
        for line, item in ambiguous:
            error(
                "ambiguous_unit", line,
                f"{item.text} has an unrecognized adjacent unit {item.ambiguous_unit!r}; spell it as a supported unit",
            )
        if not measured:
            continue
        shown = ", ".join(item.text for _, item in measured[:6]) + (" ..." if len(measured) > 6 else "")
        if not block.sources:
            error("unsourced", block.line, f"numbers without a cited source: {shown}")
        if not block.labels:
            error("unlabeled", block.line, f"numbers without an evidence label: {shown}")
        pools = [index.values(source) for source in sources]
        for line, item in measured:
            counts["checked"] += 1
            if not block.sources:
                counts["unsourced"] += 1
            elif pools and traced(item, pools):
                counts["traced"] += 1
            else:
                counts["untraced"] += 1
                error("untraced", line, f"{item.text} is not in {', '.join(f'`{s}`' for s in sources) or 'any validated source'}")

    for block in blocks:
        if block.kind in ("paragraph", "item", "table"):
            for word in _causal(block.text):
                error("causal", block.line, f"causal or hedged wording {word!r}: state what the numbers show, or ask it as a question")

    outputs = manifest.get("outputs") or {}
    mention = outputs.get("mention_list")
    if isinstance(mention, dict) and mention.get("path"):
        lowered = text.casefold()
        table = pd.read_csv(output_dir / mention["path"], dtype=str, keep_default_na=False)
        for _, row in table.iterrows():
            words = [word.strip() for word in row["keywords"].split(";") if word.strip()]
            if words and not any(word.casefold() in lowered for word in words):
                error("mention", None, f"must mention {row['item']} ({row['reason']}); looked for {words}")

    bold = [b for b in blocks if b.section == "findings" and b.kind == "item" and b.marker[:1].isdigit() and b.text.startswith("**")]
    if not 4 <= len(bold) <= 6:
        warnings.append({"check": "findings", "line": None,
                         "message": f"key findings: {len(bold)} numbered bold items; deliverables.md asks for 4-6"})
    return {
        "status": "pass" if not errors else "fail",
        "brief": BRIEF,
        "brief_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "errors": errors,
        "warnings": warnings,
        "numbers": counts,
        "sections": seen,
        "sources_cited": sorted(cited),
    }


def summary(report: dict[str, Any]) -> dict[str, Any]:
    """What final_report.json keeps of a passing check."""

    return {key: report[key] for key in ("status", "brief_sha256", "numbers", "sections", "sources_cited", "warnings")}
