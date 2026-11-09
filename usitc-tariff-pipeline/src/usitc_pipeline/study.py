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
YEAR_PATTERN = re.compile(r"\d{4}")
KNOWN_TABLES = {
    "study", "periods", "origins", "comparison", "event_windows", "events", "quantity",
    "timeline", "tariff_snapshot", "product_mix", "segments", "brief", "context", "industry_context", "drilldown", "decomposition", "derived", "anomalies",
}
# "both": first unit in the main columns, the second unit (e.g. kg) beside it.
QUANTITY_BASES = ("first", "second", "both")
MAX_HIGHLIGHTS = 6  # one fixed chart color each after the focal origin; the rest fold into "Other"
# [[derived]]: what can be measured over a window, and how its periods combine.
DERIVED_MEASURES = ("value", "quantity", "duty", "share", "quantity_share", "unit_value", "realized_duty")
SECOND_UNIT_MEASURES = ("quantity_2", "quantity_2_share", "unit_value_2")
DERIVED_STATS = ("total", "mean", "min", "max")
DERIVED_KEYS = ("id", "label", "origin", "measure", "stat", "window", "base")


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
class Derived:
    """One ``[[derived]]`` entry: a number the brief needs that no standard table holds.

    Every combination of ``origins``, ``measures`` and ``stats`` becomes one row
    of ``tables/calculations.csv``, over ``window`` and, when given, ``base``.
    """

    derived_id: str
    label: str
    origins: tuple[str, ...]
    measures: tuple[str, ...]
    stats: tuple[str, ...]
    frequency: str
    window: tuple[str, str]
    base: tuple[str, str] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": self.derived_id,
            "label": self.label,
            "origins": list(self.origins),
            "measures": list(self.measures),
            "stats": list(self.stats),
            "window": list(self.window),
            "base": list(self.base) if self.base else None,
        }


@dataclass(frozen=True)
class TariffSnapshotSpec:
    """Explicit customs-entry context for a non-binding tariff snapshot."""

    entry_date: str
    hts_release_id: str
    hts_release_date: str
    origins: tuple[str, ...]
    rate_column: str = "general"
    preference_claim: str = "none"
    entry_type: str = "consumption"
    chapter_98: bool = False
    quota_or_trq: bool = False
    ad_cvd: bool = False
    firm_specific_rate: bool = False
    requires_binding_classification: bool = False

    def as_json(self) -> dict[str, Any]:
        return {
            "entry_date": self.entry_date,
            "hts_release_id": self.hts_release_id,
            "hts_release_date": self.hts_release_date,
            "origins": list(self.origins),
            "rate_column": self.rate_column,
            "preference_claim": self.preference_claim,
            "entry_type": self.entry_type,
            "chapter_98": self.chapter_98,
            "quota_or_trq": self.quota_or_trq,
            "ad_cvd": self.ad_cvd,
            "firm_specific_rate": self.firm_specific_rate,
            "requires_binding_classification": self.requires_binding_classification,
        }


