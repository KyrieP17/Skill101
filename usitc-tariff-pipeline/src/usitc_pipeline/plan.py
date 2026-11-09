"""Query plans and the gated, scope-driven DataWeb acquisition.

``build_query_plan`` turns an approved scope plus a study's periods into an
explicit list of DataWeb queries.  ``acquire_plan`` executes that list only
after re-verifying, at run time, that the frozen scope CSV is unchanged, that
the study still asks for the same periods, and that rebuilding the plan from
those approved inputs gives exactly the same queries.  A hand-edited plan, an
edited scope file, or a year outside a code's validity window stops the run
before any network request.

Plans contain only acquisition settings.  Origins, comparison years, and
event windows are analysis settings and are deliberately excluded, so
changing them never invalidates downloaded snapshots.

Paths inside a plan are relative to the project root, so two analysts who
plan the same approved scope and study get the same ``plan_sha256``.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
import hashlib
from itertools import groupby
import json
from pathlib import Path
import re
from typing import Any, Iterable

from . import drilldown as drilldown_module
from . import tariff_snapshot_adapter as tariff_snapshot_adapter_module
from .config import ResearchConfig, read_csv_records
from .dataweb import (
    MAX_YEARS_PER_QUERY,
    REPORTER,
    TRADE_FLOW,
    DataWebClient,
    QueryJob,
    QuerySpec,
    cached_metadata,
    chunked,
    file_sha256,
    payload_sha256,
    report_measures,
)
from .scope import ApprovedScope, ScopeGateError, canonical_sha256, utc_now, verify_scope_manifest, write_json_atomic
from .study import ProductMixSpec, StudyError, StudySpec, load_study
from .timeline import TimelineError, read_timeline


PLAN_SCHEMA_VERSION = 2
UNHASHED_PLAN_KEYS = frozenset({"plan_sha256", "created_at_utc"})


def _relative(path: str | Path, root: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return resolved.as_posix()


def policy_event_ids(config: ResearchConfig) -> set[str]:
    """Return event ids declared in ``config/policy_events.csv``."""

    return {str(row.get("event_id", "")).strip() for row in read_csv_records(config.policy_events_path)} - {""}


def load_project_study(config: ResearchConfig, path: str | Path, *, today: date | None = None) -> StudySpec:
    """Load one of this project's studies, checking its event ids against ``policy_events.csv``."""

    event_rows = read_csv_records(config.policy_events_path)
    known_ids = {str(row.get("event_id", "")).strip() for row in event_rows} - {""}
    study = load_study(path, project_root=config.paths.root, known_event_ids=known_ids, today=today)
    selected = sorted(
        (row for row in event_rows if str(row.get("event_id", "")).strip() in study.policy_event_ids),
        key=lambda row: str(row.get("event_id", "")),
    )
    study = replace(study, policy_event_records_sha256=canonical_sha256(selected))
    if study.timeline_file:
        study = replace(study, timeline_records_sha256=canonical_sha256(timeline_records(config, study)))
    if study.drilldown_structure:
        study = replace(study, drilldown_structure_sha256=drilldown_structure_sha256(config, study))
    if study.drilldown_release_metadata:
        study = replace(
            study,
            drilldown_release_metadata_sha256=drilldown_release_metadata_sha256(config, study),
        )
    if study.product_mix:
        study = replace(study, product_mix=_resolve_product_mix(config, study))
    return study


