from __future__ import annotations

from pathlib import Path
import sys
import unittest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from usitc_pipeline.proxy_notes import PROXY_NOTE_COLUMNS, build_proxy_notes


class ProxyNotesTests(unittest.TestCase):
    def test_first_quantity_unit_and_limits_are_explicit(self) -> None:
        table = build_proxy_notes(
            quantity_basis="first",
            quantity_unit="No.",
            second_quantity_unit=None,
            data_through={"annual": "2025", "monthly": "2026-09"},
            hts_releases=["2026HTSRev22"],
            continuity_verified=False,
        ).set_index("item_id")

        self.assertEqual(list(table.reset_index().columns), list(PROXY_NOTE_COLUMNS))
        unit = table.loc["unit_value"]
        self.assertEqual(unit["formula"], "CONS_CUSTOMS_VALUE / CONS_FIR_UNIT_QUANT")
        self.assertEqual(unit["unit"], "USD per No.")
        self.assertIn("company ASP", unit["does_not_represent"])
        self.assertIn("quantity", unit["quality_warning"])
        self.assertIn("not verified", unit["quality_warning"])

        duty = table.loc["realized_duty"]
        self.assertIn("not", duty["does_not_represent"])
        self.assertIn("statutory HTS rate", duty["does_not_represent"])

        origin = table.loc["reported_origin"]
        self.assertIn("corporate supplier dependence", origin["does_not_represent"])

    def test_second_quantity_and_product_share_provenance(self) -> None:
        table = build_proxy_notes(
            quantity_basis="second",
            quantity_unit="kg",
            second_quantity_unit=None,
            data_through={"annual": "2025"},
            hts_releases=["2025HTSRev32", "2026HTSRev22"],
            continuity_verified=True,
            include_product_mix_share=True,
            product_mix_period=2024,
            product_mix_release="2024HTSArchived",
        ).set_index("item_id")

        self.assertEqual(table.loc["unit_value", "formula"], "CONS_CUSTOMS_VALUE / CONS_SEC_UNIT_QUANT")
        share = table.loc["hts10_import_value_share"]
        self.assertIn("independently queried parent HTS6", share["formula"])
        self.assertIn("not", share["does_not_represent"])
        self.assertIn("market share", share["does_not_represent"])
        self.assertEqual(share["data_period"], "2024")
        self.assertEqual(share["classification_release"], "2024HTSArchived")

    def test_product_share_is_absent_without_finer_level_acquisition(self) -> None:
        ids = set(
            build_proxy_notes(
                quantity_basis="first",
                quantity_unit=None,
                second_quantity_unit=None,
                data_through={},
                hts_releases=[],
                continuity_verified=False,
            )["item_id"]
        )
        self.assertNotIn("unit_value", ids)
        self.assertNotIn("hts10_import_value_share", ids)
        self.assertEqual(ids, {"realized_duty", "reported_origin"})

    def test_both_quantity_units_each_get_a_note(self) -> None:
        table = build_proxy_notes(
            quantity_basis="both",
            quantity_unit="No.",
            second_quantity_unit="kg",
            data_through={"annual": "2025"},
            hts_releases=["2026HTSRev22"],
            continuity_verified=True,
        ).set_index("item_id")
        self.assertEqual(table.loc["unit_value", "formula"], "CONS_CUSTOMS_VALUE / CONS_FIR_UNIT_QUANT")
        self.assertEqual(table.loc["unit_value_2", "formula"], "CONS_CUSTOMS_VALUE / CONS_SEC_UNIT_QUANT")


if __name__ == "__main__":
    unittest.main()