@dataclass(frozen=True)
class ProductMixSpec:
    """One full-year HTS10 product-mix acquisition under an HTS6 scope."""

    period: int
    # Optional independent archived structure.  ``None`` preserves the legacy
    # behavior: product mix reuses the study's [drilldown] inputs.
    structure: str | None = None
    release_metadata: str | None = None
    codes: tuple[str, ...] = ()
    hts_release_id: str = ""
    hts_release_date: str = ""
    structure_sha256: str = ""
    release_metadata_sha256: str = ""

    def as_json(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "period": self.period,
            "codes": list(self.codes),
            "hts_release_id": self.hts_release_id,
            "hts_release_date": self.hts_release_date,
            "structure_sha256": self.structure_sha256,
        }
        # Do not add new keys to legacy period-only studies.  Their fallback
        # paths and hashes are already bound through the [drilldown] identity.
        if self.structure is not None:
            result.update(
                {
                    "structure": self.structure,
                    "release_metadata": self.release_metadata,
                    "release_metadata_sha256": self.release_metadata_sha256,
                }
            )
        return result


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
    tariff_snapshot: TariffSnapshotSpec | None = None
    segments: tuple[Segment, ...] = ()
    focal_split: bool = False
    hts4_context: bool = False
    context_hts6: tuple[str, ...] = ()
    drilldown_structure: str | None = None
    drilldown_structure_sha256: str | None = None
    drilldown_release_metadata: str | None = None
    drilldown_release_metadata_sha256: str | None = None
    product_mix: ProductMixSpec | None = None
    decomposition: bool = False
    decomposition_windows: tuple[tuple[str, str], ...] = ()
    decomposition_mix: bool = False
    derived: tuple[Derived, ...] = ()
    anomalies: bool = False
    brief_candidates: bool = False
    # External industry-cycle context is never fetched by the pipeline.  The
    # setting only lets the deterministic next-step menu record that the user
    # opted into a light, separately sourced web-research step.
    industry_context_mode: str = "off"

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
        if self.tariff_snapshot:
            settings["tariff_snapshot"] = self.tariff_snapshot.as_json()
        if self.segments:
            settings["segments"] = [segment.as_json() for segment in self.segments]
        brief: dict[str, bool] = {}
        if self.focal_split:
            brief["focal_split"] = True
        if self.brief_candidates:
            brief["candidates"] = True
        if brief:
            settings["brief"] = brief
        if self.hts4_context:
            settings["context"] = {"hts4": True, "hts6": list(self.context_hts6)}
        if self.drilldown_structure:
            settings["drilldown"] = {
                "structure": self.drilldown_structure,
                "structure_sha256": self.drilldown_structure_sha256,
            }
            if self.drilldown_release_metadata:
                settings["drilldown"]["release_metadata"] = self.drilldown_release_metadata
                settings["drilldown"]["release_metadata_sha256"] = self.drilldown_release_metadata_sha256
        if self.product_mix:
            settings["product_mix"] = self.product_mix.as_json()
        if self.decomposition:
            decomposition: dict[str, Any] = {"windows": [list(window) for window in self.decomposition_windows]}
            if self.decomposition_mix:
                decomposition["mix"] = True
            settings["decomposition"] = decomposition
        if self.derived:
            settings["derived"] = [item.as_json() for item in self.derived]
        if self.anomalies:
            settings["anomalies"] = {"enabled": True}
        if self.industry_context_mode != "off":
            settings["industry_context"] = {"mode": self.industry_context_mode}
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


