"""Stage orchestration for the original research.toml baseline workflow.

The orchestration imports Python functions directly; it does not launch a chain
of versioned scripts through ``subprocess``.  Every stage can therefore be run
and tested independently:

``Config → acquire/import → normalize → analyze → validate → charts``

The command-line parser lives in :mod:`usitc_pipeline.cli`.  New products are
researched through an approved scope and a query plan (:mod:`usitc_pipeline.plan`);
the baseline acquisition here requires an explicit ``legacy_baseline=True``.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
from pathlib import Path
import json
import platform
import shutil
from typing import Any, Iterable

import numpy as np
import pandas as pd

from . import __version__
from .analysis import (
    add_period_status,
    audit_period_coverage,
    build_domestic_production_screen,
    build_product_reallocation,
    build_trade_panel,
    safe_divide,
    validate_trade_panel,
)
from .charts import (
    _plt,
    _prepare_output,
    plot_domestic_production_screen,
    plot_product_reallocation,
    validate_chart_audit_files,
)
from .config import ResearchConfig
from .dataweb import (
    MAX_YEARS_PER_QUERY,
    DataWebClient,
    QueryJob,
    QuerySpec,
    RawSnapshot,
    chunked,
    file_sha256,
    response_summary,
)
from .fred import acquire_fred_series, parse_fred_csv
from .normalize import normalize_dataweb_files
from .scope import ScopeGateError, utc_now, write_json_atomic


LEGACY_ACQUISITION_MESSAGE = (
    "Legacy acquisition builds DataWeb queries from research.toml without an approved HTS scope. "
    "For new research, freeze a scope, then run `plan --study <file>` and `acquire --plan <query_plan.json>`. "
    "Pass --legacy-baseline only to rebuild the original research.toml baseline."
)

# Original baseline palette, kept so the reproduction looks like the original.
COLORS = {
    "China": "#C84A3A",
    "Vietnam": "#E28E2C",
    "Mexico": "#2F6B9A",
    "Taiwan": "#7A5195",
    "Thailand": "#59A14F",
    "India": "#EDC949",
    "Other reported origins": "#A0A0A0",
    "World": "#1F3B5B",
}


def _source_note(fig, text: str) -> None:
    fig.text(0.01, 0.01, text, ha="left", va="bottom", fontsize=7, color="#555555")

def _json_ready(value: Any) -> Any:
    """Recursively convert pandas/numpy/path values to strict JSON values."""

    if isinstance(value, dict):
        return {str(key): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if value is pd.NA or (isinstance(value, (float, np.floating)) and np.isnan(value)):
        return None
    if isinstance(value, np.generic):
        return value.item()
    return value


def _write_json(path: Path, value: Any) -> Path:
    return write_json_atomic(path, _json_ready(value), allow_nan=False)


def _read_table(path: Path, **kwargs: Any) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Required pipeline table is missing: {path}")
    return pd.read_csv(path, **kwargs)


def _current_raw_json(config: ResearchConfig) -> list[Path]:
    prefixes = ("longrun_hts2_", "focus_hts6_", "laptop_hts6_monthly_")
    return sorted(
        path
        for path in config.paths.raw_dataweb.glob("*.json")
        if path.name.startswith(prefixes)
        and not path.name.endswith(".meta.json")
        and ".superseded." not in path.name
    )


def _load_cached_fred(config: ResearchConfig, series_ids: Iterable[str]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for series_id in dict.fromkeys(map(str, series_ids)):
        csv_path = config.paths.raw_fred / f"{series_id}.csv"
        meta_path = config.paths.raw_fred / f"{series_id}.meta.json"
        if not csv_path.exists() or not meta_path.exists():
            continue
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        if metadata.get("response_sha256") != file_sha256(csv_path):
            raise ValueError(f"FRED cache hash mismatch: {csv_path}")
        frames.append(
            parse_fred_csv(
                csv_path.read_text(encoding="utf-8"),
                series_id=series_id,
                retrieved_at=str(metadata["retrieved_at_utc"]),
            )
        )
    if not frames:
        return pd.DataFrame(columns=["series_id", "date", "value", "retrieved_at"])
    return pd.concat(frames, ignore_index=True)


def acquire(config: ResearchConfig, *, refresh: bool = False, legacy_baseline: bool = False) -> dict[str, Any]:
    """Acquire the baseline DataWeb and FRED raw snapshots.

    The environment is never serialized.  Only the DataWeb client reads
    ``DATAWEB_TOKEN``; FRED requires no key.  The baseline queries come from
    research.toml rather than an approved scope, so the caller must pass
    ``legacy_baseline=True`` explicitly.
    """

    dataweb = acquire_all(config, refresh=refresh, legacy_baseline=legacy_baseline)
    proxy = pd.read_csv(config.proxy_mapping_path, dtype={"hts_code": "string", "naics": "string"})
    fred = acquire_fred_series(config, proxy["series_id"].dropna(), refresh=refresh)
    fred.to_csv(config.paths.processed / "fred_series.csv", index=False)
    result = {
        "completed_at_utc": utc_now(),
        "refresh": refresh,
        "dataweb": [
            {
                "path": str(item.path),
                "metadata_path": str(item.metadata_path),
                "status": item.status,
                "sha256": item.sha256,
                "row_count": item.row_count,
            }
            for item in dataweb
        ],
        "fred_series": sorted(fred["series_id"].unique().tolist()) if not fred.empty else [],
    }
    _write_json(config.paths.raw_dataweb / "acquisition_manifest.json", result)
    return result


def _copy_legacy_file(source: Path, destination: Path) -> str:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if file_sha256(source) == file_sha256(destination):
            return "identical_existing"
        raise FileExistsError(
            f"Refusing to overwrite a different imported snapshot: {destination}. "
            "Move it aside or start from a clean project directory."
        )
    shutil.copy2(source, destination)
    return "copied"


def import_legacy(config: ResearchConfig, source_root: str | Path) -> dict[str, Any]:
    """Import reusable raw snapshots without modifying the historical project.

    Only the focus-product and laptop DataWeb JSON files are copied.  The old
    long-run processed CSV is imported separately because its country extract
    contained only China, Mexico, Vietnam, and World; it is explicitly labeled
    as limited-scope rather than renamed as an all-origin extract.
    """

    source_root = Path(source_root).resolve()
    old_raw = source_root / "work" / "revised_analysis_v4" / "data" / "raw"
    if not old_raw.exists():
        raise FileNotFoundError(f"Legacy raw directory not found: {old_raw}")
    selected = sorted(old_raw.glob("focus_hts6_*.json")) + sorted(
        old_raw.glob("laptop_hts6_monthly_*.json")
    )
    imported: list[dict[str, Any]] = []
    for source in selected:
        destination = config.paths.raw_dataweb / source.name
        status = _copy_legacy_file(source, destination)
        raw = json.loads(destination.read_text(encoding="utf-8"))
        summary = response_summary(raw)
        metadata = {
            "imported_at_utc": utc_now(),
            "legacy_source": str(source),
            "response_sha256": file_sha256(destination),
            "response_summary": summary,
            "payload_sha256": None,
            "cache_note": "Imported snapshot; original payload hash was not recorded by the legacy script.",
        }
        _write_json(destination.with_suffix(".meta.json"), metadata)
        imported.append({"source": str(source), "destination": str(destination), "status": status})

    old_longrun = source_root / "work" / "next_plan" / "longrun" / "hts2_origin_annual_1989_2026.csv"
    if old_longrun.exists():
        destination = config.paths.interim / "legacy_longrun_three_origins.csv"
        status = _copy_legacy_file(old_longrun, destination)
        imported.append(
            {
                "source": str(old_longrun),
                "destination": str(destination),
                "status": status,
                "scope": "China, Mexico, Vietnam, and World only",
            }
        )

    manifest = {
        "imported_at_utc": utc_now(),
        "source_root": str(source_root),
        "files": imported,
        "important_limit": (
            "Imported long-run origin data covers China, Mexico, Vietnam, and World only. "
            "A fresh clean DataWeb acquisition uses all reported origins plus an independent World query."
        ),
    }
    _write_json(config.paths.interim / "legacy_import_manifest.json", manifest)
    return manifest


def _legacy_longrun_table(config: ResearchConfig) -> pd.DataFrame:
    source = config.paths.interim / "legacy_longrun_three_origins.csv"
    if not source.exists():
        return pd.DataFrame()
    raw = pd.read_csv(source, dtype={"chapter": "string", "country": "string"})
    raw = raw[pd.to_numeric(raw["year"], errors="coerce").le(config.study.annual_end_year)].copy()
    raw["customs_value"] = pd.to_numeric(raw["customs_value"], errors="coerce")
    raw["world_customs_value"] = pd.to_numeric(raw["world_customs_value"], errors="coerce")
    non_world = raw[~raw["country"].eq("World")]
    by_origin = non_world.groupby(["year", "country"], as_index=False).agg(
        customs_value_usd=("customs_value", "sum"),
        world_value_usd=("world_customs_value", "sum"),
    )
    by_origin = by_origin.rename(columns={"country": "origin_name"})
    by_origin["value_share"] = by_origin["customs_value_usd"] / by_origin["world_value_usd"].replace(0, np.nan)
    world = raw[raw["country"].eq("World")].groupby("year", as_index=False).agg(
        customs_value_usd=("customs_value", "sum")
    )
    world["world_value_usd"] = world["customs_value_usd"]
    world["origin_name"] = "World"
    world["value_share"] = 1.0
    result = pd.concat([by_origin, world[by_origin.columns]], ignore_index=True)
    result["data_scope"] = "legacy_three_origins_plus_world"
    return result.sort_values(["year", "origin_name"]).reset_index(drop=True)


def analyze(config: ResearchConfig) -> dict[str, Any]:
    """Normalize raw snapshots, calculate research tables, and run strict QA."""

    raw_files = _current_raw_json(config)
    if not raw_files:
        raise FileNotFoundError(
            f"No DataWeb snapshots found under {config.paths.raw_dataweb}. "
            "Run acquire or import-legacy first."
        )
    tidy = normalize_dataweb_files(raw_files)
    tidy.to_csv(config.paths.interim / "trade_tidy.csv", index=False)

    panel = build_trade_panel(tidy)
    panel = add_period_status(
        panel,
        ytd_end_month=config.study.ytd_end_month,
        ytd_year=config.study.monthly_end_year,
    )
    validation = validate_trade_panel(panel, strict=True)
    panel.to_csv(config.paths.processed / "trade_panel.csv", index=False)

    if (panel["hts_level"] == 2).any():
        longrun = build_longrun_structure(panel, priority_origins=config.study.priority_origins)
        longrun["data_scope"] = "fresh_all_reported_origins_plus_world"
    else:
        longrun = _legacy_longrun_table(config)
    longrun.to_csv(config.paths.output_tables / "longrun_origin_shares.csv", index=False)

    focus = build_product_reallocation(
        panel[panel["hts_level"].eq(6)],
        base_year=config.study.compare_base_year,
        compare_year=config.study.compare_year,
    )
    # Replace verbose customs descriptions with the concise, versioned display
    # names declared in research.toml.  The raw description remains available
    # in trade_panel.csv; charts no longer clip 80-character labels.
    if not focus.empty:
        focus["product"] = focus["hts_code"].astype(str).map(config.product_names).fillna(focus["product"])
    focus.to_csv(config.paths.output_tables / "product_reallocation.csv", index=False)

    laptop_monthly, laptop_metrics = build_laptop_case(
        panel,
        priority_origins=config.study.priority_origins,
        ytd_year=config.study.monthly_end_year,
        ytd_end_month=config.study.ytd_end_month,
    )
    laptop_monthly.to_csv(config.paths.output_tables / "laptop_monthly_panel.csv", index=False)
    _write_json(config.paths.output_tables / "laptop_metrics.json", laptop_metrics)

    focus_tidy = tidy[tidy["frequency"].eq("annual") & tidy["hts_level"].eq(6)]
    focus_coverage = audit_period_coverage(
        focus_tidy,
        expected_periods=[str(config.study.compare_base_year), str(config.study.compare_year)],
    )
    focus_coverage.to_csv(config.paths.output_tables / "focus_coverage_audit.csv", index=False)
    laptop_expected = pd.period_range(
        f"{config.study.monthly_start_year}-01",
        f"{config.study.monthly_end_year}-{config.study.ytd_end_month:02d}",
        freq="M",
    ).astype(str)
    laptop_coverage = audit_period_coverage(
        tidy[tidy["frequency"].eq("monthly") & tidy["hts_code"].eq("847130")],
        expected_periods=laptop_expected,
    )
    laptop_coverage.to_csv(config.paths.output_tables / "laptop_coverage_audit.csv", index=False)

    proxy_mapping = pd.read_csv(
        config.proxy_mapping_path,
        dtype={"hts_code": "string", "naics": "string", "series_id": "string"},
    )
    fred = _load_cached_fred(config, proxy_mapping["series_id"].dropna())
    if not fred.empty:
        fred.to_csv(config.paths.processed / "fred_series.csv", index=False)
        production = build_domestic_production_screen(
            fred,
            proxy_mapping,
            base_year=config.study.compare_base_year,
            compare_year=config.study.compare_year,
        )
    else:
        # Preserve a readable schema even when the optional FRED cache has not
        # been acquired yet.  A header-only CSV is much safer for downstream
        # readers than pandas' two-byte blank-file representation.
        production = pd.DataFrame(
            columns=list(proxy_mapping.columns)
            + [
                "base_year",
                "compare_year",
                "base_index",
                "compare_index",
                "index_change_pct",
                "source",
                "inference_limit",
            ]
        )
    production.to_csv(config.paths.output_tables / "domestic_production_screen.csv", index=False)

    findings = {
        "generated_at_utc": utc_now(),
        "measurement": {
            "trade_flow": "U.S. imports for consumption",
            "value": "nominal customs value in actual U.S. dollars",
            "quantity": "DataWeb first unit; used only with consistent product-level units",
            "realized_duty": "calculated duties divided by customs value; not statutory tariff",
        },
        "longrun": {
            "available": not longrun.empty,
            "start_year": int(longrun["year"].min()) if not longrun.empty else None,
            "end_year": int(longrun["year"].max()) if not longrun.empty else None,
            "data_scope": sorted(longrun["data_scope"].dropna().unique().tolist()) if "data_scope" in longrun else [],
        },
        "product_reallocation": focus.to_dict("records"),
        "laptop_case": laptop_metrics,
        "domestic_production_screen": production.to_dict("records"),
        "interpretation_boundaries": [
            "Country/area of origin is not value-added origin.",
            "A gaining source is not proof of direct relocation from China.",
            "Policy-aligned timing is descriptive unless a causal design is added.",
            "FRED mappings are broad domestic-production plausibility screens.",
        ],
    }
    _write_json(config.paths.outputs / "findings.json", findings)
    write_run_manifest(config, stage="analyze", validation=validation)
    return {
        "raw_files": len(raw_files),
        "tidy_rows": len(tidy),
        "panel_rows": len(panel),
        "longrun_rows": len(longrun),
        "product_rows": len(focus),
        "laptop_rows": len(laptop_monthly),
        "fred_rows": len(fred),
        "validation": validation,
    }


def render_charts(config: ResearchConfig) -> list[str]:
    """Render all available charts from final tables only."""

    generated: list[Path] = []
    longrun = _read_table(config.paths.output_tables / "longrun_origin_shares.csv")
    focus = _read_table(config.paths.output_tables / "product_reallocation.csv", dtype={"hts_code": "string"})
    laptop = _read_table(config.paths.output_tables / "laptop_monthly_panel.csv", dtype={"period": "string"})
    events = pd.read_csv(config.policy_events_path)
    events = events.rename(columns={"event_date": "date", "event_label": "short_label"})

    if not longrun.empty:
        generated.append(plot_longrun_structure(longrun, config.paths.output_charts))
    if not focus.empty:
        generated.append(plot_product_reallocation(focus, config.paths.output_charts))
    if not laptop.empty:
        generated.append(plot_laptop_event_path(laptop, events, config.paths.output_charts))
    production_path = config.paths.output_tables / "domestic_production_screen.csv"
    if production_path.exists() and production_path.stat().st_size > 1:
        try:
            production = pd.read_csv(production_path)
        except pd.errors.EmptyDataError:
            production = pd.DataFrame()
        if not production.empty:
            generated.append(plot_domestic_production_screen(production, config.paths.output_charts))

    missing = validate_chart_audit_files(config.paths.output_charts)
    if missing:
        raise ValueError(f"Charts missing same-stem audit CSVs: {missing}")
    write_run_manifest(config, stage="charts", validation={"ok": True, "missing_chart_data": []})
    return [str(path) for path in generated]


def validate_outputs(config: ResearchConfig) -> dict[str, Any]:
    """Re-run critical checks on serialized outputs."""

    panel = _read_table(
        config.paths.processed / "trade_panel.csv",
        dtype={"period": "string", "hts_code": "string", "origin_name": "string"},
    )
    validation = validate_trade_panel(panel, strict=True)
    fred_path = config.paths.processed / "fred_series.csv"
    if fred_path.exists():
        fred = pd.read_csv(fred_path)
        required_fred = {"series_id", "date", "value", "retrieved_at"}
        missing_fred = sorted(required_fred - set(fred.columns))
        if missing_fred:
            raise ValueError(f"FRED processed table lacks provenance columns: {missing_fred}")
        if fred["series_id"].isna().any() or fred["retrieved_at"].isna().any():
            raise ValueError("FRED series_id/retrieved_at contains missing values")
        validation["fred_provenance_ok"] = True
        validation["fred_series"] = sorted(fred["series_id"].astype(str).unique().tolist())
    missing_chart_data = validate_chart_audit_files(config.paths.output_charts)
    if missing_chart_data:
        raise ValueError(f"Charts missing same-stem audit CSVs: {missing_chart_data}")
    validation["missing_chart_data"] = missing_chart_data
    write_run_manifest(config, stage="validate", validation=validation)
    return validation


def write_run_manifest(
    config: ResearchConfig,
    *,
    stage: str,
    validation: dict[str, Any],
) -> Path:
    """Write a redacted, hash-based provenance manifest.

    Only declared configuration is serialized.  Process environment variables
    are never included, preventing accidental persistence of ``DATAWEB_TOKEN``.
    """

    dataweb_raw = _current_raw_json(config)
    dataweb_metadata = [path.with_suffix(".meta.json") for path in dataweb_raw]
    fred_raw = sorted(config.paths.raw_fred.glob("*.csv")) + sorted(config.paths.raw_fred.glob("*.meta.json"))
    input_files = (
        dataweb_raw
        + dataweb_metadata
        + fred_raw
        + [config.source_path, config.proxy_mapping_path, config.policy_events_path]
    )
    output_files = sorted(
        path
        for root in (config.paths.processed, config.paths.output_tables, config.paths.output_charts)
        for path in root.glob("*")
        if path.is_file()
    )
    manifest = {
        "generated_at_utc": utc_now(),
        "stage": stage,
        "pipeline_version": __version__,
        "python": platform.python_version(),
        "config": {
            "study": asdict(config.study),
            "dataweb": asdict(config.dataweb),
            "products": [asdict(item) for item in config.products],
            "config_file_sha256": file_sha256(config.source_path),
        },
        "inputs": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in input_files
            if path.exists()
        ],
        "outputs": [
            {"path": str(path), "bytes": path.stat().st_size, "sha256": file_sha256(path)}
            for path in output_files
        ],
        "validation": validation,
        "secrets_recorded": False,
    }
    return _write_json(config.paths.outputs / "run_manifest.json", manifest)


# --- Baseline-only acquisition, analysis and charts -----------------------
# These reproduce the original research.toml (laptop) baseline.  The
# scope-driven study path never calls them.


def build_default_query_plan(config: ResearchConfig) -> list[QueryJob]:
    """Build the complete acquisition plan used by this research.

    Long-run years are chunked to reduce API complexity.  Focus products and the
    laptop event study are queried once with origins broken out and once as an
    independent World aggregate.  This dual-query design makes reconciliation
    possible instead of defining "World" as whatever rows happened to download.
    """

    jobs: list[QueryJob] = []
    study = config.study
    for years in chunked(range(study.annual_start_year, study.annual_end_year + 1), MAX_YEARS_PER_QUERY):
        for breakout, label in ((True, "all"), (False, "world")):
            jobs.append(
                QueryJob(
                    cache_stem=f"longrun_hts2_{label}_{years[0]}_{years[-1]}",
                    spec=QuerySpec(
                        hts_level=2,
                        codes=study.selected_chapters,
                        years=years,
                        frequency="annual",
                        break_out_origins=breakout,
                    ),
                )
            )

    for year in (study.compare_base_year, study.compare_year):
        for breakout, label in ((True, "all"), (False, "world")):
            jobs.append(
                QueryJob(
                    cache_stem=f"focus_hts6_{label}_{year}",
                    spec=QuerySpec(
                        hts_level=6,
                        codes=config.product_codes,
                        years=(year,),
                        frequency="annual",
                        break_out_origins=breakout,
                    ),
                )
            )

    for year in range(study.monthly_start_year, study.monthly_end_year + 1):
        for breakout, label in ((True, "all"), (False, "world")):
            jobs.append(
                QueryJob(
                    cache_stem=f"laptop_hts6_monthly_{label}_{year}",
                    spec=QuerySpec(
                        hts_level=6,
                        codes=("847130",),
                        years=(year,),
                        frequency="monthly",
                        break_out_origins=breakout,
                    ),
                )
            )
    return jobs


def acquire_all(
    config: ResearchConfig,
    *,
    refresh: bool = False,
    legacy_baseline: bool = False,
) -> list[RawSnapshot]:
    """Acquire the original research.toml baseline plan.

    New research goes through an approved scope (``plan`` + ``acquire
    --plan``).  This path exists only to rebuild the original baseline, so it
    refuses to run unless ``legacy_baseline=True`` is passed explicitly.
    """

    if not legacy_baseline:
        raise ScopeGateError(LEGACY_ACQUISITION_MESSAGE)
    client = DataWebClient(config)
    return [client.fetch(job, refresh=refresh) for job in build_default_query_plan(config)]

def build_longrun_structure(
    panel: pd.DataFrame,
    *,
    priority_origins: Iterable[str] = (),
) -> pd.DataFrame:
    """Aggregate HTS2 annual imports into a long-run sourcing table.

    Quantity is deliberately absent: summing first units across machinery,
    electronics, vehicles, furniture, and toys would combine unlike units.
    """

    annual = panel[(panel["frequency"] == "annual") & (panel["hts_level"] == 2)].copy()
    if annual.empty:
        return pd.DataFrame(
            columns=["year", "origin_name", "customs_value_usd", "world_value_usd", "value_share"]
        )
    annual["year"] = pd.to_numeric(annual["period"], errors="coerce").astype("Int64")
    by_origin = annual.groupby(["year", "origin_name"], as_index=False).agg(
        customs_value_usd=("customs_value_usd", "sum")
    )
    by_product_world = annual[
        ["year", "hts_code", "world_value_usd"]
    ].drop_duplicates(["year", "hts_code"])
    totals = by_product_world.groupby("year", as_index=False).agg(world_value_usd=("world_value_usd", "sum"))
    result = by_origin.merge(totals, on="year", how="left", validate="many_to_one")
    result["value_share"] = safe_divide(result["customs_value_usd"], result["world_value_usd"])

    selected = tuple(priority_origins)
    if selected:
        keep = result["origin_name"].isin(selected)
        other = result.loc[~keep].groupby("year", as_index=False).agg(
            customs_value_usd=("customs_value_usd", "sum"),
            world_value_usd=("world_value_usd", "first"),
        )
        other["origin_name"] = "Other reported origins"
        other["value_share"] = safe_divide(other["customs_value_usd"], other["world_value_usd"])
        result = pd.concat([result.loc[keep], other[result.columns]], ignore_index=True)

    world_rows = totals.rename(columns={"world_value_usd": "customs_value_usd"})
    world_rows["world_value_usd"] = world_rows["customs_value_usd"]
    world_rows["origin_name"] = "World"
    world_rows["value_share"] = 1.0
    result = pd.concat([result, world_rows[result.columns]], ignore_index=True)
    return result.sort_values(["year", "origin_name"], kind="stable").reset_index(drop=True)


def build_laptop_case(
    panel: pd.DataFrame,
    *,
    hts_code: str = "847130",
    priority_origins: Iterable[str] = ("China", "Vietnam", "Mexico", "Taiwan", "Thailand", "India"),
    ytd_year: int | None = None,
    ytd_end_month: int = 12,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Build the continuous portable-computer event path and compact metrics.

    Event windows are transparent calendar summaries, not a causal event-study
    estimator.  The method preserves the asymmetric pattern that motivated the
    research: rapid exit after the 2025 shock versus slower partial return.
    """

    laptop = panel[
        panel["hts_code"].astype(str).eq(str(hts_code)) & panel["frequency"].eq("monthly")
    ].copy()
    if laptop.empty:
        return laptop, {"available": False, "reason": "No monthly laptop rows"}
    laptop["period_date"] = pd.to_datetime(laptop["period"].astype(str) + "-01", errors="coerce")
    laptop = laptop[laptop["period_date"].notna()].copy()
    if ytd_year is not None and ytd_end_month < 12:
        cutoff = pd.Timestamp(year=int(ytd_year), month=int(ytd_end_month), day=1)
        laptop = laptop[laptop["period_date"].le(cutoff)].copy()
    keep = set(priority_origins)
    laptop["display_origin"] = np.where(laptop["origin_name"].isin(keep), laptop["origin_name"], "Other reported origins")
    monthly = laptop.groupby(["period", "period_date", "display_origin"], as_index=False).agg(
        customs_value_usd=("customs_value_usd", "sum"),
        quantity=("quantity", "sum"),
        world_value_usd=("world_value_usd", "first"),
        world_quantity=("world_quantity", "first"),
    )
    monthly["value_share"] = safe_divide(monthly["customs_value_usd"], monthly["world_value_usd"])
    monthly["quantity_share"] = safe_divide(monthly["quantity"], monthly["world_quantity"])
    monthly["unit_value_usd"] = safe_divide(monthly["customs_value_usd"], monthly["quantity"])
    # Blank future months in a calendar-year DataWeb response can be represented
    # as all missing values.  GroupBy would otherwise turn those into zeros and
    # draw a false collapse after the true data cutoff.
    monthly = monthly[monthly["world_value_usd"].fillna(0).gt(0)].copy()

    china = monthly[monthly["display_origin"].eq("China")].sort_values("period_date").copy()
    if china.empty:
        return monthly, {"available": False, "reason": "No China laptop rows"}

    def weighted_share(start: str, end: str) -> float:
        window = china[china["period_date"].between(pd.Timestamp(start), pd.Timestamp(end))]
        return float(safe_divide(window["customs_value_usd"].sum(), window["world_value_usd"].sum()) * 100)

    baseline_share = weighted_share("2024-11-01", "2025-01-01")
    shock = china[china["period_date"].between("2025-02-01", "2025-11-01")]
    recovery = china[china["period_date"].between("2026-03-01", "2026-08-01")]
    floor_row = shock.loc[shock["value_share"].idxmin()] if not shock.empty else None
    peak_row = recovery.loc[recovery["value_share"].idxmax()] if not recovery.empty else None
    floor_share = float(floor_row["value_share"] * 100) if floor_row is not None else float("nan")
    peak_share = float(peak_row["value_share"] * 100) if peak_row is not None else float("nan")

    metrics: dict[str, Any] = {
        "available": True,
        "hts_code": hts_code,
        "baseline_window": "2024-11 through 2025-01",
        "baseline_china_value_share_pct": baseline_share,
        "post_tariff_floor_month": floor_row["period"] if floor_row is not None else None,
        "post_tariff_floor_share_pct": floor_share,
        "exit_from_baseline_pp": floor_share - baseline_share,
        "recovery_window": "2026-03 through 2026-08",
        "recovery_peak_month": peak_row["period"] if peak_row is not None else None,
        "recovery_peak_share_pct": peak_share,
        "recovery_from_floor_pp": peak_share - floor_share,
        "interpretation": "descriptive_fast_exit_slow_partial_return",
    }

    # COVID comparison: changes in monthly China share slope before and during
    # the disruption.  It is a timing diagnostic only; no untreated causal
    # counterfactual is claimed.
    def slope(start: str, end: str) -> float:
        window = china[china["period_date"].between(start, end)].dropna(subset=["value_share"])
        if len(window) < 2:
            return float("nan")
        return float(np.polyfit(np.arange(len(window)), window["value_share"] * 100, 1)[0])

    metrics["pre_covid_share_slope_pp_per_month"] = slope("2018-01-01", "2019-12-01")
    metrics["covid_share_slope_pp_per_month"] = slope("2020-03-01", "2021-12-01")
    return monthly.sort_values(["period_date", "display_origin"], kind="stable").reset_index(drop=True), metrics

