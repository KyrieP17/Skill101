"""Unit tests for the research metrics and validation guardrails.

The important distinction here is between *calculation* and *interpretation*.
These tests verify arithmetic and data-contract rules only.  They do not claim
that a tariff caused a sourcing change, or that an industry proxy proves
domestic reshoring.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from usitc_pipeline.analysis import (  # noqa: E402
    add_period_status,
    audit_period_coverage,
    build_display_series,
    build_domestic_production_screen,
    build_origin_series,
    build_product_reallocation,
    codes_not_in_force,
    build_scope_series,
    build_trade_panel,
    build_world_check,
    compare_origin_windows,
    derive_data_through,
    quantity_unit_status,
    safe_divide,
    validate_trade_panel,
)


def _two_origin_tidy() -> pd.DataFrame:
    """Return two origins plus their independently queried World total."""

    return pd.DataFrame(
        {
            "period": ["2025", "2025", "2025"],
            "frequency": ["annual", "annual", "annual"],
            "hts_level": [6, 6, 6],
            "hts_code": ["847130", "847130", "847130"],
            "hts_description": ["Portable computers"] * 3,
            "origin_name": ["China", "Vietnam", "World"],
            "customs_value_usd": [60.0, 40.0, 100.0],
            "quantity": [6.0, 4.0, 10.0],
            "quantity_unit": ["number", "number", "number"],
            "calculated_duty_usd": [6.0, 0.0, 6.0],
            "source_file": ["origins.json", "origins.json", "world.json"],
        }
    )


def _two_year_reallocation_panel() -> pd.DataFrame:
    """Return one product with a China gain and no positive non-China gainer."""

    rows: list[dict[str, object]] = []
    values = {
        "2024": {"China": 40.0, "Vietnam": 30.0, "Mexico": 30.0, "World": 100.0},
        "2025": {"China": 60.0, "Vietnam": 20.0, "Mexico": 20.0, "World": 100.0},
    }
    for period, by_origin in values.items():
        for origin, value in by_origin.items():
            rows.append(
                {
                    "period": period,
                    "frequency": "annual",
                    "hts_level": 6,
                    "hts_code": "850760",
                    "hts_description": "Synthetic lithium-ion batteries",
                    "origin_name": origin,
                    "customs_value_usd": value,
                    "quantity": value,
                    "quantity_unit": "number",
                    "calculated_duty_usd": 0.0,
                    "source_file": "world.json" if origin == "World" else "origins.json",
                }
            )
    return build_trade_panel(pd.DataFrame(rows))


class SafeDivideTests(unittest.TestCase):
    def test_zero_denominator_becomes_nan(self) -> None:
        result = safe_divide(
            pd.Series([1.0, 2.0]),
            pd.Series([2.0, 0.0]),
        )
        self.assertAlmostEqual(float(result.iloc[0]), 0.5)
        self.assertTrue(np.isnan(result.iloc[1]))


class TradePanelTests(unittest.TestCase):
    def test_origin_shares_and_realized_duty_are_derived_once(self) -> None:
        panel = build_trade_panel(_two_origin_tidy())

        china = panel.loc[panel["origin_name"] == "China"].iloc[0]
        vietnam = panel.loc[panel["origin_name"] == "Vietnam"].iloc[0]

        self.assertEqual(china["world_value_usd"], 100.0)
        self.assertAlmostEqual(china["value_share"], 0.60)
        self.assertAlmostEqual(vietnam["value_share"], 0.40)
        self.assertAlmostEqual(china["quantity_share"], 0.60)
        self.assertAlmostEqual(china["unit_value_usd"], 10.0)
        self.assertAlmostEqual(china["realized_duty_rate"], 0.10)

        shares = panel.groupby(["period", "hts_code"])["value_share"].sum()
        self.assertTrue(np.allclose(shares.to_numpy(), 1.0))

    def test_duplicate_origin_product_period_is_rejected(self) -> None:
        panel = build_trade_panel(_two_origin_tidy())
        duplicated = pd.concat([panel, panel.iloc[[0]]], ignore_index=True)

        with self.assertRaisesRegex(ValueError, "duplicate|Duplicate"):
            validate_trade_panel(duplicated, strict=True)

    def test_mixed_units_stop_quantity_only_for_that_product_period(self) -> None:
        clean = _two_origin_tidy().assign(period="2024")
        mixed = _two_origin_tidy()
        mixed.loc[1, "quantity_unit"] = "kilograms"
        panel = build_trade_panel(pd.concat([clean, mixed], ignore_index=True))
        rows = panel.set_index(["period", "origin_name"])

        self.assertTrue(rows.loc[("2024", "China"), "quantity_comparable"])
        self.assertAlmostEqual(rows.loc[("2024", "China"), "unit_value_usd"], 10.0)
        self.assertFalse(rows.loc[("2025", "China"), "quantity_comparable"])
        for column in ("quantity", "quantity_share", "unit_value_usd"):
            self.assertTrue(pd.isna(rows.loc[("2025", "China"), column]), column)
        # Value and duty results are not affected.
        self.assertAlmostEqual(rows.loc[("2025", "China"), "value_share"], 0.60)
        self.assertAlmostEqual(rows.loc[("2025", "China"), "realized_duty_rate"], 0.10)
        validation = validate_trade_panel(panel, strict=True)
        self.assertTrue(validation["ok"])
        self.assertEqual(validation["quantity_blocked_product_periods"], 1)

    def test_unit_label_marked_mixed_or_missing_is_not_comparable(self) -> None:
        for label in ("kilograms|number", ""):
            with self.subTest(label=label):
                tidy = _two_origin_tidy()
                tidy.loc[2, "quantity_unit"] = label
                self.assertFalse(bool(quantity_unit_status(tidy)["quantity_comparable"].iloc[0]))
        self.assertTrue(bool(quantity_unit_status(_two_origin_tidy())["quantity_comparable"].iloc[0]))

    def test_origin_whose_rows_mixed_units_blocks_quantity_without_aborting(self) -> None:
        """Normalization blanks a mixed key's quantity; the rest must not look comparable."""

        tidy = _two_origin_tidy()
        tidy.loc[0, ["quantity", "quantity_unit"]] = [np.nan, "kilograms|number"]
        panel = build_trade_panel(tidy)
        self.assertFalse(panel["quantity_comparable"].any())
        self.assertTrue(panel["quantity_share"].isna().all())
        self.assertTrue(validate_trade_panel(panel, strict=True)["ok"])

    def test_independent_world_is_required_by_default(self) -> None:
        tidy = _two_origin_tidy()
        tidy = tidy[tidy["origin_name"].ne("World")]

        with self.assertRaisesRegex(ValueError, "independent World"):
            build_trade_panel(tidy)