def _digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _resolve_product_mix(config: ResearchConfig, study: StudySpec) -> ProductMixSpec:
    """Bind one HTS10 product-mix query to an approved HTS6 scope and HTS release."""

    requested = study.product_mix
    if requested is None:  # pragma: no cover - guarded by the caller
        raise StudyError("product_mix is not configured")
    scope = verify_scope_manifest(config.paths.root / study.scope_manifest)
    if scope.hts_level != 6:
        raise StudyError("[product_mix] is only valid for an HTS6 industry scope")
    structure_relative = requested.structure or study.drilldown_structure
    metadata_relative = requested.release_metadata or study.drilldown_release_metadata
    if not structure_relative or not metadata_relative:
        raise StudyError("[product_mix] needs a structure and release metadata")
    structure_path = config.paths.root / structure_relative
    metadata_path = config.paths.root / metadata_relative
    try:
        raw_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise StudyError(f"Cannot read product-mix HTS release metadata {metadata_path}: {exc}") from exc
    if not isinstance(raw_metadata, dict):
        raise StudyError("Product-mix HTS release metadata must be a JSON object")
    try:
        metadata = tariff_snapshot_adapter_module.load_structure_release_metadata(
            metadata_path,
            structure_path=structure_path,
            expected_release_id=str(raw_metadata.get("release_id") or ""),
            expected_effective_date=str(raw_metadata.get("effective_date") or ""),
        )
        structure = drilldown_module.read_structure(structure_path)
    except (
        tariff_snapshot_adapter_module.TariffSnapshotAdapterError,
        drilldown_module.DrilldownError,
    ) as exc:
        raise StudyError(str(exc)) from exc
    release_day = date.fromisoformat(str(metadata["effective_date"]))
    if release_day.year != requested.period:
        raise StudyError(
            f"product_mix.period {requested.period} needs an HTS structure effective in that year; "
            f"the bound release is effective {release_day.isoformat()}"
        )
    period_start, period_end = date(requested.period, 1, 1), date(requested.period, 12, 31)
    active_lines = [
        line for line in scope.included
        if line.valid_from <= period_end and line.valid_to >= period_start
    ]
    if not active_lines:
        raise StudyError(
            f"product_mix.period {requested.period} is outside every approved scope line's validity window"
        )
    parents = sorted({line.hts_code for line in active_lines})
    codes = sorted(
        {
            code
            for row in structure
            if len(code := _digits(row.get("htsno") or row.get("hts_code"))) == 10
            and any(code.startswith(parent) for parent in parents)
        }
    )
    missing = [parent for parent in parents if not any(code.startswith(parent) for code in codes)]
    if missing:
        raise StudyError(
            "The bound HTS structure has no HTS10 statistical categories under approved HTS6 line(s): "
            + ", ".join(missing)
        )
    return ProductMixSpec(
        period=requested.period,
        structure=requested.structure,
        release_metadata=requested.release_metadata,
        codes=tuple(codes),
        hts_release_id=str(metadata["release_id"]),
        hts_release_date=str(metadata["effective_date"]),
        structure_sha256=str(metadata["source_sha256"]),
        release_metadata_sha256=file_sha256(metadata_path),
    )


def drilldown_structure_sha256(config: ResearchConfig, study: StudySpec) -> str:
    """SHA-256 of the cached HTS structure file the drill-down menu reads."""

    path = config.paths.root / str(study.drilldown_structure)
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise StudyError(f"Cannot read drilldown.structure {study.drilldown_structure}: {exc}") from exc


def drilldown_release_metadata_sha256(config: ResearchConfig, study: StudySpec) -> str:
    """SHA-256 of the machine-readable release metadata bound to a snapshot."""

    path = config.paths.root / str(study.drilldown_release_metadata)
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise StudyError(
            f"Cannot read drilldown.release_metadata {study.drilldown_release_metadata}: {exc}"
        ) from exc


def timeline_records(config: ResearchConfig, study: StudySpec) -> dict[str, list[dict[str, str]]]:
    """The study's validated timeline rows by layer (empty when it names no timeline)."""

    root = config.paths.root
    records: dict[str, list[dict[str, str]]] = {}
    try:
        if study.timeline_file:
            records["product"] = read_timeline(root / study.timeline_file, layer="product")
        if study.timeline_country_file:
            records["country"] = read_timeline(root / study.timeline_country_file, layer="country")
    except TimelineError as exc:
        raise StudyError(str(exc)) from exc
    return records


