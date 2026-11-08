"""Study specification: how one approved scope is researched.

A scope answers "which HTS lines are this product?".  A study answers "how do
we look at it?": which years, annual or monthly detail, which origins to
highlight, which comparison years and event windows.  Keeping the two apart
lets one approved scope serve several studies, and lets origins and windows
change without touching the product definition.

Only the periods affect DataWeb queries.  Every query requests all reported
origins plus an independent World total, so focal and highlighted origins,
comparison years and event windows are analysis settings: changing them needs
no new plan and no new acquisition.

Example ``config/studies/<study_id>.toml``::

    [study]
    study_id = "laptops-2017-2026"
    title = "Where did China's U.S. laptop import share go?"
    scope_manifest = "scopes/laptops/v1/manifest.json"

    [periods]
    annual = { start = 2017, end = 2025 }
    monthly = { start = 2017, end = 2026 }

    [origins]
    focal = "China"
    highlight = ["Vietnam", "Mexico"]

    [comparison]
    base_year = 2024
    compare_year = 2025

    [[event_windows]]
    id = "baseline"
    label = "Before the 2025 tariff increases"
    start = "2024-11"
    end = "2025-01"

    [events]
    ids = ["china_additional_10pp"]
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
import tomllib
from typing import Any, Iterable

from .dataweb import FIRST_DATAWEB_YEAR
from .scope import SLUG_PATTERN, canonical_sha256


MONTH_PATTERN = re.compile(r"\d{4}-(0[1-9]|1[0-2])")
KNOWN_TABLES = {
    "study", "periods", "origins", "comparison", "event_windows", "events", "quantity",
    "timeline", "segments", "brief", "context", "drilldown",
}
# "both": first unit in the main columns, the second unit (e.g. kg) beside it.
QUANTITY_BASES = ("first", "second", "both")
MAX_HIGHLIGHTS = 6  # one fixed chart color each after the focal origin; the rest fold into "Other"


class StudyError(ValueError):
    """A study specification is invalid."""


@dataclass(frozen=True)
class YearRange:
    start: int
    end: int

    def years(self) -> tuple[int, ...]:
        return tuple(range(self.start, self.end + 1))

    def as_list(self) -> list[int]:
        return [self.start, self.end]


@dataclass(frozen=True)
class EventWindow:
    window_id: str
    label: str
    start: str
    end: str

    def as_json(self) -> dict[str, str]:
        return {"id": self.window_id, "label": self.label, "start": self.start, "end": self.end}


@dataclass(frozen=True)
class Segment:
    """A labeled group of scope codes, analyzed as one series (e.g. lines split mid-study)."""

    label: str
    codes: tuple[str, ...]

    def as_json(self) -> dict[str, Any]:
        return {"label": self.label, "codes": list(self.codes)}


@dataclass(frozen=True)
class StudySpec:
    study_id: str
    title: str
    relative_path: str
    scope_manifest: str
    annual: YearRange | None
    monthly: YearRange | None
    focal_origin: str
    highlight_origins: tuple[str, ...]
    base_year: int | None
    compare_year: int | None
    event_windows: tuple[EventWindow, ...]
    policy_event_ids: tuple[str, ...]
    policy_event_records_sha256: str | None = None
    # "first" (default): quantity in the scope's first unit (e.g. No.).
    # "second": quantity, quantity share and unit value use the second unit
    # (e.g. kg); the plan then also requests DataWeb's second quantity.
    quantity_basis: str = "first"
    # Optional features, all off by default; each enters study_sha256 only
    # when it is set, so studies written before it existed keep their hash.
    timeline_file: str | None = None
    timeline_country_file: str | None = None
    timeline_records_sha256: str | None = None
    segments: tuple[Segment, ...] = ()
    focal_split: bool = False
    hts4_context: bool = False
    context_hts6: tuple[str, ...] = ()
    drilldown_structure: str | None = None
    drilldown_structure_sha256: str | None = None

    @property
    def second_quantity(self) -> bool:
        """True when the plan must also request DataWeb's second unit of quantity."""

        return self.quantity_basis in ("second", "both")

    def periods(self) -> dict[str, list[int] | None]:
        """Return the acquisition-relevant periods used by query plans."""

        return {
            "annual": self.annual.as_list() if self.annual else None,
            "monthly": self.monthly.as_list() if self.monthly else None,
        }

    @property
    def study_sha256(self) -> str:
        """Hash every setting that can change analysis tables or charts.

        ``quantity_basis`` enters only when it is not the default, so studies
        written before it existed keep their hash.
        """

        settings: dict[str, Any] = {
            "study_id": self.study_id,
            "title": self.title,
            "scope_manifest": self.scope_manifest,
            "periods": self.periods(),
            "origins": {"focal": self.focal_origin, "highlight": list(self.highlight_origins)},
            "comparison": {"base_year": self.base_year, "compare_year": self.compare_year},
            "event_windows": [window.as_json() for window in self.event_windows],
            "policy_event_ids": list(self.policy_event_ids),
            "policy_event_records_sha256": self.policy_event_records_sha256,
        }
        if self.quantity_basis != "first":
            settings["quantity_basis"] = self.quantity_basis
        if self.timeline_file:
            settings["timeline"] = {
                "file": self.timeline_file,
                "country": self.timeline_country_file,
                "records_sha256": self.timeline_records_sha256,
            }
        if self.segments:
            settings["segments"] = [segment.as_json() for segment in self.segments]
        if self.focal_split:
            settings["brief"] = {"focal_split": True}
        if self.hts4_context:
            settings["context"] = {"hts4": True, "hts6": list(self.context_hts6)}
        if self.drilldown_structure:
            settings["drilldown"] = {
                "structure": self.drilldown_structure,
                "structure_sha256": self.drilldown_structure_sha256,
            }
        return canonical_sha256(settings)


