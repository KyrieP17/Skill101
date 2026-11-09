"""Scope-driven analysis, charts, validation and report orchestration.

A :class:`StudyContext` holds every verified input of one run; nothing is
discovered by globbing.  It is built in one of two ways:

* :func:`load_study_context` (the gated path): approved scope manifest ->
  rebuilt query plan -> ``acquisition.json`` -> hash-checked snapshots.
  Outputs go to ``outputs/runs/<study>/plan-<hash>/analysis-<hash>/``.
* :func:`load_replay_context`: legacy snapshots listed in
  ``config/replays/<id>.toml`` with a reviewed but unapproved scope.  Each
  snapshot's response hash is checked, its payload cannot be.  Outputs go to
  ``outputs/replays/`` and every manifest and report says so; no gated
  command reads a replay directory.

Both run the same analysis, chart, validation and report code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
import json
import tomllib
from typing import Any

import pandas as pd

from .analysis import (
    MEASURES,
    PERIOD,
    ValidationError,
    add_period_status,
    audit_period_coverage,
    build_display_series,
    build_event_path,
    build_origin_series,
    build_product_reallocation,
    build_scope_series,
    build_trade_panel,
    build_world_check,
    codes_not_in_force,
    compare_origin_windows,
    derive_data_through,
    validate_trade_panel,
)
from . import brief_check as brief_check_module
from . import calculations as calculation_module
from . import charts as chart_module
from . import features as feature_module
from . import flags as flag_module
from .config import ResearchConfig
from .dataweb import cached_metadata, file_sha256, snapshot_paths
from .normalize import SECOND_QUANTITY_COLUMNS, normalize_dataweb_files
from .plan import (
    build_query_plan,
    in_scope,
    load_plan,
    load_project_study,
    period_codes,
    plan_directory,
    query_through,
    timeline_records,
    drilldown_structure_sha256,
)
from .report import build_findings, render_report
from .scope import (
    CHAT_APPROVAL_METHOD,
    ApprovedScope,
    ScopeGateError,
    canonical_sha256,
    read_unapproved_scope,
    utc_now,
    verify_scope_manifest,
    write_json_atomic,
)
from .study import StudySpec
from . import timeline as timeline_module
from . import drilldown as drilldown_module


ANALYSIS_SCHEMA_VERSION = 2
CORE_OUTPUTS = (
    "trade_tidy",
    "trade_panel",
    "origin_series",
    "display_series",
    "scope_series",
    "world_check",
    "coverage_audit",
    "findings",
)
OPTIONAL_OUTPUTS = ("product_reallocation", "origin_change", "monthly_panel", "event_metrics")
# Outputs of opt-in study features.  They appear in the manifest only when the
# study turns the feature on, so default studies keep their manifest shape.
FEATURE_OUTPUTS = (
    "tariff_timeline", "focal_split", "segment_series", "quantity_check", "drilldown_menu", "heading_map",
    "share_decomposition", "calculations", "anomaly_flags", "mention_list", "window_candidates",
)
INTERPRETATION_BOUNDARIES = [
    "Country or area of origin is not value-added origin.",
    "A gaining source does not prove direct relocation from the focal origin.",
    "Policy-aligned timing is descriptive unless a causal design is added.",
    "Realized duty (calculated duty / customs value) is not the statutory tariff rate.",
]


@dataclass(frozen=True)
class StudyContext:
    """All verified inputs needed by analysis; no glob-discovered files."""

    study: StudySpec
    scope: ApprovedScope
    plan: dict[str, Any]
    plan_path: Path | None
    acquisition: dict[str, Any]
    raw_paths: tuple[Path, ...]
    metadata_paths: tuple[Path, ...]
    world_paths: tuple[Path, ...]
    output_dir: Path
    gate: dict[str, Any] = field(default_factory=dict)


def _inside(root: Path, value: str, *, label: str) -> Path:
    path = (root / value).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise ScopeGateError(f"{label} escapes the project root: {value}") from exc
    return path


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file():
        raise ScopeGateError(f"{label} not found: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScopeGateError(f"{label} is not valid JSON: {path}") from exc
    if not isinstance(value, dict):
        raise ScopeGateError(f"{label} must be a JSON object: {path}")
    return value


def _verify_snapshot(
    root: Path,
    item: dict[str, Any],
    job: dict[str, Any],
    *,
    scope_sha256: str,
) -> tuple[Path, Path]:
    raw_path = _inside(root, str(item.get("path", "")), label="snapshot path")
    metadata_path = _inside(root, str(item.get("metadata_path", "")), label="snapshot metadata path")
    if not raw_path.is_file() or not metadata_path.is_file():
        raise ScopeGateError(f"Acquisition snapshot or metadata is missing: {raw_path}")
    raw_hash = file_sha256(raw_path)
    if raw_hash != item.get("sha256"):
        raise ScopeGateError(f"Snapshot hash no longer matches acquisition.json: {raw_path}")
    metadata = _read_json(metadata_path, label="snapshot metadata")
    if metadata.get("response_sha256") != raw_hash:
        raise ScopeGateError(f"Snapshot metadata response hash does not match: {metadata_path}")
    if file_sha256(metadata_path) != item.get("metadata_sha256"):
        raise ScopeGateError(f"Snapshot metadata hash no longer matches acquisition.json: {metadata_path}")
    if metadata.get("payload_sha256") != job.get("payload_sha256"):
        raise ScopeGateError(f"Snapshot payload does not match the verified query plan: {raw_path}")
    expected_query = {
        "hts_level": int(job["hts_level"]),
        "codes": list(job["codes"]),
        "years": [int(year) for year in job["years"]],
        "frequency": job["frequency"],
        "break_out_origins": bool(job["break_out_origins"]),
        "include_duties": True,
        **({"second_quantity": True} if job.get("second_quantity") else {}),
    }
    if metadata.get("query") != expected_query:
        raise ScopeGateError(f"Snapshot metadata query does not match the verified plan job: {metadata_path}")
    provenance = metadata.get("provenance") or {}
    for key, expected in {"scope_sha256": scope_sha256, "approved_codes": job.get("codes")}.items():
        if provenance.get(key) != expected:
            raise ScopeGateError(
                f"Snapshot provenance {key}={provenance.get(key)!r}, expected {expected!r}: {raw_path}"
            )
    if item.get("source_plan_sha256") != provenance.get("plan_sha256"):
        raise ScopeGateError(f"Snapshot source-plan lineage does not match metadata: {raw_path}")
    return raw_path, metadata_path


def _check_outputs_root(config: ResearchConfig) -> Path:
    root = config.paths.root.resolve()
    try:
        config.paths.outputs.resolve().relative_to(root)
    except ValueError as exc:
        raise ScopeGateError(f"Study outputs must stay inside the project root: {config.paths.outputs}") from exc
    return root


def load_study_context(config: ResearchConfig, study_path: str | Path) -> StudyContext:
    """Rebuild every identity and return only the exact acquired snapshots."""

    root = _check_outputs_root(config)
    study = load_project_study(config, study_path)
    scope = verify_scope_manifest(root / study.scope_manifest)
    rebuilt = build_query_plan(scope, study, max_records=config.dataweb.max_records)
    run_dir = plan_directory(rebuilt, outputs_root=config.paths.outputs)
    plan_path = run_dir / "query_plan.json"
    plan = load_plan(plan_path)
    if plan["plan_sha256"] != rebuilt["plan_sha256"]:
        raise ScopeGateError("Stored query plan no longer matches the approved scope and study periods; re-run `plan`.")
    acquisition = _read_json(run_dir / "acquisition.json", label="acquisition record")
    for key, expected in (
        ("plan_sha256", plan["plan_sha256"]),
        ("scope_sha256", scope.scope_sha256),
        ("study_id", study.study_id),
    ):
        if acquisition.get(key) != expected:
            raise ScopeGateError(f"acquisition.json {key}={acquisition.get(key)!r}, expected {expected!r}")

    jobs = {str(job["cache_stem"]): job for job in plan["jobs"]}
    items = acquisition.get("snapshots")
    if not isinstance(items, list) or len(items) != len(jobs):
        raise ScopeGateError("acquisition.json must name exactly one snapshot for every query-plan job")
    seen: set[str] = set()
    raw_paths: list[Path] = []
    metadata_paths: list[Path] = []
    world_paths: list[Path] = []
    for item in items:
        stem = str((item or {}).get("cache_stem", ""))
        if stem not in jobs or stem in seen:
            raise ScopeGateError(f"Unknown or repeated acquisition cache_stem: {stem!r}")
        seen.add(stem)
        raw_path, metadata_path = _verify_snapshot(root, item, jobs[stem], scope_sha256=scope.scope_sha256)
        raw_paths.append(raw_path)
        metadata_paths.append(metadata_path)
        if not jobs[stem]["break_out_origins"]:
            world_paths.append(raw_path)
    return StudyContext(
        study=study,
        scope=scope,
        plan=plan,
        plan_path=plan_path,
        acquisition=acquisition,
        raw_paths=tuple(raw_paths),
        metadata_paths=tuple(metadata_paths),
        world_paths=tuple(world_paths),
        output_dir=run_dir / f"analysis-{study.study_sha256[:12]}",
        gate={
            "mode": "study",
            "scope_approved": True,
            "payload_hashes_verified": True,
            "continuity_verified": scope.continuity_verified,
            # Only chat approvals add a key, so analyses of terminal-approved scopes keep their bytes.
            **({"approval_method": "chat"} if scope.approval_method == CHAT_APPROVAL_METHOD else {}),
        },
    )


def load_replay_context(config: ResearchConfig, replay_path: str | Path) -> StudyContext:
    """Load a labeled legacy replay: unapproved scope, response-hash-checked snapshots."""

    root = _check_outputs_root(config)
    path = Path(replay_path).resolve()
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ScopeGateError(f"Cannot read replay config {path}: {exc}") from exc
    replay = raw.get("replay") or {}
    replay_id = str(replay.get("replay_id", "")).strip()
    if not replay_id or not replay.get("study") or not replay.get("scope") or not raw.get("snapshots"):
        raise ScopeGateError("A replay config needs [replay] replay_id, study, scope and [[snapshots]]")
    study = load_project_study(config, _inside(root, str(replay["study"]), label="replay study"))
    scope_path = _inside(root, str(replay["scope"]), label="replay scope")
    if scope_path.parent != (root / study.scope_manifest).resolve().parent:
        raise ScopeGateError(
            f"Replay scope {replay['scope']} is not the scope version named by study {study.study_id} "
            f"({study.scope_manifest})"
        )
    scope = read_unapproved_scope(scope_path)

    jobs: list[dict[str, Any]] = []
    raw_paths: list[Path] = []
    metadata_paths: list[Path] = []
    world_paths: list[Path] = []
    for entry in raw["snapshots"]:
        frequency = str(entry.get("frequency", ""))
        if frequency not in {"annual", "monthly"}:
            raise ScopeGateError(f"Replay snapshot frequency must be annual or monthly: {entry}")
        for year in entry.get("years", []):
            for label, breakout in (("all", True), ("world", False)):
                relative = str(entry["template"]).format(origins=label, year=int(year))
                raw_path = _inside(root, relative, label="replay snapshot")
                metadata_path = raw_path.with_suffix(".meta.json")
                if not raw_path.is_file() or not metadata_path.is_file():
                    raise ScopeGateError(f"Replay snapshot or metadata is missing: {raw_path}")
                raw_hash = file_sha256(raw_path)
                if _read_json(metadata_path, label="snapshot metadata").get("response_sha256") != raw_hash:
                    raise ScopeGateError(f"Replay snapshot no longer matches its recorded response hash: {raw_path}")
                jobs.append(
                    {
                        "cache_stem": raw_path.stem,
                        "frequency": frequency,
                        "years": [int(year)],
                        "codes": list(scope.codes_for_year(int(year))),
                        "break_out_origins": breakout,
                        "path": relative,
                        "sha256": raw_hash,
                        "payload_sha256": None,
                    }
                )
                raw_paths.append(raw_path)
                metadata_paths.append(metadata_path)
                if not breakout:
                    world_paths.append(raw_path)
    identity = {
        "replay": replay,
        "snapshots": raw["snapshots"],
        "scope_sha256": scope.scope_sha256,
        "inputs": [(job["path"], job["sha256"]) for job in jobs],
    }
    plan = {"kind": "legacy_replay", "plan_sha256": canonical_sha256(identity), "jobs": jobs}
    output_dir = (
        config.paths.outputs
        / "replays"
        / replay_id
        / f"replay-{plan['plan_sha256'][:12]}"
        / f"analysis-{study.study_sha256[:12]}"
    )
    return StudyContext(
        study=study,
        scope=scope,
        plan=plan,
        plan_path=None,
        acquisition={"snapshots": jobs},
        raw_paths=tuple(raw_paths),
        metadata_paths=tuple(metadata_paths),
        world_paths=tuple(world_paths),
        output_dir=output_dir,
        gate={
            "mode": "legacy_replay",
            "replay_id": replay_id,
            "replay_config": path.relative_to(root).as_posix(),
            "reason": str(replay.get("reason", "")).strip(),
            "scope_approved": False,
            "payload_hashes_verified": False,
            "continuity_verified": False,
        },
    )


def _within_data_through(frame: pd.DataFrame, data_through: dict[str, str | None]) -> pd.Series:
    """Select the contiguous, complete observation range used for metrics."""

    keep = pd.Series(False, index=frame.index)
    periods = frame["period"].astype(str)
    for frequency, cutoff in data_through.items():
        if cutoff is not None:
            keep |= frame["frequency"].astype(str).eq(frequency) & periods.le(str(cutoff))
    return keep


def _reported_by_world(frame: pd.DataFrame, world: pd.DataFrame) -> pd.Series:
    """Rows whose code the World extract reports in that period.

    Inside ``data_through`` every queried code is reported except where a
    mid-year split leaves it blank because it is not in force that month
    (``derive_data_through``).  Those code-months carry no trade, so they are
    left out of the panel instead of entering it as blank, unit-less rows.
    """

    measures = [column for column in MEASURES if column in world.columns]
    observed = world[measures].apply(pd.to_numeric, errors="coerce").notna().any(axis=1)
    keys = set(
        zip(
            world.loc[observed, "frequency"].astype(str),
            world.loc[observed, "period"].astype(str),
            world.loc[observed, "hts_code"].astype(str),
        )
    )
    rows = zip(frame["frequency"].astype(str), frame["period"].astype(str), frame["hts_code"].astype(str))
    return pd.Series([key in keys for key in rows], index=frame.index)


def _retrieved_on(context: StudyContext, year: int) -> date | None:
    """Earliest recorded retrieval date of the annual World snapshots covering ``year``."""

    dates = []
    world = set(context.world_paths)
    for raw_path, metadata_path in zip(context.raw_paths, context.metadata_paths):
        if raw_path not in world:
            continue
        metadata = _read_json(metadata_path, label="snapshot metadata")
        query = metadata.get("query") or {}
        if query.get("frequency") == "annual" and year in query.get("years", []) and metadata.get("retrieved_at_utc"):
            dates.append(datetime.fromisoformat(str(metadata["retrieved_at_utc"]).replace("Z", "+00:00")).date())
    return min(dates) if dates else None


def _label_periods(panel: pd.DataFrame, data_through: dict[str, str | None], retrieved_on: date | None) -> pd.DataFrame:
    """Label annual rows that may not be complete years.

    DataWeb returns the current year's annual total as a year-to-date sum
    under the plain year label.  The last annual period counts as a full year
    only when monthly data reach its December, or when it was retrieved after
    February of the next year (December data come out in early February).
    Otherwise it is ``ytd_through_MM`` when the months are known, and
    ``partial_year_unverified`` when they are not.
    """

    monthly, annual = data_through.get("monthly"), data_through.get("annual")
    if monthly and annual and monthly[:4] == annual and monthly[5:7] != "12":
        return add_period_status(panel, ytd_year=int(annual), ytd_end_month=int(monthly[5:7]))
    labeled = add_period_status(panel, ytd_end_month=12)
    complete = bool(monthly and monthly >= f"{annual}-12") or (
        retrieved_on is not None and annual is not None and retrieved_on >= date(int(annual) + 1, 3, 1)
    )
    if annual and not complete:
        mask = labeled["frequency"].eq("annual") & labeled["period"].astype(str).eq(annual)
        labeled.loc[mask, "period_status"] = "partial_year_unverified"
    return labeled


def _expected_periods(world_tidy: pd.DataFrame, plan: dict[str, Any], frequency: str) -> list[str]:
    """Queried periods up to the last period with any non-zero World observation."""

    queried = sorted(period for (kind, period) in period_codes(plan) if kind == frequency)
    world = world_tidy[world_tidy["frequency"].eq(frequency)]
    measures = world[["customs_value_usd", "quantity", "calculated_duty_usd"]].apply(pd.to_numeric, errors="coerce")
    nonzero = world.loc[measures.fillna(0).ne(0).any(axis=1), "period"].astype(str)
    return [period for period in queried if not nonzero.empty and period <= nonzero.max()]


def _expected_by_code(
    expected: list[str],
    frequency: str,
    codes_by_period: dict[tuple[str, str], tuple[str, ...]],
    out_of_force: dict[tuple[str, str], set[str]],
) -> dict[str, list[str]]:
    """Per code, the expected periods it was queried for and in force in."""

    codes = {code for (kind, _), queried in codes_by_period.items() if kind == frequency for code in queried}
    return {
        code: [
            period
            for period in expected
            if code in codes_by_period.get((frequency, period), ())
            and code not in out_of_force.get((frequency, period), set())
        ]
        for code in sorted(codes)
    }


def _json_ready(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, float) and value != value):
        return None
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        item = value.item()
        return None if isinstance(item, float) and item != item else item
    return value


def _write_json(path: Path, value: Any) -> Path:
    return write_json_atomic(path, _json_ready(value), allow_nan=False)


def _output_record(path: Path, base: Path) -> dict[str, str]:
    return {"path": path.relative_to(base).as_posix(), "sha256": file_sha256(path)}


def _on_quantity_basis(tidy: pd.DataFrame, basis: str) -> pd.DataFrame:
    """Put the study's quantity basis in ``quantity`` / ``quantity_unit``.

    With ``basis == "second"`` the second unit (e.g. kg) replaces the first,
    so quantity, quantity share and unit value are all per second unit.  The
    tidy CSV written for audit keeps both.
    """

    if basis == "first":
        return tidy.drop(columns=[column for column in SECOND_QUANTITY_COLUMNS if column in tidy])
    if "quantity_2" not in tidy:
        raise ValidationError(
            "The study asks for the second unit of quantity, but the snapshots have no "
            "'Second Unit of Quantity' table; run plan and acquire again for this study"
        )
    return tidy.assign(quantity=tidy["quantity_2"], quantity_unit=tidy["quantity_2_unit"]).drop(
        columns=SECOND_QUANTITY_COLUMNS
    )


def _relative_source(frame: pd.DataFrame, root: Path) -> pd.DataFrame:
    frame = frame.copy()
    frame["source_file"] = [Path(value).resolve().relative_to(root).as_posix() for value in frame["source_file"]]
    return frame


def _comparisons(
    origin_series: pd.DataFrame,
    panel: pd.DataFrame,
    study: StudySpec,
    data_through: dict[str, str | None],
) -> tuple[pd.DataFrame, dict[str, str], list[feature_module.Window]]:
    """Annual base-vs-compare and same-months YTD comparisons for every origin.

    Also returns the comparison windows that produced rows, so the opt-in
    feature tables use exactly the same periods.
    """

    frames: list[pd.DataFrame] = []
    skips: dict[str, str] = {}
    windows: list[feature_module.Window] = []

    def add(frame: pd.DataFrame, comparison: str, frequency: str, base: list[str], compare: list[str]) -> None:
        if not frame.empty:
            frames.append(frame)
            windows.append(feature_module.Window(comparison, frequency, tuple(base), tuple(compare)))

    if study.base_year is not None and study.compare_year is not None:
        annual = panel[panel["frequency"].eq("annual")]
        statuses = {
            year: set(annual.loc[annual["period"].astype(str).eq(str(year)), "period_status"].dropna())
            for year in (study.base_year, study.compare_year)
        }
        if any(not values for values in statuses.values()):
            skips["annual"] = "base or compare year has no complete annual observation"
        elif statuses[study.base_year] != {"full_year"} or statuses[study.compare_year] != {"full_year"}:
            skips["annual"] = f"compare years are not both full years ({statuses}); use the YTD comparison"
        else:
            comparison = f"annual_{study.base_year}_vs_{study.compare_year}"
            base, compare = [str(study.base_year)], [str(study.compare_year)]
            add(
                compare_origin_windows(
                    origin_series, comparison=comparison, frequency="annual", base_periods=base, compare_periods=compare
                ),
                comparison, "annual", base, compare,
            )
    else:
        skips["annual"] = "study has no [comparison] years"
    monthly_cutoff = data_through.get("monthly")
    if monthly_cutoff and study.monthly is not None:
        year, month = int(monthly_cutoff[:4]), int(monthly_cutoff[5:7])
        if year - 1 >= study.monthly.start:
            comparison = f"ytd_{month:02d}_{year - 1}_vs_{year}"
            base = [f"{year - 1}-{item:02d}" for item in range(1, month + 1)]
            compare = [f"{year}-{item:02d}" for item in range(1, month + 1)]
            ytd = compare_origin_windows(
                origin_series, comparison=comparison, frequency="monthly", base_periods=base, compare_periods=compare
            )
            if ytd.empty:
                skips["ytd"] = "prior-year months are not all observed"
            else:
                add(ytd, comparison, "monthly", base, compare)
        else:
            skips["ytd"] = "no prior year inside periods.monthly"
    else:
        skips["ytd"] = "no monthly observations"
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), skips, windows


def _analyze(config: ResearchConfig, context: StudyContext) -> dict[str, Any]:
    root = config.paths.root.resolve()
    out = context.output_dir
    processed, tables = out / "processed", out / "tables"
    for directory in (processed, tables):
        directory.mkdir(parents=True, exist_ok=True)
    study, scope = context.study, context.scope

    tidy = in_scope(_relative_source(normalize_dataweb_files(context.raw_paths), root), context.plan)
    world_tidy = in_scope(normalize_dataweb_files(context.world_paths), context.plan)
    queried_through = query_through(context.plan)
    data_through = derive_data_through(world_tidy, period_codes(context.plan))
    recorded = context.acquisition.get("data_through")
    if recorded is not None and recorded != data_through:
        raise ScopeGateError(f"Snapshots now give data_through={data_through}, but acquisition.json recorded {recorded}")
    tidy.to_csv(processed / "trade_tidy.csv", index=False)
    both = study.quantity_basis == "both"
    weight_tidy = _on_quantity_basis(tidy, "second") if both else None
    tidy = _on_quantity_basis(tidy, "first" if both else study.quantity_basis)
    second_basis = study.quantity_basis == "second"
    # On the second basis the scope's single second unit makes quantities
    # comparable (plan refuses a second-basis study without one).
    quantity_unit = scope.second_unit if second_basis else scope.quantity_unit
    quantity_comparable = bool(scope.second_unit) if second_basis else scope.quantity_comparable
    analysis_mask = _within_data_through(tidy, data_through) & _reported_by_world(tidy, world_tidy)
    if not analysis_mask.any():
        raise ValidationError("No complete observations remain at or before data_through")
    panel = build_trade_panel(tidy.loc[analysis_mask])
    annual_cutoff = data_through.get("annual")
    panel = _label_periods(panel, data_through, _retrieved_on(context, int(annual_cutoff)) if annual_cutoff else None)
    validation = validate_trade_panel(panel, strict=True)
    panel.to_csv(processed / "trade_panel.csv", index=False)
    # The panel measured in the second unit: beside the first ("both"), or the
    # main panel itself when the study's basis is the second unit.
    weight_panel = build_trade_panel(weight_tidy.loc[analysis_mask]) if both else panel if second_basis else None

    origin_series = build_origin_series(panel, quantity_comparable=quantity_comparable)
    origin_series_2 = build_origin_series(weight_panel, quantity_comparable=bool(scope.second_unit)) if both else None
    if origin_series_2 is not None:
        origin_series = feature_module.add_second_unit_columns(
            origin_series, origin_series_2, keys=PERIOD + ["origin_name"], kind="series"
        )
    display = build_display_series(
        origin_series, focal_origin=study.focal_origin, highlight_origins=study.highlight_origins
    )
    scope_series = build_scope_series(origin_series, focal_origin=study.focal_origin)
    world_check = build_world_check(panel)
    codes_by_period = period_codes(context.plan)
    out_of_force = codes_not_in_force(world_tidy, codes_by_period)
    coverage = pd.concat(
        [
            audit_period_coverage(
                tidy[tidy["frequency"].eq(frequency)],
                expected_periods=(expected := _expected_periods(world_tidy, context.plan, frequency)),
                expected_by_code=_expected_by_code(expected, frequency, codes_by_period, out_of_force),
            ).assign(frequency=frequency, data_through=data_through.get(frequency))
            for frequency in sorted({job["frequency"] for job in context.plan["jobs"]})
        ],
        ignore_index=True,
    )
    changes, comparison_skips, windows = _comparisons(origin_series, panel, study, data_through)
    if origin_series_2 is not None and not changes.empty:
        changes_2 = pd.concat(
            [
                compare_origin_windows(origin_series_2, comparison=window.comparison, frequency=window.frequency,
                                       base_periods=window.base, compare_periods=window.compare)
                for window in windows
            ],
            ignore_index=True,
        )
        changes = feature_module.add_second_unit_columns(changes, changes_2, keys=["comparison", "origin_name"], kind="change")
    included_codes = sorted({line.hts_code for line in scope.included})
    features: dict[str, Any] = {}
    feature_written: dict[str, Path] = {}
    if study.timeline_file:
        records = timeline_records(config, study)
        if canonical_sha256(records) != study.timeline_records_sha256:
            raise ScopeGateError("A timeline file changed while the study was running; run the stage again.")
        rows = [row for layer in ("product", "country") for row in records.get(layer, [])]
        try:
            timeline_module.check_codes(rows, included_codes)
        except timeline_module.TimelineError as exc:
            raise ValidationError(str(exc)) from exc
        features["timeline"] = timeline_module.annotate(rows)
        pd.DataFrame(features["timeline"]).to_csv(tables / "tariff_timeline.csv", index=False)
        feature_written["tariff_timeline"] = tables / "tariff_timeline.csv"
    if study.drilldown_structure:
        if drilldown_structure_sha256(config, study) != study.drilldown_structure_sha256:
            raise ScopeGateError("The drill-down HTS structure file changed while the study was running; run the stage again.")
        try:
            structure = drilldown_module.read_structure(root / study.drilldown_structure)
        except drilldown_module.DrilldownError as exc:
            raise ValidationError(str(exc)) from exc
        years = [span for span in (study.annual, study.monthly) if span is not None]
        menu = drilldown_module.build_menu(
            structure,
            [
                drilldown_module.ScopeCode(
                    line.hts_code, line.valid_from, line.valid_to,
                    tuple(unit for unit in (line.quantity_unit, line.second_unit) if unit),
                )
                for line in scope.included
            ],
            features.get("timeline") or [],
            period_start=date(min(span.start for span in years), 1, 1),
            period_end=date(max(span.end for span in years), 12, 31),
        )
        pd.DataFrame(menu, columns=list(drilldown_module.MENU_COLUMNS)).to_csv(tables / "drilldown_menu.csv", index=False)
        feature_written["drilldown_menu"] = tables / "drilldown_menu.csv"
        features["drilldown_menu"] = _json_ready(menu)
    if study.hts4_context:
        context_jobs = context.plan["context"]["jobs"]
        cached = [job for job in context_jobs if cached_metadata(config, job["cache_stem"], job["payload_sha256"])]
        features["heading_map_status"] = {"jobs": len(context_jobs), "cached": len(cached)}
        if context_jobs and len(cached) == len(context_jobs):
            def frame(breakout: bool) -> pd.DataFrame:
                return normalize_dataweb_files(
                    snapshot_paths(config, job["cache_stem"])[0] for job in context_jobs if job["break_out_origins"] == breakout
                )
            heading_map = feature_module.build_heading_map(
                frame(True), frame(False), windows, focal_origin=study.focal_origin,
                scope_hts6={line.hts_code[:6] for line in scope.included},
            )
            heading_map.to_csv(tables / "heading_map.csv", index=False)
            feature_written["heading_map"] = tables / "heading_map.csv"
            features["heading_map"] = _json_ready(heading_map.to_dict(orient="records"))
    weight_unit = scope.second_unit if (both or second_basis) else None
    feature_tables: dict[str, pd.DataFrame] = {}
    if study.focal_split:
        feature_tables["focal_split"] = feature_module.build_focal_split(
            panel, windows, focal_origin=study.focal_origin, quantity_unit=quantity_unit,
            quantity_comparable=quantity_comparable, weight_panel=weight_panel if both else None, weight_unit=weight_unit,
        )
    if study.segments:
        feature_tables["segment_series"] = feature_module.build_segment_series(
            panel, study.segments, windows, focal_origin=study.focal_origin, weight_panel=weight_panel, weight_unit=weight_unit,
        )
    if both:
        feature_tables["quantity_check"] = feature_module.build_quantity_check(
            panel, weight_panel, windows, segments=study.segments, first_unit=scope.quantity_unit,
            second_unit=scope.second_unit, total_comparable=scope.quantity_comparable,
        )
    if study.derived:
        try:
            feature_tables["calculations"] = calculation_module.build_calculations(
                origin_series, study.derived, focal_origin=study.focal_origin, highlight_origins=study.highlight_origins,
                quantity_unit=quantity_unit, second_unit=scope.second_unit if both else None,
            )
        except calculation_module.CalculationError as exc:
            raise ValidationError(str(exc)) from exc
    for name, frame in feature_tables.items():
        frame.to_csv(tables / f"{name}.csv", index=False)
        feature_written[name] = tables / f"{name}.csv"
        features[name] = _json_ready(frame.to_dict(orient="records"))
    if study.decomposition:
        decomposition = feature_module.build_share_decomposition(
            display, windows, focal_origin=study.focal_origin, highlight_origins=study.highlight_origins,
            custom_windows=study.decomposition_windows, quantity_unit=quantity_unit, timeline=features.get("timeline"),
            panel=panel, mix=study.decomposition_mix,
        )
        decomposition.to_csv(tables / "share_decomposition.csv", index=False)
        feature_written["share_decomposition"] = tables / "share_decomposition.csv"
        # findings.json keeps the named, YTD and annual windows; the month-to-month rows stay in the CSV.
        named = decomposition[decomposition["window_kind"].ne("month")]
        features["share_decomposition"] = _json_ready(named.to_dict(orient="records"))
    # Rule-based aids for the brief (D-024): flags to ask about, what must be mentioned, window candidates.
    brief_tables: dict[str, pd.DataFrame] = {}
    anomaly_skips: dict[str, str] = {}
    if study.anomalies:
        brief_tables["anomaly_flags"], anomaly_skips = flag_module.build_anomaly_flags(
            changes, display, focal_origin=study.focal_origin, highlight_origins=study.highlight_origins,
            timeline=features.get("timeline"), drilldown_menu=features.get("drilldown_menu"),
            quantity_check=feature_tables.get("quantity_check"),
        )
    if study.brief_candidates:
        brief_tables["mention_list"] = flag_module.build_mention_list(
            changes, focal_origin=study.focal_origin, timeline=features.get("timeline"),
            continuity_verified=bool(context.gate.get("continuity_verified")),
        )
        brief_tables["window_candidates"] = flag_module.build_window_candidates(
            display, focal_origin=study.focal_origin, study_windows=study.decomposition_windows,
        )
    for name, frame in brief_tables.items():
        frame.to_csv(tables / f"{name}.csv", index=False)
        feature_written[name] = tables / f"{name}.csv"

    written: dict[str, Path | None] = {name: None for name in (*CORE_OUTPUTS, *OPTIONAL_OUTPUTS)}
    for name, frame in (
        ("origin_series", origin_series),
        ("display_series", display),
        ("scope_series", scope_series),
        ("world_check", world_check),
        ("coverage_audit", coverage),
    ):
        frame.to_csv(tables / f"{name}.csv", index=False)
        written[name] = tables / f"{name}.csv"
    written["trade_tidy"], written["trade_panel"] = processed / "trade_tidy.csv", processed / "trade_panel.csv"
    if not changes.empty:
        changes.to_csv(tables / "origin_change.csv", index=False)
        written["origin_change"] = tables / "origin_change.csv"

    reallocation = pd.DataFrame()
    if "annual" not in comparison_skips:
        reallocation = build_product_reallocation(
            panel, base_year=study.base_year, compare_year=study.compare_year, focal_origin=study.focal_origin
        )
        if not reallocation.empty:
            reallocation.to_csv(tables / "product_reallocation.csv", index=False)
            written["product_reallocation"] = tables / "product_reallocation.csv"

    metrics: dict[str, Any] = {"available": False, "reason": "study has no periods.monthly"}
    if study.monthly is not None:
        monthly, metrics = build_event_path(display, focal_origin=study.focal_origin, windows=study.event_windows)
        if metrics.get("available"):
            monthly.to_csv(tables / "monthly_panel.csv", index=False)
            _write_json(tables / "event_metrics.json", metrics)
            written["monthly_panel"], written["event_metrics"] = tables / "monthly_panel.csv", tables / "event_metrics.json"

    skips = {
        "product_reallocation": None if written["product_reallocation"] else comparison_skips.get("annual", "no comparable products"),
        "origin_change": None if written["origin_change"] else "; ".join(comparison_skips.values()),
        "monthly_panel": None if written["monthly_panel"] else metrics.get("reason"),
        "event_metrics": None if written["event_metrics"] else metrics.get("reason"),
        "comparisons": comparison_skips,
        "policy_events": None if study.policy_event_ids else "study lists no [events] ids",
        "event_windows": None if study.event_windows else "study lists no [[event_windows]]",
        "fred_domestic_proxy": "not part of the scope-driven study",
    }
    if study.anomalies:
        skips["anomaly_rules"] = anomaly_skips
    identity = {
        "study_id": study.study_id,
        "study_title": study.title,
        "study_path": study.relative_path,
        "study_sha256": study.study_sha256,
        "policy_event_records_sha256": study.policy_event_records_sha256,
        "scope_id": scope.scope_id,
        "scope_version": scope.scope_version,
        "product_name": scope.product_name,
        "hts_level": scope.hts_level,
        "included_codes": included_codes,
        "quantity_unit": quantity_unit,
        "quantity_comparable": quantity_comparable,
        **({"quantity_basis": "second", "first_unit": scope.quantity_unit} if second_basis else {}),
        **({"quantity_basis": "both"} if both else {}),
        "second_unit": scope.second_unit,
        "second_units": list(scope.second_units),
        "scope_sha256": scope.scope_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "gate": context.gate,
        "query_through": queried_through,
        "data_through": data_through,
    }
    findings = build_findings(
        identity=identity,
        focal_origin=study.focal_origin,
        validation=validation,
        tables={
            "scope_series": scope_series,
            "display_series": display,
            "origin_change": changes,
            "world_check": world_check,
            "coverage_audit": coverage,
            "product_reallocation": reallocation,
        },
        event_metrics=metrics,
        skips=skips,
        features=features,
    )
    _write_json(out / "findings.json", findings)
    written["findings"] = out / "findings.json"

    manifest = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        **identity,
        "plan_path": context.plan_path.relative_to(root).as_posix() if context.plan_path else None,
        "rows_excluded_after_data_through": int((~analysis_mask).sum()),
        "inputs": [
            {"kind": kind, "path": path.relative_to(root).as_posix(), "sha256": file_sha256(path)}
            for kind, paths in (("raw_snapshot", context.raw_paths), ("snapshot_metadata", context.metadata_paths))
            for path in paths
        ],
        "validation": validation,
        "outputs": {
            **{name: _output_record(path, out) if path else None for name, path in written.items()},
            **{name: _output_record(path, out) for name, path in feature_written.items()},
        },
        "optional_skips": skips,
        "interpretation_boundaries": INTERPRETATION_BOUNDARIES,
    }
    _write_json(out / "analysis_manifest.json", manifest)
    return {
        "analysis_dir": str(out),
        "mode": context.gate.get("mode"),
        "study_sha256": study.study_sha256,
        "scope_sha256": scope.scope_sha256,
        "plan_sha256": context.plan["plan_sha256"],
        "query_through": queried_through,
        "data_through": data_through,
        "tidy_rows": len(tidy),
        "panel_rows": len(panel),
        "validation": validation,
    }


def _verified_analysis(config: ResearchConfig, context: StudyContext) -> dict[str, Any]:
    manifest = _read_json(context.output_dir / "analysis_manifest.json", label="analysis manifest")
    for key, expected in (
        ("scope_sha256", context.scope.scope_sha256),
        ("plan_sha256", context.plan["plan_sha256"]),
        ("study_sha256", context.study.study_sha256),
    ):
        if manifest.get(key) != expected:
            raise ScopeGateError(f"analysis manifest {key} does not match the current verified study")
    if manifest.get("schema_version") != ANALYSIS_SCHEMA_VERSION or (manifest.get("gate") or {}).get("mode") != context.gate["mode"]:
        raise ScopeGateError("analysis manifest was written by another schema or run mode; re-run `analyze`")
    root = config.paths.root
    input_records = manifest.get("inputs")
    if not isinstance(input_records, list):
        raise ScopeGateError("Analysis manifest inputs must be a complete list")
    expected_inputs = {path.relative_to(root).as_posix() for path in (*context.raw_paths, *context.metadata_paths)}
    recorded_inputs = {str(item.get("path", "")) for item in input_records if isinstance(item, dict)}
    if recorded_inputs != expected_inputs or len(input_records) != len(expected_inputs):
        raise ScopeGateError("Analysis manifest input set does not match the exact acquisition snapshots and metadata")
    for item in input_records:
        path = _inside(root, str(item.get("path", "")), label="analysis input")
        if file_sha256(path) != item.get("sha256"):
            raise ScopeGateError(f"Analysis input changed after the manifest was written: {path}")
    outputs = manifest.get("outputs")
    base = {*CORE_OUTPUTS, *OPTIONAL_OUTPUTS}
    if not isinstance(outputs, dict) or not base <= set(outputs) <= base | set(FEATURE_OUTPUTS):
        raise ScopeGateError("Analysis manifest output set is incomplete or has unknown entries")
    if any(outputs.get(name) is None for name in CORE_OUTPUTS):
        raise ScopeGateError(f"Analysis manifest is missing required outputs: {list(CORE_OUTPUTS)}")
    for name, item in outputs.items():
        if item is not None:
            _verify_artifact(context.output_dir, item, label=f"Analysis output {name!r}")
    return manifest


def _verify_artifact(base: Path, item: Any, *, label: str) -> Path:
    if not isinstance(item, dict):
        raise ScopeGateError(f"{label} record is malformed")
    path = (base / str(item.get("path", ""))).resolve()
    try:
        path.relative_to(base.resolve())
    except ValueError as exc:
        raise ScopeGateError(f"{label} escapes its run directory") from exc
    if not path.is_file() or file_sha256(path) != item.get("sha256"):
        raise ScopeGateError(f"{label} changed after it was written: {path}")
    return path


def _render_charts(config: ResearchConfig, context: StudyContext) -> list[str]:
    manifest = _verified_analysis(config, context)
    out = context.output_dir
    outputs = manifest["outputs"]
    tables = {
        name: pd.read_csv(out / outputs[name]["path"], dtype={"period": "string", "hts_code": "string", "heading": "string", "hts6": "string"})
        for name in (
            "scope_series", "display_series", "world_check", "origin_change", "product_reallocation", "monthly_panel",
            "tariff_timeline", "segment_series", "heading_map",
        )
        if outputs.get(name)
    }
    events = pd.read_csv(config.policy_events_path)
    events = events[events["event_id"].isin(context.study.policy_event_ids)].copy()
    events = events.rename(columns={"event_date": "date", "event_label": "short_label"})
    labels = chart_module.ChartLabels(
        product=context.scope.product_name,
        title=context.study.title,
        focal_origin=context.study.focal_origin,
        highlight_origins=context.study.highlight_origins,
        quantity_unit=(
            context.scope.second_unit if context.study.quantity_basis == "second" else context.scope.quantity_unit
        ),
        source=_source_line(manifest),
    )
    generated, skipped = chart_module.render_all(
        tables,
        events=events,
        windows=[window.as_json() for window in context.study.event_windows],
        output_dir=out / "charts",
        labels=labels,
        timeline=tables.get("tariff_timeline"),
        heading_map=tables.get("heading_map", pd.DataFrame()) if context.study.hts4_context else None,
    )
    missing = chart_module.validate_chart_audit_files(out / "charts")
    if missing:
        raise ValidationError(f"Charts missing same-stem audit CSVs: {missing}")
    _write_json(
        out / "chart_manifest.json",
        {
            "schema_version": 2,
            "generated_at_utc": utc_now(),
            "scope_sha256": manifest["scope_sha256"],
            "plan_sha256": manifest["plan_sha256"],
            "study_sha256": manifest["study_sha256"],
            "analysis_manifest": "analysis_manifest.json",
            "analysis_manifest_sha256": file_sha256(out / "analysis_manifest.json"),
            "analysis_data_through": manifest.get("data_through"),
            "charts": [
                {
                    "stem": png.stem,
                    "png": _output_record(png, out),
                    "chart_data": _output_record(png.with_suffix(".csv"), out),
                    "supplemental_data": [_output_record(path, out) for path in sorted(png.parent.glob(f"{png.stem}.*.csv"))],
                }
                for png in generated
            ],
            "skipped": skipped,
        },
    )
    return [str(path) for path in generated]


def _source_line(manifest: dict[str, Any]) -> str:
    through = ", ".join(f"{key} through {value}" for key, value in (manifest.get("data_through") or {}).items() if value)
    codes = ", ".join(manifest.get("included_codes") or [])
    mode = " Legacy replay: scope not approved." if (manifest.get("gate") or {}).get("mode") == "legacy_replay" else ""
    return f"Source: USITC DataWeb, U.S. imports for consumption, HTS {codes}; {through}.{mode}"


def _validate(config: ResearchConfig, context: StudyContext) -> dict[str, Any]:
    manifest = _verified_analysis(config, context)
    out = context.output_dir
    panel = pd.read_csv(out / "processed" / "trade_panel.csv", dtype={"period": "string", "hts_code": "string"})
    validation = validate_trade_panel(panel, strict=True)
    missing = chart_module.validate_chart_audit_files(out / "charts") if (out / "charts").exists() else []
    if missing:
        raise ValidationError(f"Charts missing same-stem audit CSVs: {missing}")
    chart_manifest_path = out / "chart_manifest.json"
    if not chart_manifest_path.exists():
        raise ScopeGateError("chart_manifest.json is missing; run the charts stage first.")
    chart_manifest = _read_json(chart_manifest_path, label="chart manifest")
    for key in ("scope_sha256", "plan_sha256", "study_sha256"):
        if chart_manifest.get(key) != manifest.get(key):
            raise ScopeGateError(f"chart manifest {key} does not match analysis manifest")
    if chart_manifest.get("analysis_manifest_sha256") != file_sha256(out / "analysis_manifest.json"):
        raise ScopeGateError("chart manifest does not bind the current analysis manifest")
    for chart in chart_manifest.get("charts") or []:
        for item in [chart.get("png"), chart.get("chart_data"), *(chart.get("supplemental_data") or [])]:
            _verify_artifact(out, item, label="Chart artifact")
    validation.update(
        {
            "mode": context.gate["mode"],
            "scope_sha256": manifest["scope_sha256"],
            "plan_sha256": manifest["plan_sha256"],
            "study_sha256": manifest["study_sha256"],
            "query_through": manifest["query_through"],
            "data_through": manifest["data_through"],
            "charts": len(chart_manifest.get("charts") or []),
            "missing_chart_data": missing,
        }
    )
    return validation


def _report(config: ResearchConfig, context: StudyContext) -> dict[str, Any]:
    validation = _validate(config, context)
    out = context.output_dir
    findings = _read_json(out / "findings.json", label="findings")
    chart_manifest = _read_json(out / "chart_manifest.json", label="chart manifest")
    paths = render_report(findings, chart_manifest, validation=validation, output_dir=out)
    return {"report": str(paths[0]), "key_points": str(paths[1]), "validation": validation}


def _final_report(
    config: ResearchConfig,
    context: StudyContext,
    *,
    language: str | None = None,
    browser: str | Path | None = None,
    printer: Any = None,
) -> dict[str, Any]:
    from .final_report import FinalReportError, build_final_report

    validation = _validate(config, context)
    out = context.output_dir
    manifest = _read_json(out / "analysis_manifest.json", label="analysis manifest")
    chart_manifest = _read_json(out / "chart_manifest.json", label="chart manifest")
    if not (out / brief_check_module.BRIEF).is_file():
        raise FinalReportError(f"No {brief_check_module.BRIEF} in {out}; write the brief first (deliverables.md), then run report-pdf")
    check = brief_check_module.check_brief(out, manifest=manifest, chart_manifest=chart_manifest)
    if check["status"] != "pass":
        shown = "; ".join(
            f"line {item['line']}: {item['message']}" if item["line"] else item["message"] for item in check["errors"][:5]
        )
        more = f" (and {len(check['errors']) - 5} more)" if len(check["errors"]) > 5 else ""
        raise FinalReportError(
            f"{brief_check_module.BRIEF} failed brief-check with {len(check['errors'])} errors: {shown}{more}. "
            "Run `brief-check` for the full list."
        )
    result = build_final_report(
        out,
        manifest=manifest,
        validation=validation,
        findings=_read_json(out / "findings.json", label="findings"),
        chart_manifest=chart_manifest,
        language=language,
        browser=browser,
        printer=printer,
        brief_check=brief_check_module.summary(check),
    )
    return {**result, "validation": validation}


def _brief_check(config: ResearchConfig, context: StudyContext) -> dict[str, Any]:
    _validate(config, context)
    out = context.output_dir
    return brief_check_module.check_brief(
        out,
        manifest=_read_json(out / "analysis_manifest.json", label="analysis manifest"),
        chart_manifest=_read_json(out / "chart_manifest.json", label="chart manifest"),
    )


def _context(config: ResearchConfig, study_path: str | Path | None, replay_path: str | Path | None) -> StudyContext:
    if (study_path is None) == (replay_path is None):
        raise ScopeGateError("Pass exactly one of a study TOML or a replay TOML")
    return load_study_context(config, study_path) if study_path else load_replay_context(config, replay_path)


def analyze_study(config: ResearchConfig, study_path: str | Path | None = None, *, replay: str | Path | None = None) -> dict[str, Any]:
    """Analyze one approved study (or one labeled replay) into its own directory."""

    return _analyze(config, _context(config, study_path, replay))


def render_study_charts(config: ResearchConfig, study_path: str | Path | None = None, *, replay: str | Path | None = None) -> list[str]:
    """Render one chart per analysis, each with its chart-data CSV."""

    return _render_charts(config, _context(config, study_path, replay))


def validate_study_outputs(config: ResearchConfig, study_path: str | Path | None = None, *, replay: str | Path | None = None) -> dict[str, Any]:
    """Re-read serialized outputs, re-run the core QA checks, and verify every hash."""

    return _validate(config, _context(config, study_path, replay))


def report_study(config: ResearchConfig, study_path: str | Path | None = None, *, replay: str | Path | None = None) -> dict[str, Any]:
    """Validate, then write report.md and key_points.md from findings.json only."""

    return _report(config, _context(config, study_path, replay))


def final_report_study(
    config: ResearchConfig,
    study_path: str | Path | None = None,
    *,
    replay: str | Path | None = None,
    language: str | None = None,
    browser: str | Path | None = None,
    printer: Any = None,
) -> dict[str, Any]:
    """Validate, check brief.md, then render it plus an appendix into final_report.html and final_report.pdf."""

    return _final_report(config, _context(config, study_path, replay), language=language, browser=browser, printer=printer)


def brief_check_study(config: ResearchConfig, study_path: str | Path | None = None, *, replay: str | Path | None = None) -> dict[str, Any]:
    """Validate, then check brief.md against the validated outputs (the same check report-pdf runs)."""

    return _brief_check(config, _context(config, study_path, replay))