def check_segments(scope: ApprovedScope, study: StudySpec) -> None:
    """Segment codes must be included codes of the approved scope."""

    included = {line.hts_code for line in scope.included}
    for segment in study.segments:
        outside = sorted(set(segment.codes) - included)
        if outside:
            raise ScopeGateError(
                f"Segment {segment.label!r} lists codes that are not included in the approved scope: {outside}"
            )


def _plan_digest(plan: dict[str, Any]) -> str:
    return canonical_sha256({key: value for key, value in plan.items() if key not in UNHASHED_PLAN_KEYS})


def _spec(job: dict[str, Any]) -> QuerySpec:
    return QuerySpec(
        hts_level=int(job["hts_level"]),
        codes=tuple(job["codes"]),
        years=tuple(int(year) for year in job["years"]),
        frequency=job["frequency"],
        break_out_origins=bool(job["break_out_origins"]),
        second_quantity=bool(job.get("second_quantity", False)),
        include_quantity=bool(job.get("include_quantity", True)),
    )


def _annual_chunks(scope: ApprovedScope, years: Iterable[int]) -> list[tuple[tuple[int, ...], tuple[str, ...]]]:
    """Split consecutive years into queries that each use one approved code set.

    A code change (for example a split in 2022) starts a new query, so each
    query only asks for codes valid in the years it covers.  Long runs are cut
    every ``MAX_YEARS_PER_QUERY`` years.
    """

    return [
        (chunk, codes)
        for codes, run in groupby(years, key=scope.codes_for_year)
        for chunk in chunked(run, MAX_YEARS_PER_QUERY)
    ]


def _job(
    scope: ApprovedScope,
    frequency: str,
    years: tuple[int, ...],
    codes: tuple[str, ...],
    break_out_origins: bool,
    max_records: int,
    second_quantity: bool = False,
) -> dict[str, Any]:
    spec = QuerySpec(
        hts_level=scope.hts_level,
        codes=codes,
        years=years,
        frequency=frequency,  # type: ignore[arg-type]
        break_out_origins=break_out_origins,
        second_quantity=second_quantity,
    )
    digest = payload_sha256(spec, max_records=max_records)
    label = "all" if break_out_origins else "world"
    extra = {"second_quantity": True} if second_quantity else {}
    return {
        **extra,
        "cache_stem": (
            f"scopes/{scope.scope_id}/v{scope.scope_version}/"
            f"hts{scope.hts_level}_{frequency}_{label}_{years[0]}_{years[-1]}_{digest[:10]}"
        ),
        "frequency": frequency,
        "years": list(years),
        "hts_level": scope.hts_level,
        "codes": list(codes),
        "break_out_origins": break_out_origins,
        "payload_sha256": digest,
    }


def _product_mix_job(
    scope: ApprovedScope,
    product_mix: ProductMixSpec,
    break_out_origins: bool,
    max_records: int,
) -> dict[str, Any]:
    """One same-year HTS10 query used only by the product hierarchy matrix."""

    spec = QuerySpec(
        hts_level=10,
        codes=product_mix.codes,
        years=(product_mix.period,),
        frequency="annual",
        break_out_origins=break_out_origins,
    )
    digest = payload_sha256(spec, max_records=max_records)
    label = "all" if break_out_origins else "world"
    return {
        "purpose": "product_mix",
        "cache_stem": (
            f"scopes/{scope.scope_id}/v{scope.scope_version}/product_mix/"
            f"hts10_annual_{label}_{product_mix.period}_{product_mix.period}_{digest[:10]}"
        ),
        "frequency": "annual",
        "years": [product_mix.period],
        "hts_level": 10,
        "codes": list(product_mix.codes),
        "break_out_origins": break_out_origins,
        "payload_sha256": digest,
    }