class PeriodStatusTests(unittest.TestCase):
    def test_latest_annual_period_is_marked_ytd_when_cutoff_is_partial(self) -> None:
        annual = pd.DataFrame(
            {
                "period": ["2025", "2026"],
                "frequency": ["annual", "annual"],
            }
        )
        result = add_period_status(annual, ytd_end_month=8)

        self.assertEqual(result.loc[0, "period_status"], "full_year")
        self.assertEqual(result.loc[1, "period_status"], "ytd_through_08")

    def test_monthly_rows_remain_monthly_observations(self) -> None:
        monthly = pd.DataFrame(
            {
                "period": ["2026-07", "2026-08"],
                "frequency": ["monthly", "monthly"],
            }
        )
        result = add_period_status(monthly, ytd_end_month=8)
        self.assertTrue((result["period_status"] == "monthly").all())


class ProductReallocationRegressionTests(unittest.TestCase):
    def test_full_year_cannot_be_compared_with_ytd(self) -> None:
        panel = _two_year_reallocation_panel()
        panel["period_status"] = np.where(
            panel["period"].eq("2024"),
            "full_year",
            "ytd_through_08",
        )

        with self.assertRaisesRegex(ValueError, "period_status|full-year|YTD"):
            build_product_reallocation(panel, base_year=2024, compare_year=2025)

    def test_top_gainer_is_missing_when_every_non_china_share_falls(self) -> None:
        result = build_product_reallocation(
            _two_year_reallocation_panel(),
            base_year=2024,
            compare_year=2025,
        )

        row = result.iloc[0]
        self.assertTrue(pd.isna(row["top_gaining_origin"]))
        self.assertTrue(pd.isna(row["top_origin_share_gain_pp"]))

    def test_missing_quantity_unit_is_not_comparable(self) -> None:
        panel = _two_year_reallocation_panel()
        panel["quantity_unit"] = ""

        row = build_product_reallocation(panel, base_year=2024, compare_year=2025).iloc[0]

        self.assertFalse(bool(row["quantity_comparable"]))
        self.assertTrue(pd.isna(row["quantity_unit"]))
        self.assertTrue(pd.isna(row["world_import_quantity_growth_pct"]))
        self.assertTrue(pd.isna(row["china_import_quantity_growth_pct"]))

    def test_unlabeled_zero_row_does_not_block_quantity(self) -> None:
        # HTS10 extracts list origins with no trade that year as zero rows
        # without a unit label; they say nothing about the unit.
        panel = _two_year_reallocation_panel()
        zero = panel[panel["origin_name"].eq("Mexico")].copy()
        zero["origin_name"], zero["customs_value_usd"], zero["quantity"], zero["quantity_unit"] = "Chile", 0.0, 0.0, ""
        zero["value_share"], zero["quantity_share"] = 0.0, 0.0
        panel = pd.concat([panel, zero], ignore_index=True)

        row = build_product_reallocation(panel, base_year=2024, compare_year=2025).iloc[0]

        self.assertTrue(bool(row["quantity_comparable"]))
        self.assertEqual(row["quantity_unit"], "number")

    def test_share_loss_without_comparable_quantity_is_not_called_limited_change(self) -> None:
        panel = _two_year_reallocation_panel()
        panel["quantity_unit"] = ""

        row = build_product_reallocation(panel, base_year=2024, compare_year=2025, focal_origin="Vietnam").iloc[0]

        self.assertAlmostEqual(float(row["focal_share_change_pp"]), -10.0)
        self.assertEqual(row["pattern"], "share_loss_demand_quantity_not_comparable")

    def test_non_china_focal_origin_has_only_generic_focal_fields(self) -> None:
        row = build_product_reallocation(
            _two_year_reallocation_panel(),
            base_year=2024,
            compare_year=2025,
            focal_origin="Vietnam",
        ).iloc[0]

        self.assertEqual(row["focal_origin"], "Vietnam")
        self.assertAlmostEqual(float(row["focal_share_change_pp"]), -10.0)
        self.assertFalse(any(str(column).startswith("china_") for column in row.index))