def _tariff_snapshot(
    table: dict[str, Any],
    *,
    focal_origin: str,
    structure: str | None,
    release_metadata: str | None,
    today: date,
) -> TariffSnapshotSpec | None:
    keys = {
        "enabled", "entry_date", "hts_release_id", "hts_release_date", "origins", "rate_column", "preference_claim", "entry_type",
        "chapter_98", "quota_or_trq", "ad_cvd", "firm_specific_rate", "requires_binding_classification",
    }
    unknown = sorted(set(table) - keys)
    if unknown:
        raise StudyError(f"[tariff_snapshot] has unknown fields: {unknown}")
    if not table:
        return None
    enabled_value = table.get("enabled", True)
    if not isinstance(enabled_value, bool):
        raise StudyError("tariff_snapshot.enabled must be true or false")
    if not enabled_value:
        if set(table) - {"enabled"}:
            raise StudyError("[tariff_snapshot] enabled = false cannot include snapshot settings")
        return None
    if not structure:
        raise StudyError("[tariff_snapshot] needs [drilldown] structure so HTS8/HTS10 product lines and base rates are explicit")
    if not release_metadata:
        raise StudyError(
            "[tariff_snapshot] needs [drilldown] release_metadata with the HTS release id, effective date and structure SHA-256"
        )
    entry_date = str(table.get("entry_date") or "").strip()
    try:
        parsed_date = date.fromisoformat(entry_date)
    except ValueError as exc:
        raise StudyError("tariff_snapshot.entry_date must be an explicit YYYY-MM-DD") from exc
    if parsed_date > today:
        raise StudyError("tariff_snapshot.entry_date cannot be in the future")
    hts_release_id = str(table.get("hts_release_id") or "").strip()
    if not hts_release_id:
        raise StudyError("tariff_snapshot.hts_release_id must be explicit and non-empty")
    hts_release_date = str(table.get("hts_release_date") or "").strip()
    try:
        parsed_release_date = date.fromisoformat(hts_release_date)
    except ValueError as exc:
        raise StudyError("tariff_snapshot.hts_release_date must be an explicit YYYY-MM-DD") from exc
    if parsed_release_date > parsed_date:
        raise StudyError("tariff_snapshot.hts_release_date cannot be later than entry_date")
    origins = _string_list(table.get("origins", [focal_origin]), "tariff_snapshot.origins")
    if not origins or any(origin.casefold() == "world" for origin in origins):
        raise StudyError("tariff_snapshot.origins must name at least one actual country or area, not World")
    strings: dict[str, str] = {}
    for key, default in (
        ("rate_column", "general"),
        ("preference_claim", "none"),
        ("entry_type", "consumption"),
    ):
        value = str(table.get(key, default)).strip()
        if not value:
            raise StudyError(f"tariff_snapshot.{key} must be non-empty")
        strings[key] = value
    return TariffSnapshotSpec(
        entry_date=entry_date,
        hts_release_id=hts_release_id,
        hts_release_date=hts_release_date,
        origins=origins,
        **strings,
        chapter_98=_flag(table, "chapter_98", "tariff_snapshot"),
        quota_or_trq=_flag(table, "quota_or_trq", "tariff_snapshot"),
        ad_cvd=_flag(table, "ad_cvd", "tariff_snapshot"),
        firm_specific_rate=_flag(table, "firm_specific_rate", "tariff_snapshot"),
        requires_binding_classification=_flag(table, "requires_binding_classification", "tariff_snapshot"),
    )


def _decomposition(table: dict[str, Any], monthly: YearRange | None) -> tuple[bool, tuple[tuple[str, str], ...]]:
    """``[decomposition]``: enabled, plus optional month pairs to compare (e.g. the turns in a share path)."""

    if sorted(set(table) - {"enabled", "windows", "mix"}):
        raise StudyError("[decomposition] supports only enabled, windows and mix")
    enabled = _flag(table, "enabled", "decomposition")
    if _flag(table, "mix", "decomposition") and not enabled:
        raise StudyError("decomposition.mix is used only with enabled = true")
    items = table.get("windows", [])
    if not isinstance(items, list):
        raise StudyError('decomposition.windows must be a list of ["YYYY-MM", "YYYY-MM"] pairs')
    if items and not enabled:
        raise StudyError("decomposition.windows is used only with enabled = true")
    windows: list[tuple[str, str]] = []
    for index, item in enumerate(items):
        where = f"decomposition.windows[{index}]"
        if not (isinstance(item, list) and len(item) == 2 and all(isinstance(m, str) and MONTH_PATTERN.fullmatch(m) for m in item)):
            raise StudyError(f'{where} must be ["YYYY-MM", "YYYY-MM"]')
        start, end = item
        if start >= end:
            raise StudyError(f"{where}: the first month must come before the second")
        if monthly is None or int(start[:4]) < monthly.start or int(end[:4]) > monthly.end:
            raise StudyError(f"{where} must lie inside [periods] monthly")
        windows.append((start, end))
    if len(set(windows)) != len(windows):
        raise StudyError("decomposition.windows must be distinct")
    return enabled, tuple(windows)