def _check_coverage(scope: ApprovedScope, study: StudySpec) -> None:
    problems: list[str] = []
    for name, period in (("annual", study.annual), ("monthly", study.monthly)):
        if period is None:
            continue
        gaps = scope.uncovered(date(period.start, 1, 1), date(period.end, 12, 31))
        if gaps:
            listed = ", ".join(f"{start.isoformat()}..{end.isoformat()}" for start, end in gaps)
            problems.append(f"{name} {period.start}-{period.end} has no approved code for {listed}")
    if problems:
        raise ScopeGateError(
            "Requested years fall outside the approved scope's validity windows: "
            + "; ".join(problems)
            + ". Add reviewed predecessor or successor lines in a new scope version, or shorten the study period."
        )


def build_query_plan(scope: ApprovedScope, study: StudySpec, *, max_records: int) -> dict[str, Any]:
    """Build the deterministic DataWeb query plan for one scope and study.

    Every period gets an origin breakout query and an independent World
    query, so origin totals can be reconciled instead of defining World as
    whatever rows were downloaded.
    """

    _check_coverage(scope, study)
    check_segments(scope, study)
    second = study.second_quantity
    if second and not scope.second_unit:
        raise ScopeGateError(
            "The study asks for quantity in the second unit, but the approved scope records no single second unit "
            f"for all included lines (second units: {list(scope.second_units) or 'none'})."
        )
    periods: list[tuple[str, tuple[int, ...], tuple[str, ...]]] = []
    if study.annual:
        periods += [("annual", years, codes) for years, codes in _annual_chunks(scope, study.annual.years())]
    if study.monthly:
        periods += [("monthly", (year,), scope.codes_for_year(year)) for year in study.monthly.years()]
    jobs = [
        _job(scope, frequency, years, codes, breakout, max_records, second_quantity=second)
        for frequency, years, codes in periods
        for breakout in (True, False)
    ]
    if study.product_mix:
        if scope.hts_level != 6 or not study.product_mix.codes:
            raise ScopeGateError(
                "An HTS10 product-mix plan needs an HTS6 approved scope and release-bound HTS10 codes"
            )
        jobs += [
            _product_mix_job(scope, study.product_mix, breakout, max_records)
            for breakout in (True, False)
        ]

    content: dict[str, Any] = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "measurement": {
            "reporter": REPORTER,
            "trade_flow": TRADE_FLOW,
            "measures": report_measures(second_quantity=second),
            "origins": "all reported origins, plus an independent World total query",
            "max_records": max_records,
        },
        "scope": {
            "manifest_path": study.scope_manifest,
            "scope_id": scope.scope_id,
            "scope_version": scope.scope_version,
            "product_id": scope.product_id,
            "product_name": scope.product_name,
            "scope_sha256": scope.scope_sha256,
            "hts_level": scope.hts_level,
            "quantity_unit": scope.quantity_unit,
            "quantity_comparable": scope.quantity_comparable,
            "continuity_verified": scope.continuity_verified,
        },
        "study": {
            "study_id": study.study_id,
            "path": study.relative_path,
            "periods": study.periods(),
        },
        "jobs": jobs,
    }
    if study.hts4_context:
        content["context"] = context_plan(scope, study, max_records=max_records)
    if study.product_mix:
        content["product_mix"] = study.product_mix.as_json()
    return {"plan_sha256": _plan_digest(content), "created_at_utc": utc_now(), **content}