class OriginTablesTests(unittest.TestCase):
    """Shares, inflow/outflow, World reconciliation, HHI and realized duty across all origins."""

    def setUp(self) -> None:
        frames = []
        for period, (china, vietnam, mexico) in {"2024": (60.0, 30.0, 10.0), "2025": (30.0, 50.0, 0.0)}.items():
            tidy = pd.DataFrame(
                {
                    "period": [period] * 4,
                    "frequency": ["annual"] * 4,
                    "hts_level": [6] * 4,
                    "hts_code": ["847130"] * 4,
                    "hts_description": ["Portable computers"] * 4,
                    "origin_name": ["China", "Vietnam", "Mexico", "World"],
                    "customs_value_usd": [china, vietnam, mexico, china + vietnam + mexico],
                    "quantity": [china / 10, vietnam / 10, mexico / 10, (china + vietnam + mexico) / 10],
                    "quantity_unit": ["number"] * 4,
                    "calculated_duty_usd": [china * 0.1, 0.0, 0.0, china * 0.1],
                    "source_file": ["origins.json"] * 3 + ["world.json"],
                }
            )
            frames.append(tidy)
        self.panel = build_trade_panel(pd.concat(frames, ignore_index=True))
        self.origins = build_origin_series(self.panel)

    def test_window_comparison_covers_every_origin_and_sums_to_world_growth(self) -> None:
        change = compare_origin_windows(
            self.origins, comparison="annual", frequency="annual", base_periods=["2024"], compare_periods=["2025"]
        ).set_index("origin_name")
        self.assertEqual(list(change.index), ["World", "Vietnam", "Mexico", "China"])
        self.assertEqual(change.loc["World", "base_periods"], "2024")
        self.assertEqual(
            change.loc[["Vietnam", "China", "Mexico"], "direction"].tolist(), ["inflow", "outflow", "exited"]
        )
        self.assertAlmostEqual(change.loc["World", "value_growth_pct"], -20.0)
        self.assertAlmostEqual(change.drop(index="World")["contribution_pp"].sum(), -20.0)
        self.assertAlmostEqual(change.loc["China", "share_change_pp"], 30.0 / 80.0 * 100 - 60.0)
        self.assertAlmostEqual(change.loc["China", "compare_realized_duty_pct"], 10.0)

    def test_window_comparison_drops_quantity_when_units_are_not_comparable(self) -> None:
        change = compare_origin_windows(
            build_origin_series(self.panel, quantity_comparable=False),
            comparison="annual", frequency="annual", base_periods=["2024"], compare_periods=["2025"],
        )
        self.assertTrue(change["quantity_growth_pct"].isna().all())
        self.assertTrue(change["unit_value_change_pct"].isna().all())
        self.assertFalse(change["value_growth_pct"].isna().any())

    def test_scope_series_hhi_top_origins_and_realized_duty(self) -> None:
        series = build_scope_series(self.origins, focal_origin="China").set_index("period")
        self.assertAlmostEqual(series.loc["2024", "hhi"], 60.0**2 + 30.0**2 + 10.0**2)
        self.assertEqual(series.loc["2025", "top_origin"], "Vietnam")
        self.assertAlmostEqual(series.loc["2025", "focal_value_share_pct"], 37.5)
        self.assertAlmostEqual(series.loc["2024", "world_realized_duty_rate_pct"], 6.0)
        self.assertEqual(series.loc["2025", "reporting_origins"], 2)

    def test_display_series_folds_other_origins_and_world_check_reconciles(self) -> None:
        display = build_display_series(self.origins, focal_origin="China", highlight_origins=("Vietnam",))
        self.assertEqual(set(display["display_origin"]), {"China", "Vietnam", "Other reported origins"})
        self.assertTrue(display.groupby("period")["value_share"].sum().round(12).eq(1.0).all())
        check = build_world_check(self.panel)
        self.assertEqual(check["status"].tolist(), ["ok", "ok"])
        self.assertTrue(check["world_value_gap_pct"].abs().lt(1e-9).all())


