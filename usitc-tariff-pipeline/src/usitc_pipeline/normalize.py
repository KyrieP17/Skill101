"""Normalize DataWeb's presentation-oriented JSON into one tidy trade schema.

DataWeb returns one table per measure, nested column headers, and row values as
formatted strings.  Earlier scripts each parsed that structure differently and
sometimes assumed that table order or the first row group was stable.  This
module keeps one defensive parser and names measures from their tab labels.

Canonical output columns
------------------------
``period, frequency, hts_level, hts_code, hts_description, origin_name,
customs_value_usd, quantity, quantity_unit, calculated_duty_usd, source_file``

Country/area of origin is a customs classification.  It must not be interpreted
as the country that supplied every component or all value added.
"""

from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Any, Iterable, Mapping

import numpy as np
import pandas as pd


MONTH_NAMES = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)
MONTH_NUMBER = {name: number for number, name in enumerate(MONTH_NAMES, start=1)}

CANONICAL_COLUMNS = [
    "period",
    "frequency",
    "hts_level",
    "hts_code",
    "hts_description",
    "origin_name",
    "customs_value_usd",
    "quantity",
    "quantity_unit",
    "calculated_duty_usd",
    "source_file",
]
# Present only when a snapshot requested the second unit of quantity.
SECOND_QUANTITY_COLUMNS = ["quantity_2", "quantity_2_unit"]


def clean_number(value: Any) -> float:
    """Convert one DataWeb numeric cell to ``float`` without inventing zeroes.

    Missing markers and unparseable text become ``NaN``.  Parentheses are
    treated as accounting negatives, while currency symbols, commas, percent
    signs, and surrounding whitespace are removed.

    The parser intentionally does *not* coerce invalid values to zero: doing so
    would manufacture trade, quantity, or duty observations.
    """

    if value is None or (isinstance(value, float) and np.isnan(value)):
        return float("nan")
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    text = str(value).strip()
    if text.lower() in {"", "--", "n/a", "na", "nan", "none", "null"}:
        return float("nan")
    negative = text.startswith("(") and text.endswith(")")
    if negative:
        text = text[1:-1]
    text = text.replace(",", "").replace("$", "").replace("%", "").strip()
    try:
        number = float(text)
    except ValueError:
        return float("nan")
    return -number if negative else number


def _column_labels(table: Mapping[str, Any]) -> list[str]:
    """Flatten all leaf labels in the nested DataWeb column tree."""

    labels: list[str] = []

    def visit(items: Iterable[Any]) -> None:
        for item in items:
            if isinstance(item, Mapping) and isinstance(item.get("columns"), list):
                visit(item["columns"])
            elif isinstance(item, Mapping) and item.get("label") is not None:
                labels.append(str(item["label"]))
            elif isinstance(item, list):
                visit(item)

    visit(table.get("column_groups", []))
    return labels