def context_plan(scope: ApprovedScope, study: StudySpec, *, max_records: int) -> dict[str, Any]:
    """HTS4 context queries: each heading broken out by HTS6, value and calculated duty only.

    They reach outside the approved scope, so ``acquire`` never runs them: the
    user approves them separately (decisions.md, 待确认).
    """

    headings = sorted({line.hts_code[:4] for line in scope.included})
    codes = tuple(study.context_hts6)
    missing = sorted({line.hts_code[:6] for line in scope.included} - set(codes))
    outside = sorted(code for code in codes if code[:4] not in headings)
    if missing or outside:
        raise ScopeGateError(
            "[context] hts6 must list every HTS6 of the scope and only codes under its headings "
            f"{headings}: missing {missing}, outside {outside}"
        )
    periods: list[tuple[str, tuple[int, ...]]] = []
    if study.annual:
        periods += [("annual", tuple(chunk)) for chunk in chunked(study.annual.years(), MAX_YEARS_PER_QUERY)]
    if study.monthly:
        periods += [("monthly", (year,)) for year in study.monthly.years()]
    jobs = []
    for frequency, years in periods:
        for breakout in (True, False):
            spec = QuerySpec(hts_level=6, codes=codes, years=years, frequency=frequency,  # type: ignore[arg-type]
                             break_out_origins=breakout, include_quantity=False)
            digest = payload_sha256(spec, max_records=max_records)
            jobs.append({
                "cache_stem": (
                    f"scopes/{scope.scope_id}/v{scope.scope_version}/context/"
                    f"hts6_{frequency}_{'all' if breakout else 'world'}_{years[0]}_{years[-1]}_{digest[:10]}"
                ),
                "frequency": frequency,
                "years": list(years),
                "hts_level": 6,
                "codes": list(codes),
                "break_out_origins": breakout,
                "include_quantity": False,
                "payload_sha256": digest,
            })
    return {
        "hts4": headings,
        "hts6": list(codes),
        "needs_user_approval": True,
        "note": "Outside the approved scope: acquire does not fetch these; the user approves them separately.",
        "jobs": jobs,
    }


def plan_study(config: ResearchConfig, study_path: str | Path) -> tuple[dict[str, Any], Path]:
    """Plan a study against its frozen scope and write ``query_plan.json``."""

    study = load_project_study(config, study_path)
    scope = verify_scope_manifest(config.paths.root / study.scope_manifest)
    plan = build_query_plan(scope, study, max_records=config.dataweb.max_records)
    return plan, write_plan(plan, outputs_root=config.paths.outputs)


def plan_directory(plan: dict[str, Any], *, outputs_root: str | Path) -> Path:
    """Return ``outputs/runs/<study_id>/plan-<hash12>`` for a plan."""

    return Path(outputs_root) / "runs" / plan["study"]["study_id"] / f"plan-{plan['plan_sha256'][:12]}"


def write_plan(plan: dict[str, Any], *, outputs_root: str | Path) -> Path:
    """Write ``query_plan.json`` once; re-planning identical inputs is a no-op."""

    path = plan_directory(plan, outputs_root=outputs_root) / "query_plan.json"
    if path.exists():
        existing = load_plan(path)
        if existing["plan_sha256"] != plan["plan_sha256"]:
            raise ScopeGateError(f"A different plan already occupies {path}")
        return path
    return write_json_atomic(path, plan)


def load_plan(path: str | Path) -> dict[str, Any]:
    """Load a plan and reject it if its content no longer matches its hash."""

    plan_path = Path(path).resolve()
    if not plan_path.is_file():
        raise ScopeGateError(f"Query plan not found: {plan_path}")
    try:
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScopeGateError(f"Query plan is not valid JSON: {plan_path}") from exc
    if plan.get("schema_version") != PLAN_SCHEMA_VERSION:
        raise ScopeGateError(f"Unsupported query plan schema in {plan_path}; re-run `plan`.")
    if _plan_digest(plan) != plan.get("plan_sha256"):
        raise ScopeGateError(f"{plan_path} was edited after planning (plan_sha256 mismatch). Re-run `plan` instead.")
    return plan


def _is_cached(config: ResearchConfig, job: dict[str, Any]) -> bool:
    try:
        return cached_metadata(config, job["cache_stem"], job["payload_sha256"]) is not None
    except json.JSONDecodeError:  # unreadable metadata: `acquire` will report it
        return False


