"""Configuration and path handling for the research pipeline.

Why this module exists
----------------------
Earlier versions of this project embedded years, product lists, absolute paths,
and policy dates in many unrelated scripts.  That made two runs that looked
similar surprisingly hard to compare.  This module turns those choices into a
single, validated research specification.

No network calls or statistical calculations belong here.  The only side effect
is the optional creation of the project's data/output directories.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import tomllib
from typing import Any, Iterable


@dataclass(frozen=True)
class ProductSpec:
    """One product selected for detailed analysis.

    ``code`` is stored as text so a future HTS code with leading zeroes is not
    silently changed by a spreadsheet or integer conversion.
    """

    code: str
    name: str
    role: str = "focus"


@dataclass(frozen=True)
class StudyConfig:
    """Research-window and comparison choices."""

    annual_start_year: int
    annual_end_year: int
    monthly_start_year: int
    monthly_end_year: int
    ytd_end_month: int
    compare_base_year: int
    compare_year: int
    selected_chapters: tuple[str, ...]
    priority_origins: tuple[str, ...]


@dataclass(frozen=True)
class DataWebSettings:
    """Connection settings that are safe to persist.

    The bearer token is intentionally absent.  It is read only from the
    ``DATAWEB_TOKEN`` environment variable by :mod:`usitc_pipeline.dataweb`.
    """

    base_url: str
    timeout_seconds: int
    max_records: int
    retry_delays: tuple[int, ...]


@dataclass(frozen=True)
class ProjectPaths:
    """Resolved project paths.

    All paths are resolved relative to the project root, never the current
    working directory.  This makes ``python run.py ...`` behave the same from a
    terminal, Notebook, or scheduled process.
    """

    root: Path
    raw_dataweb: Path
    raw_fred: Path
    interim: Path
    processed: Path
    output_tables: Path
    output_charts: Path
    outputs: Path

    def create(self) -> None:
        """Create data/output directories without deleting existing files."""

        for path in (
            self.raw_dataweb,
            self.raw_fred,
            self.interim,
            self.processed,
            self.output_tables,
            self.output_charts,
            self.outputs,
        ):
            path.mkdir(parents=True, exist_ok=True)


@dataclass(frozen=True)
class ResearchConfig:
    """Complete, immutable configuration used by one pipeline run."""

    source_path: Path
    study: StudyConfig
    dataweb: DataWebSettings
    paths: ProjectPaths
    products: tuple[ProductSpec, ...]
    proxy_mapping_path: Path
    policy_events_path: Path

    @property
    def product_codes(self) -> tuple[str, ...]:
        """Return configured HTS product codes in file order."""

        return tuple(product.code for product in self.products)

    @property
    def product_names(self) -> dict[str, str]:
        """Return a convenient HTS-code-to-display-name mapping."""

        return {product.code: product.name for product in self.products}


def _as_tuple(values: Iterable[Any], *, cast=str) -> tuple[Any, ...]:
    return tuple(cast(value) for value in values)


def _resolve(root: Path, value: str | Path) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def _validate(config: ResearchConfig) -> None:
    study = config.study
    if study.annual_start_year > study.annual_end_year:
        raise ValueError("annual_start_year must not exceed annual_end_year")
    if study.monthly_start_year > study.monthly_end_year:
        raise ValueError("monthly_start_year must not exceed monthly_end_year")
    if not 1 <= study.ytd_end_month <= 12:
        raise ValueError("ytd_end_month must be between 1 and 12")
    if study.compare_base_year >= study.compare_year:
        raise ValueError("compare_base_year must be earlier than compare_year")
    if not study.selected_chapters:
        raise ValueError("At least one HTS chapter is required")
    if any(len(code) != 2 or not code.isdigit() for code in study.selected_chapters):
        raise ValueError("selected_chapters must contain two-digit strings")
    if not config.products:
        raise ValueError("At least one detailed product is required")
    if len({product.code for product in config.products}) != len(config.products):
        raise ValueError("Product HTS codes must be unique")
    if any(len(product.code) not in {2, 4, 6, 8, 10} or not product.code.isdigit() for product in config.products):
        raise ValueError("Product codes must be 2/4/6/8/10-digit numeric text")
    if not config.dataweb.base_url.lower().startswith("https://"):
        raise ValueError("DataWeb base_url must use HTTPS")
    if config.dataweb.timeout_seconds <= 0 or config.dataweb.max_records <= 0:
        raise ValueError("DataWeb timeout and max_records must be positive")


def load_config(path: str | Path | None = None, *, create_directories: bool = True) -> ResearchConfig:
    """Load and validate ``config/research.toml``.

    Parameters
    ----------
    path:
        TOML path.  When omitted, the package walks up from this file to the
        project root and uses ``config/research.toml``.
    create_directories:
        Create declared data/output directories.  Disable this in a read-only
        validation context.

    Returns
    -------
    ResearchConfig
        An immutable configuration with absolute, resolved paths.

    Raises
    ------
    FileNotFoundError
        If the TOML file or referenced CSV configuration is absent.
    ValueError
        If a year, month, HTS code, or endpoint is invalid.
    """

    default_root = Path(__file__).resolve().parents[2]
    source_path = Path(path).resolve() if path else default_root / "config" / "research.toml"
    with source_path.open("rb") as handle:
        raw = tomllib.load(handle)

    root = source_path.parent.parent.resolve()
    study_raw = raw.get("study", {})
    dataweb_raw = raw.get("dataweb", {})
    paths_raw = raw.get("paths", {})

    study = StudyConfig(
        annual_start_year=int(study_raw["annual_start_year"]),
        annual_end_year=int(study_raw["annual_end_year"]),
        monthly_start_year=int(study_raw["monthly_start_year"]),
        monthly_end_year=int(study_raw["monthly_end_year"]),
        ytd_end_month=int(study_raw["ytd_end_month"]),
        compare_base_year=int(study_raw["compare_base_year"]),
        compare_year=int(study_raw["compare_year"]),
        selected_chapters=_as_tuple(study_raw["selected_chapters"], cast=str),
        priority_origins=_as_tuple(study_raw.get("priority_origins", []), cast=str),
    )
    dataweb = DataWebSettings(
        base_url=str(dataweb_raw.get("base_url", "https://datawebws.usitc.gov/dataweb")).rstrip("/"),
        timeout_seconds=int(dataweb_raw.get("timeout_seconds", 240)),
        max_records=int(dataweb_raw.get("max_records", 20000)),
        retry_delays=_as_tuple(dataweb_raw.get("retry_delays", [0, 8, 20, 45]), cast=int),
    )

    outputs = _resolve(root, paths_raw.get("outputs", "outputs"))
    paths = ProjectPaths(
        root=root,
        raw_dataweb=_resolve(root, paths_raw.get("raw_dataweb", "data/raw/dataweb")),
        raw_fred=_resolve(root, paths_raw.get("raw_fred", "data/raw/fred")),
        interim=_resolve(root, paths_raw.get("interim", "data/interim")),
        processed=_resolve(root, paths_raw.get("processed", "data/processed")),
        output_tables=_resolve(root, paths_raw.get("output_tables", "outputs/tables")),
        output_charts=_resolve(root, paths_raw.get("output_charts", "outputs/charts")),
        outputs=outputs,
    )

    # Accept both TOML array-of-tables and a compact mapping.  This small amount
    # of flexibility makes the config readable while keeping the Python API
    # stable.
    product_raw = raw.get("products", [])
    if isinstance(product_raw, dict):
        products = tuple(
            ProductSpec(code=str(code), name=str(value), role="focus")
            for code, value in product_raw.items()
        )
    else:
        products = tuple(
            ProductSpec(
                code=str(item["code"]),
                name=str(item["name"]),
                role=str(item.get("role", "focus")),
            )
            for item in product_raw
        )

    proxy_mapping_path = _resolve(root, raw.get("external", {}).get("proxy_mapping", "config/hts_naics_proxy.csv"))
    policy_events_path = _resolve(root, raw.get("external", {}).get("policy_events", "config/policy_events.csv"))
    config = ResearchConfig(
        source_path=source_path,
        study=study,
        dataweb=dataweb,
        paths=paths,
        products=products,
        proxy_mapping_path=proxy_mapping_path,
        policy_events_path=policy_events_path,
    )
    _validate(config)
    for required in (proxy_mapping_path, policy_events_path):
        if not required.exists():
            raise FileNotFoundError(required)
    if create_directories:
        paths.create()
    return config


def read_csv_records(path: str | Path) -> list[dict[str, str]]:
    """Read a small configuration CSV without introducing pandas here."""

    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))