def _table_rows(table: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Read every row group, padding/truncating values to the header length."""

    labels = _column_labels(table)
    rows: list[dict[str, Any]] = []
    for group in table.get("row_groups", []):
        for row in group.get("rowsNew", []):
            values = [entry.get("value") for entry in row.get("rowEntries", [])]
            if len(values) < len(labels):
                values.extend([None] * (len(labels) - len(values)))
            rows.append(dict(zip(labels, values[: len(labels)])))
    return rows


def _rename_identifier_columns(frame: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "country": "origin_name",
        "hts number": "hts_code",
        "description": "hts_description",
        "quantity description": "quantity_unit",
        "year": "year",
    }
    rename = {
        column: aliases[str(column).strip().lower()]
        for column in frame.columns
        if str(column).strip().lower() in aliases
    }
    return frame.rename(columns=rename)


def _normalize_unit(value: Any) -> str | pd.NA:
    if value is None or pd.isna(value):
        return pd.NA
    text = str(value).strip()
    text = re.sub(r"^Value\s+for:\s*", "", text, flags=re.IGNORECASE)
    return text.lower() if text else pd.NA


def _unit_label(values: pd.Series) -> Any:
    units = sorted({str(value) for value in values.dropna()})
    return "|".join(units) if units else pd.NA


def _measure_frame(
    table: Mapping[str, Any],
    *,
    measure: str,
    source_file: str,
    origin_default: str,
) -> pd.DataFrame:
    rows = _table_rows(table)
    if not rows:
        return pd.DataFrame()
    frame = _rename_identifier_columns(pd.DataFrame(rows))
    if "origin_name" not in frame:
        frame["origin_name"] = origin_default
    frame["origin_name"] = frame["origin_name"].fillna(origin_default).astype(str).str.strip()
    if "hts_code" not in frame:
        raise ValueError(f"DataWeb table lacks HTS Number: {source_file}")

    # Dots are presentation only.  Codes remain strings so leading zeroes are
    # preserved; any non-code total row is removed explicitly.
    frame["hts_code"] = (
        frame["hts_code"].astype("string").str.replace(".", "", regex=False).str.strip()
    )
    frame = frame[frame["hts_code"].str.fullmatch(r"\d{2}|\d{4}|\d{6}|\d{8}|\d{10}", na=False)].copy()
    frame["hts_level"] = frame["hts_code"].str.len().astype("int64")
    if "hts_description" not in frame:
        frame["hts_description"] = pd.NA
    if "quantity_unit" not in frame:
        frame["quantity_unit"] = pd.NA

    month_columns = [name for name in MONTH_NAMES if name in frame.columns]
    year_columns = [
        column for column in frame.columns if re.fullmatch(r"(?:19|20)\d{2}", str(column))
    ]
    identifiers = [
        "origin_name",
        "hts_level",
        "hts_code",
        "hts_description",
        "quantity_unit",
    ]

    if month_columns:
        if "year" not in frame:
            raise ValueError(f"Monthly DataWeb table lacks a Year column: {source_file}")
        tidy = frame.melt(
            id_vars=identifiers + ["year"],
            value_vars=month_columns,
            var_name="month_name",
            value_name=measure,
        )
        tidy["year"] = pd.to_numeric(tidy["year"], errors="coerce").astype("Int64")
        tidy["month"] = tidy["month_name"].map(MONTH_NUMBER).astype("Int64")
        tidy["period"] = tidy.apply(
            lambda row: f"{int(row['year']):04d}-{int(row['month']):02d}"
            if pd.notna(row["year"]) and pd.notna(row["month"])
            else pd.NA,
            axis=1,
        )
        tidy["frequency"] = "monthly"
    elif year_columns:
        tidy = frame.melt(
            id_vars=identifiers,
            value_vars=year_columns,
            var_name="period",
            value_name=measure,
        )
        tidy["period"] = tidy["period"].astype(str)
        tidy["frequency"] = "annual"
    else:
        raise ValueError(f"DataWeb table has neither annual nor monthly value columns: {source_file}")

    tidy[measure] = tidy[measure].map(clean_number)
    tidy["quantity_unit"] = tidy["quantity_unit"].map(_normalize_unit)
    tidy["source_file"] = source_file

    keys = ["period", "frequency", "hts_level", "hts_code", "origin_name"]
    # A report should normally have one row per key.  Grouping here handles
    # DataWeb's occasional repeated presentation rows while preserving missing
    # values with min_count=1.  A duplicate-key guard remains after measures are
    # merged so different snapshots cannot be silently double-counted.
    metadata = (
        tidy.groupby(keys, dropna=False, as_index=False)
        .agg(
            hts_description=("hts_description", "first"),
            quantity_unit=("quantity_unit", _unit_label),
            source_file=("source_file", "first"),
        )
    )
    values = tidy.groupby(keys, dropna=False, as_index=False)[measure].sum(min_count=1)
    frame = metadata.merge(values, on=keys, how="outer", validate="one_to_one")
    if measure == "quantity_2":
        # The second-unit table's "Quantity Description" is the second unit.
        frame = frame.rename(columns={"quantity_unit": "quantity_2_unit"})
    unit_column = {"quantity": "quantity_unit", "quantity_2": "quantity_2_unit"}.get(measure)
    if unit_column:
        # Rows of one key that report different units are never added: the
        # key keeps the joined unit label ("kg|no.") and a blank quantity.
        mixed = frame[unit_column].astype("string").str.contains("|", regex=False).fillna(False)
        frame.loc[mixed, measure] = np.nan
    return frame


def parse_dataweb_response(
    response: Mapping[str, Any],
    *,
    source_file: str = "<memory>",
    origin_default: str = "World",
) -> pd.DataFrame:
    """Parse one DataWeb response into the canonical tidy schema.

    Measure tables are identified by ``tabName`` rather than their order.  All
    row groups are read.  A missing country column is interpreted as the World
    aggregate because that is the layout produced by our world query.
    """

    errors = (response.get("dto") or {}).get("errors")
    if errors:
        raise ValueError(f"DataWeb response contains errors: {errors}")
    tables = (response.get("dto") or {}).get("tables", [])
    measure_map = {
        "customs value": "customs_value_usd",
        "first unit of quantity": "quantity",
        "second unit of quantity": "quantity_2",
        "calculated duties": "calculated_duty_usd",
    }
    frames: list[pd.DataFrame] = []
    for table in tables:
        tab = (table.get("tableInfo") or {}).get("tabName") or table.get("tab_name") or table.get("name")
        measure = measure_map.get(str(tab).strip().lower())
        if measure:
            one = _measure_frame(
                table,
                measure=measure,
                source_file=source_file,
                origin_default=origin_default,
            )
            if not one.empty:
                frames.append(one)

    if not frames:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)

    keys = ["period", "frequency", "hts_level", "hts_code", "origin_name"]
    # The quantity tab carries the first unit (value tabs only say "Value for:
    # <unit>" once per row), so its unit label, including a mixed one, wins.
    frames.sort(key=lambda frame: "quantity" not in frame.columns)
    panel = frames[0]
    for extra in frames[1:]:
        value_columns = [
            column
            for column in ("customs_value_usd", "quantity", "calculated_duty_usd", "quantity_2")
            if column in extra.columns
        ]
        metadata_columns = [
            column
            for column in ("hts_description", "quantity_unit", "quantity_2_unit", "source_file")
            if column in extra.columns
        ]
        panel = panel.merge(
            extra[keys + metadata_columns + value_columns],
            on=keys,
            how="outer",
            suffixes=("", "__new"),
            validate="one_to_one",
        )
        for column in metadata_columns:
            new_column = column + "__new"
            if new_column in panel:
                panel[column] = panel[column].combine_first(panel[new_column])
                panel = panel.drop(columns=new_column)

    for column in ("customs_value_usd", "quantity", "calculated_duty_usd"):
        if column not in panel:
            panel[column] = np.nan
    panel["hts_description"] = panel.groupby("hts_code")["hts_description"].transform(
        lambda values: values.ffill().bfill()
    )
    panel["source_file"] = source_file

    if panel.duplicated(keys).any():
        examples = panel.loc[panel.duplicated(keys, keep=False), keys].head().to_dict("records")
        raise ValueError(f"Duplicate DataWeb keys after measure merge: {examples}")
    columns = CANONICAL_COLUMNS + (SECOND_QUANTITY_COLUMNS if "quantity_2" in panel else [])
    return panel[columns].sort_values(keys, kind="stable").reset_index(drop=True)


def parse_dataweb_file(path: str | Path, *, origin_default: str | None = None) -> pd.DataFrame:
    """Read and parse one immutable JSON snapshot.

    World queries are recognized from the filename when the caller does not
    supply a default.  A broken or non-JSON file fails loudly.
    """

    source = Path(path)
    with source.open("r", encoding="utf-8") as handle:
        response = json.load(handle)
    default = origin_default or "World"
    return parse_dataweb_response(response, source_file=str(source), origin_default=default)


def normalize_dataweb_files(paths: Iterable[str | Path]) -> pd.DataFrame:
    """Parse several non-overlapping raw snapshots into one tidy table."""

    frames = [parse_dataweb_file(path) for path in paths]
    frames = [frame for frame in frames if not frame.empty]
    if not frames:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    keys = ["period", "frequency", "hts_level", "hts_code", "origin_name"]
    duplicates = combined.duplicated(keys, keep=False)
    if duplicates.any():
        examples = combined.loc[duplicates, keys + ["source_file"]].head(10).to_dict("records")
        raise ValueError(
            "Overlapping raw snapshots produced duplicate product-period-origin keys. "
            f"Do not mix superseded and current snapshots: {examples}"
        )
    return combined.sort_values(keys, kind="stable").reset_index(drop=True)