def cached_jobs(config: ResearchConfig, plan: dict[str, Any]) -> list[str]:
    """Return cache stems whose snapshot already exists for the same payload."""

    return [job["cache_stem"] for job in plan["jobs"] if _is_cached(config, job)]


def codes_by_period(plan: dict[str, Any]) -> list[dict[str, Any]]:
    """Summarize which approved codes each period queries (breakout jobs only).

    Consecutive breakout jobs with the same frequency and codes cover
    consecutive years, so each group is one period.
    """

    breakouts = (
        job for job in plan["jobs"]
        if job.get("purpose", "scope") == "scope" and job["break_out_origins"]
    )
    rows: list[dict[str, Any]] = []
    for (frequency, codes), group in groupby(breakouts, key=lambda job: (job["frequency"], job["codes"])):
        jobs = list(group)
        start, end = jobs[0]["years"][0], jobs[-1]["years"][-1]
        rows.append({"frequency": frequency, "start": start, "end": end, "codes": codes})
    return rows


def period_codes(plan: dict[str, Any]) -> dict[tuple[str, str], tuple[str, ...]]:
    """Map each queried ``(frequency, period)`` to the approved codes queried for it."""

    result: dict[tuple[str, str], tuple[str, ...]] = {}
    for job in plan["jobs"]:
        if job.get("purpose", "scope") != "scope" or job["break_out_origins"]:
            continue
        for year in job["years"]:
            if job["frequency"] == "annual":
                result[("annual", str(year))] = tuple(job["codes"])
            else:
                for month in range(1, 13):
                    result[("monthly", f"{year:04d}-{month:02d}")] = tuple(job["codes"])
    return result


def query_through(plan: dict[str, Any]) -> dict[str, str]:
    """The last period each frequency asked for (not the last period with data)."""

    result: dict[str, str] = {}
    for job in plan["jobs"]:
        if job.get("purpose", "scope") != "scope":
            continue
        year = max(map(int, job["years"]))
        period = str(year) if job["frequency"] == "annual" else f"{year:04d}-12"
        result[job["frequency"]] = max(result.get(job["frequency"], period), period)
    return result


def in_scope(frame: Any, plan: dict[str, Any]) -> Any:
    """Keep only rows whose code is an approved code for that period."""

    allowed = {(frequency, period, code) for (frequency, period), codes in period_codes(plan).items() for code in codes}
    keys = zip(frame["frequency"].astype(str), frame["period"].astype(str), frame["hts_code"].astype(str))
    return frame.loc[[key in allowed for key in keys]].reset_index(drop=True)


def snapshot_data_through(plan: dict[str, Any], world_paths: Iterable[Path]) -> dict[str, Any]:
    """Derive ``data_through`` from the independent World snapshots.

    A placeholder month DataWeb returns as blank is not an observation, so the
    cutoff is the last complete month (or year) for every approved code.  YTD
    comparisons align their months to it.  pandas is imported only here.
    """

    from .analysis import derive_data_through
    from .normalize import normalize_dataweb_files

    try:
        world = in_scope(normalize_dataweb_files(list(world_paths)), plan)
        return {"data_through": derive_data_through(world, period_codes(plan))}
    except ValueError as exc:  # the analysis stage re-derives it and reports the same problem
        return {"data_through": None, "data_through_error": str(exc)}