class DataThroughTests(unittest.TestCase):
    def test_zero_counts_as_observed_but_blank_world_value_does_not(self) -> None:
        frame = pd.DataFrame(
            {
                "period": ["2026-07", "2026-08", "2026-09", "2026-09"],
                "frequency": ["monthly"] * 4,
                "hts_code": ["850760"] * 4,
                "origin_name": ["World", "World", "World", "China"],
                "customs_value_usd": [12.0, 0.0, "", 99.0],
            }
        )

        result = derive_data_through(
            frame,
            {
                ("monthly", "2026-07"): ["850760"],
                ("monthly", "2026-08"): ["850760"],
                ("monthly", "2026-09"): ["850760"],
            },
        )

        self.assertEqual(result, {"monthly": "2026-08"})

    def test_trailing_zero_calendar_tail_is_not_future_data(self) -> None:
        frame = pd.DataFrame(
            {
                "period": ["2026-06", "2026-07", "2026-08"],
                "frequency": ["monthly"] * 3,
                "hts_code": ["847130"] * 3,
                "origin_name": ["World"] * 3,
                "customs_value_usd": [10.0, 0.0, 0.0],
                "quantity": [2.0, 0.0, 0.0],
                "calculated_duty_usd": [1.0, 0.0, 0.0],
            }
        )

        result = derive_data_through(
            frame,
            {
                ("monthly", "2026-06"): ["847130"],
                ("monthly", "2026-07"): ["847130"],
                ("monthly", "2026-08"): ["847130"],
            },
        )

        self.assertEqual(result, {"monthly": "2026-06"})

    def test_multiple_active_codes_use_conservative_complete_through(self) -> None:
        frame = pd.DataFrame(
            {
                "period": [
                    "2026-01",
                    "2026-02",
                    "2026-03",
                    "2026-02",
                    "2026-03",
                ],
                "frequency": ["monthly"] * 5,
                "hts_code": ["8507600010"] * 3 + ["8507600090"] * 2,
                "origin_name": ["World"] * 5,
                "customs_value_usd": [10.0, 11.0, 12.0, 6.0, np.nan],
            }
        )

        result = derive_data_through(
            frame,
            {
                ("monthly", "2026-01"): ["8507600010"],
                ("monthly", "2026-02"): ["8507600010", "8507600090"],
                ("monthly", "2026-03"): ["8507600010", "8507600090"],
            },
        )

        self.assertEqual(result, {"monthly": "2026-02"})

    @staticmethod
    def _split_year(values: dict[str, list[float]]) -> tuple[pd.DataFrame, dict]:
        """One fully queried 2026 with the codes in ``values`` (12 months each)."""

        months = [f"2026-{month:02d}" for month in range(1, 13)]
        frame = pd.DataFrame(
            {
                "period": [month for _ in values for month in months],
                "frequency": "monthly",
                "hts_code": [code for code in values for _ in months],
                "origin_name": "World",
                "customs_value_usd": [value for series in values.values() for value in series],
            }
        )
        return frame, {("monthly", month): list(values) for month in months}

    def test_mid_year_split_blanks_are_not_missing_data(self) -> None:
        # .20 retired after January; .30 and .90 start in February; Oct-Dec are
        # DataWeb's zero placeholders for the codes in force, blank for .20.
        nan = np.nan
        frame, schedule = self._split_year(
            {
                "8507600010": [5.0] * 9 + [0.0] * 3,
                "8507600020": [9.0] + [nan] * 11,
                "8507600030": [nan] + [3.0] * 8 + [0.0] * 3,
                "8507600090": [nan] + [6.0] * 8 + [0.0] * 3,
            }
        )

        self.assertEqual(derive_data_through(frame, schedule), {"monthly": "2026-09"})

    def test_new_code_blank_between_observations_still_stops_the_scan(self) -> None:
        nan = np.nan
        frame, schedule = self._split_year(
            {
                "8507600010": [5.0] * 9 + [0.0] * 3,
                "8507600020": [9.0] + [nan] * 11,
                "8507600030": [nan, 3.0, nan] + [3.0] * 6 + [0.0] * 3,
                "8507600090": [nan] + [6.0] * 8 + [0.0] * 3,
            }
        )

        self.assertEqual(derive_data_through(frame, schedule), {"monthly": "2026-02"})

    def test_codes_not_in_force_lists_the_split_months(self) -> None:
        nan = np.nan
        frame, schedule = self._split_year(
            {
                "8507600010": [5.0] * 9 + [0.0] * 3,
                "8507600020": [9.0] + [nan] * 11,
                "8507600030": [nan] + [3.0] * 8 + [0.0] * 3,
            }
        )

        result = codes_not_in_force(frame, schedule)

        self.assertEqual(result[("monthly", "2026-01")], {"8507600030"})
        self.assertEqual(result[("monthly", "2026-12")], {"8507600020"})
        self.assertNotIn("8507600020", result.get(("monthly", "2026-01"), set()))
        self.assertNotIn("8507600010", set().union(*result.values()))

    def test_code_never_reported_in_the_year_is_not_excused(self) -> None:
        nan = np.nan
        frame, schedule = self._split_year(
            {
                "8507600010": [5.0] * 9 + [0.0] * 3,
                "8507600030": [nan] * 12,
            }
        )

        self.assertEqual(derive_data_through(frame, schedule), {"monthly": None})

    def test_data_through_refuses_origin_sums_without_world_rows(self) -> None:
        frame = pd.DataFrame(
            {
                "period": ["2026-01"],
                "frequency": ["monthly"],
                "hts_code": ["850760"],
                "origin_name": ["China"],
                "customs_value_usd": [10.0],
            }
        )

        with self.assertRaisesRegex(ValueError, "independent World"):
            derive_data_through(
                frame,
                {("monthly", "2026-01"): ["850760"]},
            )