def _table(raw: dict[str, Any], name: str, *, required: bool = True) -> dict[str, Any]:
    value = raw.get(name)
    if value is None and not required:
        return {}
    if not isinstance(value, dict):
        raise StudyError(f"[{name}] table is required")
    return value


def _slug(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not SLUG_PATTERN.fullmatch(text):
        raise StudyError(f"{name} must be a lowercase slug (letters, digits, '-' or '_'); got {text!r}")
    return text


def _integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StudyError(f"{name} must be an integer year, got {value!r}")
    return value


def _inside(path: Path, root: Path, what: str) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise StudyError(
            f"The {what} must live inside the project ({root}) so a teammate's copy can reproduce it: {path}"
        ) from exc


def _project_relative(value: Any, root: Path, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise StudyError(f"{name} is required")
    candidate = Path(text)
    if candidate.is_absolute():
        raise StudyError(f"{name} must be relative to the project root, got an absolute path: {text}")
    return _inside(root / candidate, root, name)


def _year_range(value: Any, name: str, current_year: int) -> YearRange | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"start", "end"}:
        raise StudyError(f"{name} must look like {{ start = 2017, end = 2025 }}")
    start = _integer(value["start"], f"{name}.start")
    end = _integer(value["end"], f"{name}.end")
    if start < FIRST_DATAWEB_YEAR:
        raise StudyError(f"{name}.start must be {FIRST_DATAWEB_YEAR} or later")
    if end > current_year:
        raise StudyError(f"{name}.end {end} is in the future")
    if start > end:
        raise StudyError(f"{name}.start must not exceed {name}.end")
    return YearRange(start, end)


def _string_list(value: Any, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise StudyError(f"{name} must be a list of non-empty strings")
    items = tuple(item.strip() for item in value)
    if len(set(items)) != len(items):
        raise StudyError(f"{name} contains duplicates")
    return items


def _event_windows(items: Any, monthly: YearRange | None) -> tuple[EventWindow, ...]:
    if items in (None, []):
        return ()
    if not isinstance(items, list):
        raise StudyError("[[event_windows]] must be an array of tables")
    if monthly is None:
        raise StudyError("event windows need periods.monthly")
    windows: list[EventWindow] = []
    for index, item in enumerate(items, start=1):
        where = f"event_windows[{index}]"
        if not isinstance(item, dict):
            raise StudyError(f"{where} must be a table")
        window_id = _slug(item.get("id"), f"{where}.id")
        label = str(item.get("label", "")).strip() or window_id
        start, end = str(item.get("start", "")), str(item.get("end", ""))
        for field, value in (("start", start), ("end", end)):
            if not MONTH_PATTERN.fullmatch(value):
                raise StudyError(f"{where}.{field} must be YYYY-MM, got {value!r}")
        if start > end:
            raise StudyError(f"{where}: start must not be later than end")
        if int(start[:4]) < monthly.start or int(end[:4]) > monthly.end:
            raise StudyError(f"{where} ({start}..{end}) falls outside periods.monthly {monthly.start}-{monthly.end}")
        windows.append(EventWindow(window_id, label, start, end))
    ids = [window.window_id for window in windows]
    if len(set(ids)) != len(ids):
        raise StudyError("event window ids must be unique")
    return tuple(windows)


def _segments(items: Any) -> tuple[Segment, ...]:
    if items in (None, []):
        return ()
    if not isinstance(items, list):
        raise StudyError("[[segments]] must be an array of tables")
    segments: list[Segment] = []
    for index, item in enumerate(items, start=1):
        where = f"segments[{index}]"
        if not isinstance(item, dict) or set(item) - {"label", "codes"}:
            raise StudyError(f"{where} takes only label and codes")
        label = str(item.get("label", "")).strip()
        if not label:
            raise StudyError(f"{where}.label is required")
        codes = _string_list(item.get("codes"), f"{where}.codes")
        if not codes or not all(code.isdigit() for code in codes):
            raise StudyError(f"{where}.codes must list HTS codes as digit strings")
        segments.append(Segment(label, codes))
    labels = [segment.label for segment in segments]
    if len(set(labels)) != len(labels):
        raise StudyError("segment labels must be unique")
    codes = [code for segment in segments for code in segment.codes]
    if len(set(codes)) != len(codes):
        raise StudyError("a code may belong to only one segment")
    return tuple(segments)


def _flag(table: dict[str, Any], key: str, name: str) -> bool:
    value = table.get(key, False)
    if not isinstance(value, bool):
        raise StudyError(f"{name}.{key} must be true or false")
    return value


def load_study(
    path: str | Path,
    *,
    project_root: str | Path,
    known_event_ids: Iterable[str] | None = None,
    today: date | None = None,
) -> StudySpec:
    """Load and validate a study TOML.

    ``known_event_ids`` (normally the ``event_id`` column of
    ``config/policy_events.csv``) lets unknown event references fail early.
    ``today`` is injectable so tests do not depend on the calendar.
    """

    root = Path(project_root).resolve()
    study_path = Path(path).resolve()
    relative_path = _inside(study_path, root, "study file")
    try:
        with study_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except FileNotFoundError as exc:
        raise StudyError(f"Study file not found: {study_path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise StudyError(f"Study file is not valid TOML: {study_path}: {exc}") from exc
    unknown = sorted(set(raw) - KNOWN_TABLES)
    if unknown:
        raise StudyError(f"Unknown study sections: {unknown}")
    current_year = (today or date.today()).year

    study = _table(raw, "study")
    study_id = _slug(study.get("study_id"), "study.study_id")
    title = str(study.get("title", "")).strip()
    scope_manifest = _project_relative(study.get("scope_manifest"), root, "study.scope_manifest")

    periods = _table(raw, "periods")
    extra_periods = sorted(set(periods) - {"annual", "monthly"})
    if extra_periods:
        raise StudyError(f"periods supports only annual and monthly; found {extra_periods}")
    annual = _year_range(periods.get("annual"), "periods.annual", current_year)
    monthly = _year_range(periods.get("monthly"), "periods.monthly", current_year)
    if annual is None and monthly is None:
        raise StudyError("periods needs an annual range, a monthly range, or both")

    origins = _table(raw, "origins")
    focal = str(origins.get("focal", "")).strip()
    if not focal:
        raise StudyError('origins.focal is required, for example focal = "China"')
    highlight = _string_list(origins.get("highlight", []), "origins.highlight")
    if focal in highlight:
        raise StudyError("origins.highlight must not repeat the focal origin")
    if len(highlight) > MAX_HIGHLIGHTS:
        raise StudyError(
            f"origins.highlight takes at most {MAX_HIGHLIGHTS} origins; every other origin is still analyzed "
            "and shown as 'Other reported origins'"
        )
    if any(name.casefold() == "world" for name in (focal, *highlight)):
        raise StudyError("World is always included as an independent total; do not list it as an origin")

    comparison = _table(raw, "comparison", required=False)
    base_year = comparison.get("base_year")
    compare_year = comparison.get("compare_year")
    if (base_year is None) != (compare_year is None):
        raise StudyError("comparison needs both base_year and compare_year, or neither")
    if base_year is not None:
        base_year = _integer(base_year, "comparison.base_year")
        compare_year = _integer(compare_year, "comparison.compare_year")
        if base_year >= compare_year:
            raise StudyError("comparison.base_year must be earlier than compare_year")
        if annual is None or base_year < annual.start or compare_year > annual.end:
            raise StudyError("comparison years must lie inside periods.annual")

    windows = _event_windows(raw.get("event_windows"), monthly)
    event_ids = _string_list(_table(raw, "events", required=False).get("ids", []), "events.ids")
    if known_event_ids is not None:
        missing = sorted(set(event_ids) - set(known_event_ids))
        if missing:
            raise StudyError(f"events.ids not found in config/policy_events.csv: {missing}")

    quantity = _table(raw, "quantity", required=False)
    extra_quantity = sorted(set(quantity) - {"unit"})
    if extra_quantity:
        raise StudyError(f"[quantity] supports only unit; found {extra_quantity}")
    quantity_basis = str(quantity.get("unit", "first")).strip()
    if quantity_basis not in QUANTITY_BASES:
        raise StudyError(f'quantity.unit must be "first", "second" or "both", got {quantity_basis!r}')

    timeline = _table(raw, "timeline", required=False)
    if sorted(set(timeline) - {"file", "country"}):
        raise StudyError(f"[timeline] supports only file and country; found {sorted(set(timeline) - {'file', 'country'})}")
    if timeline and "file" not in timeline:
        raise StudyError("[timeline] needs file = \"config/tariff_timelines/<scope_id>.csv\"")
    timeline_file = _project_relative(timeline["file"], root, "timeline.file") if timeline else None
    timeline_country = _project_relative(timeline["country"], root, "timeline.country") if timeline.get("country") else None

    brief = _table(raw, "brief", required=False)
    if sorted(set(brief) - {"focal_split"}):
        raise StudyError("[brief] supports only focal_split")
    context = _table(raw, "context", required=False)
    if sorted(set(context) - {"hts4", "hts6"}):
        raise StudyError("[context] supports only hts4 and hts6")
    hts4 = _flag(context, "hts4", "context")
    context_hts6 = _string_list(context.get("hts6", []), "context.hts6")
    if hts4 and not context_hts6:
        raise StudyError("[context] hts4 = true needs hts6 = [...]: every HTS6 line of the scope's headings, from the HTS structure")
    if context_hts6 and not hts4:
        raise StudyError("[context] hts6 is used only with hts4 = true")
    if any(len(code) != 6 or not code.isdigit() for code in context_hts6) or len(set(context_hts6)) != len(context_hts6):
        raise StudyError("[context] hts6 must be distinct 6-digit HTS codes")
    drilldown = _table(raw, "drilldown", required=False)
    if sorted(set(drilldown) - {"structure"}):
        raise StudyError("[drilldown] supports only structure")
    if drilldown and "structure" not in drilldown:
        raise StudyError('[drilldown] needs structure = "scopes/<id>/v<k>/history/<HTS REST search>.json"')
    structure = _project_relative(drilldown["structure"], root, "drilldown.structure") if drilldown else None

    return StudySpec(
        study_id=study_id,
        title=title,
        relative_path=relative_path,
        scope_manifest=scope_manifest,
        annual=annual,
        monthly=monthly,
        focal_origin=focal,
        highlight_origins=highlight,
        base_year=base_year,
        compare_year=compare_year,
        event_windows=windows,
        policy_event_ids=event_ids,
        quantity_basis=quantity_basis,
        timeline_file=timeline_file,
        timeline_country_file=timeline_country,
        segments=_segments(raw.get("segments")),
        focal_split=_flag(brief, "focal_split", "brief"),
        hts4_context=hts4,
        context_hts6=tuple(sorted(context_hts6)),
        drilldown_structure=structure,
    )