def acquire_plan(
    config: ResearchConfig,
    plan_path: str | Path,
    *,
    refresh: bool = False,
    client: DataWebClient | None = None,
    today: date | None = None,
) -> dict[str, Any]:
    """Execute a query plan after the scope gate passes.

    The gate re-verifies the frozen scope, reloads the study, and rebuilds the
    plan from those approved inputs; the run proceeds only if the rebuilt
    ``plan_sha256`` equals the stored one.  The bearer token is read by
    :class:`DataWebClient` from ``DATAWEB_TOKEN`` and is never written.
    """

    root = config.paths.root
    plan_file = Path(plan_path).resolve()
    plan = load_plan(plan_file)
    scope = verify_scope_manifest(root / plan["scope"]["manifest_path"])
    if scope.scope_sha256 != plan["scope"]["scope_sha256"]:
        raise ScopeGateError("The approved scope behind this plan now has a different scope_sha256; re-run `plan`.")
    study = load_project_study(config, root / plan["study"]["path"], today=today)
    if (root / study.scope_manifest).resolve() != scope.manifest_path.resolve():
        raise ScopeGateError(
            f"Study {study.study_id} points to {study.scope_manifest}, but the verified scope came from "
            f"{scope.manifest_path}"
        )
    rebuilt = build_query_plan(scope, study, max_records=config.dataweb.max_records)
    if rebuilt["plan_sha256"] != plan["plan_sha256"]:
        raise ScopeGateError(
            "This plan no longer matches its approved scope and study (codes, years, or DataWeb settings differ). "
            "Re-run `plan` and acquire the new plan."
        )

    client = client or DataWebClient(config)
    provenance_base = {
        "scope_id": scope.scope_id,
        "scope_version": scope.scope_version,
        "scope_sha256": scope.scope_sha256,
        "hts_level": scope.hts_level,
        "study_id": study.study_id,
        "plan_sha256": plan["plan_sha256"],
    }
    snapshots: list[dict[str, Any]] = []
    for job in plan["jobs"]:
        snapshot = client.fetch(
            QueryJob(cache_stem=job["cache_stem"], spec=_spec(job)),
            refresh=refresh,
            provenance={**provenance_base, "approved_codes": list(job["codes"])},
        )
        recorded = (snapshot.provenance or {}).get("scope_sha256")
        if recorded != scope.scope_sha256:
            raise ScopeGateError(
                f"Snapshot {snapshot.path} was acquired under scope_sha256={recorded!r}, not "
                f"{scope.scope_sha256}. Never reuse a scope_version for different content."
            )
        recorded_codes = (snapshot.provenance or {}).get("approved_codes")
        if recorded_codes != list(job["codes"]):
            raise ScopeGateError(
                f"Snapshot {snapshot.path} was acquired for approved_codes={recorded_codes!r}, not {job['codes']!r}."
            )
        snapshots.append(
            {
                "cache_stem": job["cache_stem"],
                "path": _relative(snapshot.path, root),
                "metadata_path": _relative(snapshot.metadata_path, root),
                "status": snapshot.status,
                "sha256": snapshot.sha256,
                "metadata_sha256": file_sha256(snapshot.metadata_path),
                "row_count": snapshot.row_count,
                # A payload-identical immutable cache may first have been
                # downloaded under another study/plan.  Preserve that source
                # lineage while acquisition.json binds reuse to this plan.
                "source_plan_sha256": (snapshot.provenance or {}).get("plan_sha256"),
            }
        )

    result = {
        "completed_at_utc": utc_now(),
        **({"context_jobs_not_fetched": len(plan["context"]["jobs"])} if plan.get("context") else {}),
        "plan_path": _relative(plan_file, root),
        "plan_sha256": plan["plan_sha256"],
        "scope_id": scope.scope_id,
        "scope_version": scope.scope_version,
        "scope_sha256": scope.scope_sha256,
        "study_id": study.study_id,
        "refresh": refresh,
        "downloaded": sum(item["status"] == "downloaded" for item in snapshots),
        "cached": sum(item["status"] == "cached" for item in snapshots),
        "query_through": query_through(plan),
        **snapshot_data_through(
            plan, (root / item["path"] for item, job in zip(snapshots, plan["jobs"]) if not job["break_out_origins"])
        ),
        "snapshots": snapshots,
        "secrets_recorded": False,
    }
    write_json_atomic(plan_file.parent / "acquisition.json", result)
    return result
