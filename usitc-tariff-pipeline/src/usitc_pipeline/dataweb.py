"""USITC DataWeb acquisition with reproducible raw snapshots.

This is the *only* module that knows the DataWeb request schema.  Analysis code
must never construct API payloads, read bearer tokens, or silently refresh data.

Important measurement choice
----------------------------
The payload requests U.S. **imports for consumption** (``CONS_*`` measures), not
general imports.  ``Calculated Duties`` is later divided by customs value to
obtain a realized-duty measure; it is not a statutory tariff schedule.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
import json
import os
import random
import time
from typing import TYPE_CHECKING, Any, Iterable, Literal, Mapping

from .config import ResearchConfig
from .scope import canonical_sha256, utc_now

if TYPE_CHECKING:  # pragma: no cover - typing only
    import requests


Frequency = Literal["annual", "monthly"]
FIRST_DATAWEB_YEAR = 1989
MAX_YEARS_PER_QUERY = 10
REPORTER = "United States"
TRADE_FLOW = "imports_for_consumption"
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


def _requests():
    """Import ``requests`` only when a network request is actually made.

    Planning, payload hashing, and the scope gate need no HTTP stack, so they
    stay usable (and testable) in a minimal interpreter.
    """

    import requests

    return requests


def _transient_network_errors() -> tuple[type[Exception], ...]:
    """Errors worth retrying; used as an ``except`` expression, which runs only on failure."""

    requests = _requests()
    return (requests.Timeout, requests.ConnectionError)


class DataWebError(RuntimeError):
    """DataWeb acquisition failed: no token, query errors, truncation, or exhausted retries."""


@dataclass(frozen=True)
class QuerySpec:
    """A logical DataWeb query independent of cache filename.

    Parameters
    ----------
    hts_level:
        HTS code granularity (2, 4, 6, 8, or 10 digits).
    codes:
        Commodity codes at ``hts_level``.
    years:
        Calendar years included in the request.  Monthly requests still use a
        year list; DataWeb returns January–December columns for each year.
    frequency:
        ``annual`` or ``monthly``.
    break_out_origins:
        ``True`` returns each reported country/area; ``False`` returns a single
        aggregated World row.  The clean plan requests both so totals can be
        independently reconciled.
    include_duties:
        Include calculated duties alongside value and first-unit quantity.
    second_quantity:
        Also request the second unit of quantity (``CONS_SEC_UNIT_QUANT``, e.g.
        kg next to No.).  Off by default, so existing payloads keep their hash.
    """

    hts_level: int
    codes: tuple[str, ...]
    years: tuple[int, ...]
    frequency: Frequency
    break_out_origins: bool
    include_duties: bool = True
    second_quantity: bool = False
    # False drops the first quantity (HTS4 context map: value and duty only).
    include_quantity: bool = True

    def __post_init__(self) -> None:
        if self.hts_level not in {2, 4, 6, 8, 10}:
            raise ValueError("hts_level must be one of 2, 4, 6, 8, 10")
        if not self.codes or any(len(code) != self.hts_level or not code.isdigit() for code in self.codes):
            raise ValueError("Every HTS code must be numeric text matching hts_level")
        if not self.years or any(year < FIRST_DATAWEB_YEAR for year in self.years):
            raise ValueError(f"DataWeb study years must start in {FIRST_DATAWEB_YEAR} or later")
        if self.frequency not in {"annual", "monthly"}:
            raise ValueError("frequency must be 'annual' or 'monthly'")


@dataclass(frozen=True)
class QueryJob:
    """A query plus its human-readable cache stem."""

    cache_stem: str
    spec: QuerySpec


@dataclass(frozen=True)
class RawSnapshot:
    """Acquisition result returned without exposing the bearer token."""

    path: Path
    metadata_path: Path
    status: Literal["downloaded", "cached"]
    sha256: str
    row_count: int
    provenance: Mapping[str, Any] | None = None


def snapshot_paths(config: ResearchConfig, cache_stem: str) -> tuple[Path, Path]:
    """Return the raw snapshot path and its ``.meta.json`` sibling."""

    raw_path = config.paths.raw_dataweb / f"{cache_stem}.json"
    return raw_path, raw_path.with_suffix(".meta.json")


def cached_metadata(config: ResearchConfig, cache_stem: str, payload_hash: str) -> dict[str, Any] | None:
    """Return the snapshot metadata if a snapshot of exactly this payload is cached.

    This is the one cache-hit rule: ``fetch`` reuses a snapshot only when it
    holds, and ``plan`` reports cached jobs with it.
    """

    raw_path, meta_path = snapshot_paths(config, cache_stem)
    if not (raw_path.exists() and meta_path.exists()):
        return None
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    return metadata if metadata.get("payload_sha256") == payload_hash else None


def report_measures(
    include_duties: bool = True, second_quantity: bool = False, include_quantity: bool = True
) -> list[str]:
    """DataWeb measures requested: imports for consumption, plus calculated duties.

    ``second_quantity`` adds the second unit of quantity (its own "Second Unit
    of Quantity" table, e.g. kilograms; checked against DataWeb 2026-11-06).
    """

    measures = ["CONS_CUSTOMS_VALUE"] + (["CONS_FIR_UNIT_QUANT"] if include_quantity else [])
    if include_duties:
        measures.append("CONS_CALC_DUTY")
    if second_quantity:
        measures.append("CONS_SEC_UNIT_QUANT")
    return measures


def query_record(spec: QuerySpec) -> dict[str, Any]:
    """The query as written into snapshot metadata (``second_quantity`` only when requested)."""

    record = asdict(spec)
    if not spec.second_quantity:
        record.pop("second_quantity")
    if spec.include_quantity:
        record.pop("include_quantity")
    return record


def _column_entry(value: str) -> dict[str, Any]:
    """Return the verbose column-selection object expected by DataWeb."""

    return {
        "checked": False,
        "disabled": False,
        "hasChildren": False,
        "name": value,
        "value": value,
        "classificationSystem": "",
        "groupUUID": "",
        "items": [],
        "tradeType": "",
    }


def build_payload(spec: QuerySpec, *, max_records: int = 100_000) -> dict[str, Any]:
    """Translate a validated :class:`QuerySpec` into a DataWeb request.

    The function is deterministic and has no side effects, so a payload hash can
    identify the exact query behind a raw snapshot.
    """

    hts_label = f"HTS{spec.hts_level} & DESCRIPTION"
    country_mode = "all" if spec.break_out_origins else "world"
    column_order = ["COUNTRY", hts_label] if spec.break_out_origins else [hts_label]
    expanded = [{"name": code, "value": code} for code in spec.codes]
    return {
        "savedQueryType": "",
        "savedQueryID": "",
        "savedQueryName": (
            f"GROUP2_CLEAN_HTS{spec.hts_level}_{country_mode.upper()}_"
            f"{spec.frequency.upper()}_{min(spec.years)}_{max(spec.years)}"
        ),
        "savedQueryDesc": "Reproducible Group 2 tariff-research extract",
        "isOwner": True,
        "runMonthly": False,
        "unitConversion": "0",
        "manualConversions": [],
        "reportOptions": {"tradeType": "Import", "classificationSystem": "HTS"},
        "searchOptions": {
            "MiscGroup": {
                "districts": {
                    "aggregation": "Aggregate District",
                    "districtGroups": {"userGroups": []},
                    "districts": [],
                    "districtsExpanded": [],
                    "districtsSelectType": "all",
                },
                "importPrograms": {
                    "aggregation": None,
                    "importPrograms": [],
                    "programsSelectType": "all",
                },
                "extImportPrograms": {
                    "aggregation": "Aggregate CSC",
                    "extImportPrograms": [],
                    "extImportProgramsExpanded": [],
                    "programsSelectType": "all",
                },
                "provisionCodes": {
                    "aggregation": "Aggregate RPCODE",
                    "provisionCodesSelectType": "all",
                    "rateProvisionCodes": [],
                    "rateProvisionCodesExpanded": [],
                    "rateProvisionGroups": {"systemGroups": []},
                },
            },
            "commodities": {
                "aggregation": "Break Out Commodities",
                "codeDisplayFormat": "YES",
                "commodities": list(spec.codes),
                "commoditiesExpanded": expanded,
                "commoditiesManual": ",".join(spec.codes),
                "commodityGroups": {"systemGroups": [], "userGroups": []},
                "commoditySelectType": "list",
                "granularity": str(spec.hts_level),
                "groupGranularity": None,
                "searchGranularity": None,
                "showHTSValidDetails": spec.hts_level == 10,
            },
            "componentSettings": {
                "dataToReport": report_measures(spec.include_duties, spec.second_quantity, spec.include_quantity),
                "scale": "1",  # Keep actual dollars and reported quantity.
                "timeframeSelectType": "fullYears",
                "years": [str(year) for year in spec.years],
                "startDate": None,
                "endDate": None,
                "startMonth": None,
                "endMonth": None,
                "yearsTimeline": "Annual" if spec.frequency == "annual" else "Monthly",
            },
            "countries": {
                "aggregation": "Break Out Countries" if spec.break_out_origins else "Aggregate Countries",
                "countries": [],
                "countriesExpanded": [],
                "countriesSelectType": "all",
                "countryGroups": {"systemGroups": [], "userGroups": []},
            },
        },
        "sortingAndDataFormat": {
            "DataSort": {
                "columnOrder": column_order,
                "fullColumnOrder": [_column_entry(item) for item in column_order],
                "sortOrder": [
                    {"sortData": item, "orderBy": "asc", "year": "0"}
                    for item in column_order
                ],
            },
            "reportCustomizations": {
                "exportCombineTables": False,
                "totalRecords": str(max_records),
                "exportRawData": False,
            },
        },
        "deletedCountryUserGroups": [],
        "deletedCommodityUserGroups": [],
        "deletedDistrictUserGroups": [],
    }


def payload_sha256(spec: QuerySpec, *, max_records: int = 100_000) -> str:
    """Return the hash that identifies the exact DataWeb request for ``spec``."""

    return canonical_sha256(build_payload(spec, max_records=max_records))


def file_sha256(path: str | Path) -> str:
    """Return a streaming SHA-256 digest for manifest provenance."""

    digest = sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def response_summary(response: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize tables and detect record-limit truncation.

    DataWeb can return more than one ``row_group``.  We count all groups rather
    than only index zero, a silent-loss bug present in some earlier scripts.
    """

    tables = (response.get("dto") or {}).get("tables", [])
    details: list[dict[str, Any]] = []
    total_rows = 0
    truncated = False
    for table in tables:
        returned = sum(len(group.get("rowsNew", [])) for group in table.get("row_groups", []))
        expected_raw = table.get("totalRowSize")
        try:
            expected = int(expected_raw) if expected_raw is not None else None
        except (TypeError, ValueError):
            expected = None
        if expected is not None and returned < expected:
            truncated = True
        total_rows += returned
        details.append(
            {
                "tab": (table.get("tableInfo") or {}).get("tabName") or table.get("tab_name"),
                "returned_rows": returned,
                "reported_total_rows": expected,
            }
        )
    return {"row_count": total_rows, "truncated": truncated, "tables": details}


class DataWebClient:
    """Authenticated DataWeb client with immutable cache and retry handling."""

    def __init__(
        self,
        config: ResearchConfig,
        *,
        token: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.config = config
        self._token = token or os.environ.get("DATAWEB_TOKEN")
        self._session = session

    @property
    def session(self) -> requests.Session:
        """Create the HTTP session on first use; cached runs never need one."""

        if self._session is None:
            self._session = _requests().Session()
        return self._session

    @property
    def endpoint(self) -> str:
        base = self.config.dataweb.base_url.rstrip("/")
        return base if base.endswith("/runReport") else base + "/api/v2/report2/runReport"

    def fetch(
        self,
        job: QueryJob,
        *,
        refresh: bool = False,
        provenance: Mapping[str, Any] | None = None,
    ) -> RawSnapshot:
        """Fetch one job, reusing only a payload-matched cache.

        ``refresh=True`` never overwrites the prior immutable snapshot in place;
        it first archives that file with its short checksum in the filename.
        ``provenance`` (scope identity and hashes for scope-driven plans) is
        written into the metadata of a new download; it must never contain
        credentials.
        """

        payload = build_payload(job.spec, max_records=self.config.dataweb.max_records)
        payload_hash = canonical_sha256(payload)
        raw_path, meta_path = snapshot_paths(self.config, job.cache_stem)

        metadata = None if refresh else cached_metadata(self.config, job.cache_stem, payload_hash)
        if metadata is not None:
            data = raw_path.read_bytes()
            raw_hash = sha256(data).hexdigest()
            if metadata.get("response_sha256") != raw_hash:
                raise DataWebError(
                    f"Cached response hash no longer matches its metadata for {job.cache_stem}; "
                    "refusing to reuse a modified snapshot."
                )
            summary = response_summary(json.loads(data.decode("utf-8")))
            return RawSnapshot(
                path=raw_path,
                metadata_path=meta_path,
                status="cached",
                sha256=raw_hash,
                row_count=int(summary["row_count"]),
                provenance=metadata.get("provenance"),
            )

        if not self._token:
            raise DataWebError(
                "DATAWEB_TOKEN is required for acquisition. Analysis of existing raw snapshots does not require it."
            )

        response = self._post_with_retry(payload)
        result = response.json()
        errors = (result.get("dto") or {}).get("errors")
        if errors:
            raise DataWebError(f"DataWeb reported query errors for {job.cache_stem}: {errors}")
        summary = response_summary(result)
        if summary["truncated"]:
            raise DataWebError(
                f"DataWeb response for {job.cache_stem} appears truncated: {summary['tables']}"
            )

        raw_bytes = json.dumps(result, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        raw_hash = sha256(raw_bytes).hexdigest()
        metadata = {
            "cache_stem": job.cache_stem,
            "retrieved_at_utc": utc_now(),
            "endpoint": self.endpoint,
            "query": query_record(job.spec),
            "payload_sha256": payload_hash,
            "response_sha256": raw_hash,
            "response_summary": summary,
            # Never write Authorization headers, token values, or environment.
        }
        if provenance:
            metadata["provenance"] = dict(provenance)

        raw_path.parent.mkdir(parents=True, exist_ok=True)
        if raw_path.exists():
            old_hash = file_sha256(raw_path)[:12]
            archive = raw_path.with_name(f"{raw_path.stem}.{old_hash}.superseded.json")
            if not archive.exists():
                raw_path.replace(archive)
            if meta_path.exists():
                meta_archive = archive.with_suffix(".meta.json")
                meta_path.replace(meta_archive)

        # Write to sibling temporary files and atomically replace.  A network or
        # disk interruption therefore cannot leave a half-written JSON snapshot.
        raw_tmp = raw_path.with_suffix(".json.tmp")
        meta_tmp = meta_path.with_suffix(".json.tmp")
        raw_tmp.write_bytes(raw_bytes)
        meta_tmp.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        raw_tmp.replace(raw_path)
        meta_tmp.replace(meta_path)
        return RawSnapshot(
            raw_path, meta_path, "downloaded", raw_hash, int(summary["row_count"]), metadata.get("provenance")
        )

    def _post_with_retry(self, payload: Mapping[str, Any]) -> requests.Response:
        delays = self.config.dataweb.retry_delays or (0,)
        last_error: Exception | None = None
        for attempt, delay in enumerate(delays, start=1):
            if delay:
                # Small jitter avoids synchronized retries without changing the
                # research result; it only affects request timing.
                time.sleep(delay + random.uniform(0, min(1.0, delay / 10)))
            try:
                response = self.session.post(
                    self.endpoint,
                    headers={
                        "Content-Type": "application/json; charset=utf-8",
                        "Authorization": "Bearer " + str(self._token),
                    },
                    json=payload,
                    timeout=self.config.dataweb.timeout_seconds,
                )
                if response.status_code not in RETRYABLE_STATUS:
                    response.raise_for_status()
                    return response
                last_error = _requests().HTTPError(
                    f"Retryable DataWeb HTTP {response.status_code}", response=response
                )
            except _transient_network_errors() as exc:
                # Only timeouts and dropped connections are retried; HTTP and
                # programming errors propagate unchanged.
                last_error = exc
            if attempt == len(delays):
                break
        raise DataWebError(f"DataWeb request failed after {len(delays)} attempts") from last_error


def chunked(values: Iterable[int], size: int) -> list[tuple[int, ...]]:
    """Split ``values`` into consecutive tuples of at most ``size`` items."""

    items = tuple(values)
    return [items[index : index + size] for index in range(0, len(items), size)]
