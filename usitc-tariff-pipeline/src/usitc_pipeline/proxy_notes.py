"""Machine-readable provenance and limits for reported measures and proxies.

These rows are report inputs, not prose added after analysis.  A proxy is
therefore accompanied by its source fields, formula, denominator, unit and
interpretation boundary in every study run.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pandas as pd


PROXY_NOTE_COLUMNS = (
    "item_id",
    "item_type",
    "evidence_label",
    "source_dataset",
    "source_fields",
    "formula",
    "denominator",
    "unit",
    "represents",
    "does_not_represent",
    "data_period",
    "classification_release",
    "quality_warning",
)


def display_quantity_unit(value: str | None) -> str:
    """Return a user-facing HTS quantity-unit label.

    Scope identities are normalized to lower case for comparison and hashing.
    Display keeps the Schedule's conventional ``No.`` spelling; symbols such
    as ``kg`` are already conventionally lower case.
    """

    unit = str(value or "").strip()
    return "No." if unit.lower() == "no." else unit


def _period(data_through: Mapping[str, Any]) -> str:
    return "; ".join(
        f"{frequency} through {value}"
        for frequency, value in sorted(data_through.items())
        if value not in (None, "")
    )


def build_proxy_notes(
    *,
    quantity_basis: str,
    quantity_unit: str | None,
    second_quantity_unit: str | None = None,
    data_through: Mapping[str, Any],
    hts_releases: Sequence[str],
    continuity_verified: bool,
    include_product_mix_share: bool = False,
    product_mix_period: str | int | None = None,
    product_mix_release: str | None = None,
) -> pd.DataFrame:
    """Return the required provenance note for each measure or proxy.

    ``include_product_mix_share`` is true only when an HTS10 trade panel was
    actually acquired.  The function never implies that an HTS6 study can
    manufacture finer-level shares from its aggregate cache.
    """

    period = _period(data_through)
    releases = "; ".join(sorted(dict.fromkeys(str(item) for item in hts_releases if item)))
    continuity = (
        "HTS continuity was verified for the study window."
        if continuity_verified
        else "HTS continuity was not verified for every year; a code split or merge can affect comparisons."
    )
    rows: list[dict[str, str]] = []

    quantity_specs: list[tuple[str, str, str]] = []
    if quantity_unit:
        quantity_specs.append(
            (
                "unit_value",
                "CONS_SEC_UNIT_QUANT" if quantity_basis == "second" else "CONS_FIR_UNIT_QUANT",
                quantity_unit,
            )
        )
    if quantity_basis == "both" and second_quantity_unit:
        quantity_specs.append(("unit_value_2", "CONS_SEC_UNIT_QUANT", second_quantity_unit))

    for item_id, quantity_field, unit_name in quantity_specs:
        display_unit = display_quantity_unit(unit_name)
        count_warning = (
            " No. is the HTS statistical count unit for reported articles; it does not standardize "
            "storage capacity, weight, package type or product specification, and it is not a count of customs declarations."
            if display_unit == "No."
            else ""
        )
        rows.append(
            {
                "item_id": item_id,
                "item_type": "derived_proxy",
                "evidence_label": "Derived",
                "source_dataset": "USITC DataWeb, U.S. imports for consumption",
                "source_fields": f"CONS_CUSTOMS_VALUE; {quantity_field}",
                "formula": f"CONS_CUSTOMS_VALUE / {quantity_field}",
                "denominator": f"reported import quantity ({quantity_field}; HTS statistical unit {display_unit})",
                "unit": f"USD per {display_unit}",
                "represents": "average customs value per reported quantity within the selected HTS scope, origin and period",
                "does_not_represent": "a transaction price, company ASP, market price, per-capacity price or a quality-adjusted price index",
                "data_period": period,
                "classification_release": releases,
                "quality_warning": (
                    "Product technology, capacity, package and specification mix can change the result; Census may suppress "
                    f"quantity without a flag.{count_warning} {continuity}"
                ),
            }
        )

    rows.extend(
        [
            {
                "item_id": "realized_duty",
                "item_type": "derived_measure",
                "evidence_label": "Derived",
                "source_dataset": "USITC DataWeb, U.S. imports for consumption",
                "source_fields": "CONS_CALC_DUTY; CONS_CUSTOMS_VALUE",
                "formula": "CONS_CALC_DUTY / CONS_CUSTOMS_VALUE",
                "denominator": "customs value of observed consumption entries",
                "unit": "percent",
                "represents": "calculated duty collected relative to customs value for the observed entry mix",
                "does_not_represent": "not the statutory HTS rate and not proof that every entry used the same rate provision, preference or Chapter 99 treatment",
                "data_period": period,
                "classification_release": releases,
                "quality_warning": "Entry programs, exclusions, duty-free entries and product mix can make the realized rate differ from a statutory treatment.",
            },
            {
                "item_id": "reported_origin",
                "item_type": "reported_dimension",
                "evidence_label": "Measured",
                "source_dataset": "USITC DataWeb / U.S. Census import records",
                "source_fields": "reported country or area of origin",
                "formula": "reported dimension; no calculation",
                "denominator": "not applicable",
                "unit": "country or area",
                "represents": "the country or area of origin reported to U.S. Customs for the imported good",
                "does_not_represent": "shipping country, value-added origin, fab location, corporate supplier dependence or transshipment-adjusted exposure",
                "data_period": period,
                "classification_release": releases,
                "quality_warning": "Firm-level and value-chain dependence require separate shipment, company or supply-chain data.",
            },
        ]
    )

    if include_product_mix_share:
        mix_period = str(product_mix_period) if product_mix_period not in (None, "") else period
        mix_release = str(product_mix_release or "").strip() or releases
        rows.append(
            {
                "item_id": "hts10_import_value_share",
                "item_type": "derived_proxy",
                "evidence_label": "Derived",
                "source_dataset": "USITC DataWeb, independent World consumption-import queries",
                "source_fields": "HTS10 CONS_CUSTOMS_VALUE; parent HTS6 CONS_CUSTOMS_VALUE",
                "formula": "HTS10 World customs value / independently queried parent HTS6 World customs value",
                "denominator": "parent HTS6 World customs value for the same period",
                "unit": "percent of parent HTS6 import customs value",
                "represents": "the HTS10 line's share of U.S. consumption-import customs value inside the researched HTS6 scope",
                "does_not_represent": "not U.S. market share, global capacity, shipment count, company share or value-added share",
                "data_period": mix_period,
                "classification_release": mix_release,
                "quality_warning": f"Numerator and denominator must use the same period and compatible HTS edition. {continuity}",
            }
        )

    return pd.DataFrame(rows, columns=PROXY_NOTE_COLUMNS)