def _choices(value: Any, name: str, *, allowed: tuple[str, ...] | None = None, default: str | None = None) -> tuple[str, ...]:
    """A string or a list of strings; ``default`` when the key is absent."""

    if value is None:
        if default is None:
            raise StudyError(f"{name} is required")
        return (default,)
    items = _string_list([value] if isinstance(value, str) else value, name)
    if not items:
        raise StudyError(f"{name} must not be empty")
    if allowed is not None and sorted(set(items) - set(allowed)):
        raise StudyError(f"{name} must be one of {list(allowed)}; got {sorted(set(items) - set(allowed))}")
    return items


def _span(value: Any, name: str, annual: YearRange | None, monthly: YearRange | None) -> tuple[str, str, str]:
    """``"YYYY-MM"``, ``"YYYY"`` or ``[first, last]`` of one kind -> (frequency, first, last)."""

    items = [value, value] if isinstance(value, str) else value
    if not (isinstance(items, list) and len(items) == 2 and all(isinstance(item, str) for item in items)):
        raise StudyError(f'{name} must be "YYYY-MM", "YYYY" or ["first", "last"]')
    first, last = items
    if MONTH_PATTERN.fullmatch(first) and MONTH_PATTERN.fullmatch(last):
        frequency, span = "monthly", monthly
    elif YEAR_PATTERN.fullmatch(first) and YEAR_PATTERN.fullmatch(last):
        frequency, span = "annual", annual
    else:
        raise StudyError(f"{name}: give months (YYYY-MM) or years (YYYY), not a mix; got {items}")
    if first > last:
        raise StudyError(f"{name}: {first} comes after {last}")
    if span is None or int(first[:4]) < span.start or int(last[:4]) > span.end:
        raise StudyError(f"{name} ({first}..{last}) must lie inside [periods] {frequency}")
    return frequency, first, last


