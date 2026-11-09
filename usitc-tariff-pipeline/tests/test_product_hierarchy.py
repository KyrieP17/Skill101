"""Synthetic tests for the HTS6 -> HTS8 -> HTS10 product-mix matrix."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from usitc_pipeline import product_hierarchy  # noqa: E402


STRUCTURE = [
    {"htsno": "8542.32", "description": "Memories"},
    {"htsno": "8542.32.00", "description": "Memories", "general": "Free"},
    {"htsno": "8542.32.00.11", "description": "Dynamic random access", "units": ["No."]},
    {"htsno": "8542.32.00.21", "description": "Static random access", "units": ["No."]},
    {"htsno": "8542.32.00.41", "description": "Flash memory", "units": ["No."]},
]


def trade() -> pd.DataFrame:
    rows = []
    for code, world, china in (
        ("8542320011", 600.0, 300.0),
        ("8542320021", 250.0, 25.0),
        ("8542320041", 50.0, 0.0),
    ):
        rows.extend([
            {"frequency": "annual", "period": "2025", "hts_code": code, "origin_name": "World",
             "customs_value_usd": world},
            {"frequency": "annual", "period": "2025", "hts_code": code, "origin_name": "China",
             "customs_value_usd": china},
        ])
    return pd.DataFrame(rows)


class ProductHierarchyTests(unittest.TestCase):
    def build(self, **kwargs) -> pd.DataFrame:
        defaults = {
            "parent_hts6": "854232",
            "classification_release": "2025 HTS Revision 19",
            "continuity_status": "verified_for_2025",
        }
        defaults.update(kwargs)
        return product_hierarchy.build_product_hierarchy_matrix(STRUCTURE, **defaults)

    def test_structure_only_never_invents_hts10_trade_shares(self) -> None:
        matrix = self.build(full_period="2025", independent_hts6_world_value_usd=1000,
                            independent_hts6_world_period="2025")
        self.assertEqual(matrix["hts10_code"].tolist(), ["8542320011", "8542320021", "8542320041"])
        self.assertEqual(set(matrix["hts8_code"]), {"85423200"})
        self.assertEqual(set(matrix["hts8_mfn_rate"]), {"Free"})
        self.assertTrue(matrix["share_of_parent_hts6_pct"].isna().all())
        self.assertEqual(set(matrix["product_share_status"]), {"unavailable_missing_hts10_trade_rows"})
        layout = product_hierarchy.product_mix_layout(matrix)
        self.assertTrue(layout["collapse_hts8"])
        self.assertEqual(layout["display_mode"], "horizontal_bar_single_hts8")
        self.assertTrue(layout["chart_status"].startswith("unavailable_"))

    def test_import_mix_uses_independent_hts6_denominator_and_does_not_renormalize(self) -> None:
        matrix = self.build(
            trade_rows=trade(), full_period="2025", independent_hts6_world_value_usd=1000,
            independent_hts6_world_period="2025",
        ).set_index("hts10_code")
        self.assertAlmostEqual(matrix.at["8542320011", "share_of_parent_hts6_pct"], 60.0)
        self.assertAlmostEqual(matrix.at["8542320021", "share_of_parent_hts6_pct"], 25.0)
        self.assertAlmostEqual(matrix.at["8542320041", "share_of_parent_hts6_pct"], 5.0)
        self.assertAlmostEqual(matrix["share_of_parent_hts6_pct"].sum(), 90.0)
        self.assertAlmostEqual(matrix.at["8542320011", "focal_origin_source_share_pct"], 50.0)
        self.assertIn("not market share", matrix.iloc[0]["share_proxy_limit"])
        self.assertIn("reported country of origin", matrix.iloc[0]["origin_proxy_limit"])

    def test_hts10_values_do_not_create_shares_without_the_independent_hts6_query(self) -> None:
        matrix = self.build(trade_rows=trade(), full_period="2025")
        self.assertEqual(set(matrix["trade_data_status"]), {"measured"})
        self.assertTrue(matrix["share_of_parent_hts6_pct"].isna().all())
        self.assertEqual(
            set(matrix["product_share_status"]), {"unavailable_missing_independent_hts6_world_query"}
        )

    def test_period_mismatch_fails_closed(self) -> None:
        matrix = self.build(
            trade_rows=trade(), full_period="2025", independent_hts6_world_value_usd=1000,
            independent_hts6_world_period="2024",
        )
        self.assertTrue(matrix["share_of_parent_hts6_pct"].isna().all())
        self.assertEqual(set(matrix["product_share_status"]), {"unavailable_hts6_hts10_period_mismatch"})

    def test_missing_hts10_world_query_is_not_replaced_by_sum_of_origins(self) -> None:
        origins_only = trade()[lambda frame: frame["origin_name"].ne("World")]
        matrix = self.build(
            trade_rows=origins_only, full_period="2025", independent_hts6_world_value_usd=1000,
            independent_hts6_world_period="2025",
        )
        self.assertEqual(
            set(matrix["trade_data_status"]), {"unavailable_missing_independent_hts10_world_query"}
        )
        self.assertTrue(matrix["latest_full_period_import_value_usd"].isna().all())

    def test_resolved_tariff_snapshot_maps_by_longest_product_code(self) -> None:
        treatments = [
            {"hts_code": "854232", "treatment_status": "Resolved", "additional_tariff_rate": "+10%"},
            {"hts_code": "8542320011", "treatment_status": "Conditional",
             "additional_tariff_rate": "excluded", "note": "HTS10 exclusion controls"},
        ]
        matrix = self.build(tariff_treatments=treatments).set_index("hts10_code")
        self.assertEqual(matrix.at["8542320011", "additional_tariff_status"], "Conditional")
        self.assertEqual(matrix.at["8542320011", "additional_tariff_rate"], "excluded")
        self.assertEqual(matrix.at["8542320021", "additional_tariff_rate"], "+10%")

    def test_multiple_hts8_groups_remain_visible(self) -> None:
        structure = STRUCTURE + [
            {"htsno": "8542.32.10", "description": "Other memories", "general": "2.5%"},
            {"htsno": "8542.32.10.10", "description": "Other memory line", "units": ["No."]},
        ]
        matrix = product_hierarchy.build_product_hierarchy_matrix(structure, parent_hts6="854232")
        layout = product_hierarchy.product_mix_layout(matrix)
        self.assertFalse(layout["collapse_hts8"])
        self.assertEqual(layout["display_mode"], "grouped_horizontal_bar")
        self.assertEqual(layout["hts8_count"], 2)

    def test_chart_data_keeps_unavailable_rows_and_proxy_note(self) -> None:
        matrix = self.build(
            trade_rows=trade(), full_period="2025", independent_hts6_world_value_usd=1000,
            independent_hts6_world_period="2025",
        )
        chart = product_hierarchy.build_product_mix_chart_data(matrix)
        self.assertEqual(list(chart.columns), list(product_hierarchy.CHART_COLUMNS))
        self.assertEqual(chart["sort_order"].tolist(), [1, 2, 3])
        self.assertEqual(chart["hts10_code"].tolist(), ["8542320011", "8542320021", "8542320041"])
        self.assertTrue(chart["proxy_note"].str.contains("not market share").all())
        self.assertTrue(chart["proxy_note"].str.contains("China customs value / HTS10 World").all())
        self.assertTrue(chart["proxy_note"].str.contains("reported country of origin").all())

    def test_duplicate_product_period_origin_rows_are_refused(self) -> None:
        duplicated = pd.concat([trade(), trade().iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(product_hierarchy.ProductHierarchyError, "Duplicate HTS10-period-origin"):
            self.build(trade_rows=duplicated, full_period="2025")


if __name__ == "__main__":
    unittest.main()