class CoverageAuditTests(unittest.TestCase):
    def test_blank_future_month_is_not_an_observation_but_zero_is(self) -> None:
        frame = pd.DataFrame(
            {
                "hts_code": ["847130", "847130", "847130"],
                "period": ["2026-08", "2026-09", "2026-10"],
                "quantity_unit": ["no."] * 3,
                "hts_description": ["Portable computers"] * 3,
                "customs_value_usd": [0.0, np.nan, ""],
                "quantity": [0.0, np.nan, ""],
            }
        )

        row = audit_period_coverage(
            frame,
            expected_periods=["2026-08", "2026-09", "2026-10"],
        ).iloc[0]

        self.assertEqual(row["last_observed_period"], "2026-08")
        self.assertEqual(row["missing_periods"], "2026-09;2026-10")

    def test_code_is_only_expected_where_it_was_queried_and_in_force(self) -> None:
        frame = pd.DataFrame(
            {
                "hts_code": ["8507600020", "8507600030", "8507600030"],
                "period": ["2026-01", "2026-02", "2026-03"],
                "quantity_unit": ["no."] * 3,
                "hts_description": ["Other", "Storage systems", "Storage systems"],
                "customs_value_usd": [9.0, 3.0, 3.0],
            }
        )

        audit = audit_period_coverage(
            frame,
            expected_periods=["2026-01", "2026-02", "2026-03"],
            expected_by_code={"8507600020": ["2026-01"], "8507600030": ["2026-02", "2026-03"]},
        ).set_index("hts_code")

        self.assertEqual(audit["missing_period_count"].tolist(), [0, 0])
        self.assertFalse(audit["needs_review"].any())

    def test_trailing_dataweb_zero_placeholders_are_reported_missing(self) -> None:
        frame = pd.DataFrame(
            {
                "hts_code": ["847130"] * 3,
                "period": ["2026-08", "2026-09", "2026-10"],
                "quantity_unit": ["no."] * 3,
                "hts_description": ["Portable computers"] * 3,
                "customs_value_usd": [10.0, 0.0, 0.0],
                "quantity": [2.0, 0.0, 0.0],
            }
        )

        row = audit_period_coverage(
            frame,
            expected_periods=["2026-08", "2026-09", "2026-10"],
        ).iloc[0]

        self.assertEqual(row["last_observed_period"], "2026-08")
        self.assertEqual(row["missing_periods"], "2026-09;2026-10")


class DomesticProductionProxyTests(unittest.TestCase):
    def test_growth_is_computed_from_official_series_values(self) -> None:
        fred = pd.DataFrame(
            {
                "series_id": ["IPG3341A", "IPG3341A"],
                "date": pd.to_datetime(["2024-01-01", "2025-01-01"]),
                "value": [144.9861, 165.2480],
                "retrieved_at": ["2026-10-23", "2026-10-23"],
            }
        )
        mapping = pd.DataFrame(
            {
                "hts_code": ["847130"],
                "hts_level": [6],
                "series_id": ["IPG3341A"],
                "naics": ["3341"],
                "industry_name": ["Computer and peripheral equipment"],
                "mapping_quality": ["broad"],
                "notes": ["Includes products beyond portable computers"],
            }
        )

        result = build_domestic_production_screen(
            fred,
            mapping,
            base_year=2024,
            compare_year=2025,
        )
        row = result.iloc[0]

        self.assertAlmostEqual(row["base_index"], 144.9861)
        self.assertAlmostEqual(row["compare_index"], 165.2480)
        self.assertAlmostEqual(row["index_change_pct"], 13.9751, places=3)
        self.assertEqual(row["mapping_quality"], "broad")


if __name__ == "__main__":
    unittest.main()