def plot_longrun_structure(longrun: pd.DataFrame, output_dir: str | Path) -> Path:
    """Plot total imports and selected source-origin shares since 1989."""

    if longrun.empty:
        raise ValueError("Cannot plot an empty long-run table")
    png, _ = _prepare_output(output_dir, "01_longrun_structure", longrun)
    plt = _plt()
    fig, (ax_total, ax_share) = plt.subplots(2, 1, figsize=(11.2, 7.2), sharex=True, gridspec_kw={"height_ratios": [1, 1.35]})

    world = longrun[longrun["origin_name"].eq("World")].sort_values("year")
    ax_total.plot(world["year"], world["customs_value_usd"] / 1e12, color=COLORS["World"], linewidth=2.3)
    ax_total.set_ylabel("Imports ($tn)")
    ax_total.set_title("U.S. imports: scale first, sourcing structure second", loc="left")
    ax_total.grid(axis="y", alpha=0.22)

    origins = longrun[~longrun["origin_name"].eq("World")]
    for origin, group in origins.groupby("origin_name", sort=False):
        group = group.sort_values("year")
        ax_share.plot(
            group["year"],
            group["value_share"] * 100,
            label=origin,
            color=COLORS.get(origin),
            linewidth=2 if origin != "Other reported origins" else 1.4,
        )
    ax_share.set_ylabel("Share of selected-chapter imports (%)")
    ax_share.set_xlabel("Calendar year")
    ax_share.grid(axis="y", alpha=0.22)
    ax_share.legend(ncol=4, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.16))
    _source_note(
        fig,
        "Source: USITC DataWeb, imports for consumption, nominal customs value. "
        "2026 is excluded from the annual long-run chart because the configured series ends in 2025.",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(png)
    plt.close(fig)
    return png


def plot_laptop_event_path(
    monthly: pd.DataFrame,
    events: pd.DataFrame,
    output_dir: str | Path,
) -> Path:
    """Plot portable-computer sourcing shares with event markers."""

    if monthly.empty:
        raise ValueError("Cannot plot an empty laptop event table")
    data = monthly.copy()
    data["period_date"] = pd.to_datetime(data["period_date"], errors="coerce")
    png, _ = _prepare_output(output_dir, "03_laptop_event_path", data)
    event_csv = Path(output_dir) / "03_laptop_event_path.events.csv"
    events.to_csv(event_csv, index=False)

    plt = _plt()
    fig, (ax_share, ax_world) = plt.subplots(2, 1, figsize=(11.5, 7.4), sharex=True, gridspec_kw={"height_ratios": [1.6, 0.8]})
    for origin, group in data.groupby("display_origin", sort=False):
        group = group.sort_values("period_date")
        if origin == "Other reported origins":
            continue
        ax_share.plot(
            group["period_date"],
            group["value_share"] * 100,
            label=origin,
            color=COLORS.get(origin),
            linewidth=2.0 if origin in {"China", "Vietnam", "Mexico"} else 1.2,
        )
    world = data[["period_date", "world_value_usd", "world_quantity"]].drop_duplicates("period_date").sort_values("period_date")
    ax_world.plot(world["period_date"], world["world_value_usd"] / 1e9, color=COLORS["World"], linewidth=1.8)

    plotted_events = events.copy()
    plotted_events["date"] = pd.to_datetime(plotted_events["date"], errors="coerce")
    plotted_events = plotted_events[plotted_events["date"].between(data["period_date"].min(), data["period_date"].max())]
    for index, row in plotted_events.reset_index(drop=True).iterrows():
        color = "#C84A3A" if str(row.get("status", "")).lower() == "implemented" else "#888888"
        for axis in (ax_share, ax_world):
            axis.axvline(row["date"], color=color, linestyle="--", linewidth=0.85, alpha=0.7)
        if index < 6:  # Keep the slide readable; all events remain in the CSV.
            ax_share.text(
                row["date"],
                ax_share.get_ylim()[1],
                str(row.get("short_label", row.get("event_name", "event"))),
                rotation=90,
                va="top",
                ha="right",
                fontsize=7,
                color=color,
            )

    ax_share.set_ylabel("Share of U.S. laptop import value (%)")
    ax_share.set_title("Portable computers: fast exit, then a slower partial return", loc="left")
    ax_share.legend(ncol=6, fontsize=8, loc="upper center", bbox_to_anchor=(0.5, -0.08))
    ax_share.grid(axis="y", alpha=0.2)
    ax_world.set_ylabel("Total imports ($bn)")
    ax_world.set_xlabel("Month")
    ax_world.grid(axis="y", alpha=0.2)
    _source_note(
        fig,
        "Source: USITC DataWeb, HTS 847130, imports for consumption. Event lines distinguish implemented policy, "
        "announced-but-not-imposed threats, and COVID context; the chart is descriptive.",
    )
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(png)
    plt.close(fig)
    return png

def _print_result(value: Any) -> None:
    print(json.dumps(_json_ready(value), ensure_ascii=False, indent=2, allow_nan=False))


def main(argv: list[str] | None = None) -> None:
    """Backward-compatible entry point; the parser lives in :mod:`usitc_pipeline.cli`."""

    from .cli import main as cli_main

    raise SystemExit(cli_main(argv))


if __name__ == "__main__":
    main()