def _derived(items: Any, annual: YearRange | None, monthly: YearRange | None, quantity_basis: str) -> tuple[Derived, ...]:
    """``[[derived]]``: numbers for the brief that the standard tables do not hold."""

    if items in (None, []):
        return ()
    if not isinstance(items, list):
        raise StudyError("[[derived]] must be an array of tables")
    result: list[Derived] = []
    for index, item in enumerate(items, start=1):
        where = f"derived[{index}]"
        if not isinstance(item, dict) or sorted(set(item) - set(DERIVED_KEYS)):
            raise StudyError(f"{where} takes only {list(DERIVED_KEYS)}")
        derived_id = _slug(item.get("id"), f"{where}.id")
        label = item.get("label", "")
        if not isinstance(label, str):
            raise StudyError(f"{where}.label must be a string")
        measures = _choices(item.get("measure"), f"{where}.measure", allowed=DERIVED_MEASURES + SECOND_UNIT_MEASURES)
        if quantity_basis != "both" and set(measures) & set(SECOND_UNIT_MEASURES):
            raise StudyError(f'{where}: {list(SECOND_UNIT_MEASURES)} need [quantity] unit = "both"')
        stats = _choices(item.get("stat"), f"{where}.stat", allowed=DERIVED_STATS, default="total")
        origins = _choices(item.get("origin"), f"{where}.origin")
        if "window" not in item:
            raise StudyError(f"{where}.window is required")
        frequency, *window = _span(item["window"], f"{where}.window", annual, monthly)
        base = None
        if "base" in item:
            base_frequency, *base = _span(item["base"], f"{where}.base", annual, monthly)
            if base_frequency != frequency:
                raise StudyError(f"{where}: window and base must both be months or both be years")
        result.append(
            Derived(derived_id, label.strip(), origins, measures, stats, frequency, tuple(window), tuple(base) if base else None)
        )
    ids = [item.derived_id for item in result]
    if len(set(ids)) != len(ids):
        raise StudyError("derived ids must be unique")
    return tuple(result)


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
    if sorted(set(brief) - {"focal_split", "candidates"}):
        raise StudyError("[brief] supports only focal_split and candidates")
    anomalies = _table(raw, "anomalies", required=False)
    if sorted(set(anomalies) - {"enabled"}):
        raise StudyError("[anomalies] supports only enabled")
    industry_context = _table(raw, "industry_context", required=False)
    if sorted(set(industry_context) - {"mode"}):
        raise StudyError("[industry_context] supports only mode")
    industry_context_mode = str(industry_context.get("mode", "off")).strip().lower()
    if industry_context_mode not in {"off", "light"}:
        raise StudyError('industry_context.mode must be "off" or "light"')
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
    if sorted(set(drilldown) - {"structure", "release_metadata"}):
        raise StudyError("[drilldown] supports only structure and release_metadata")
    if drilldown and "structure" not in drilldown:
        raise StudyError('[drilldown] needs structure = "scopes/<id>/v<k>/history/<HTS REST search>.json"')
    structure = _project_relative(drilldown["structure"], root, "drilldown.structure") if drilldown else None
    release_metadata = (
        _project_relative(drilldown["release_metadata"], root, "drilldown.release_metadata")
        if drilldown.get("release_metadata") else None
    )
    product_mix_table = _table(raw, "product_mix", required=False)
    if sorted(set(product_mix_table) - {"period", "structure", "release_metadata"}):
        raise StudyError("[product_mix] supports only period, structure and release_metadata")
    product_mix: ProductMixSpec | None = None
    if product_mix_table:
        if "period" not in product_mix_table:
            raise StudyError("[product_mix] needs an explicit full-year period")
        product_mix_period = _integer(product_mix_table["period"], "product_mix.period")
        if annual is None or not (annual.start <= product_mix_period <= annual.end):
            raise StudyError("product_mix.period must lie inside periods.annual")
        if not structure:
            raise StudyError(
                "[product_mix] needs [drilldown] structure for the product hierarchy output"
            )
        product_mix_structure = (
            _project_relative(product_mix_table["structure"], root, "product_mix.structure")
            if product_mix_table.get("structure") else None
        )
        product_mix_release_metadata = (
            _project_relative(
                product_mix_table["release_metadata"], root, "product_mix.release_metadata"
            )
            if product_mix_table.get("release_metadata") else None
        )
        if (product_mix_structure is None) != (product_mix_release_metadata is None):
            raise StudyError(
                "[product_mix] structure and release_metadata must be supplied together"
            )
        if product_mix_structure is None and (not structure or not release_metadata):
            raise StudyError(
                "[product_mix] needs its own structure and release_metadata, or both fields in [drilldown]"
            )
        product_mix = ProductMixSpec(
            period=product_mix_period,
            structure=product_mix_structure,
            release_metadata=product_mix_release_metadata,
        )
    snapshot = _tariff_snapshot(
        _table(raw, "tariff_snapshot", required=False),
        focal_origin=focal,
        structure=structure,
        release_metadata=release_metadata,
        today=today or date.today(),
    )
    decomposition_table = _table(raw, "decomposition", required=False)
    decomposition, decomposition_windows = _decomposition(decomposition_table, monthly)

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
        tariff_snapshot=snapshot,
        segments=_segments(raw.get("segments")),
        focal_split=_flag(brief, "focal_split", "brief"),
        hts4_context=hts4,
        context_hts6=tuple(sorted(context_hts6)),
        drilldown_structure=structure,
        drilldown_release_metadata=release_metadata,
        product_mix=product_mix,
        decomposition=decomposition,
        decomposition_windows=decomposition_windows,
        decomposition_mix=_flag(decomposition_table, "mix", "decomposition"),
        derived=_derived(raw.get("derived"), annual, monthly, quantity_basis),
        anomalies=_flag(anomalies, "enabled", "anomalies"),
        brief_candidates=_flag(brief, "candidates", "brief"),
        industry_context_mode=industry_context_mode,
    )
