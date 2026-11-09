"""Deterministic automatic next-step menu."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

import pandas as pd


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from usitc_pipeline import next_steps  # noqa: E402


def resolved() -> list[dict[str, str]]:
    return [{"hts_code": "8542320011", "treatment_status": "Resolved"}]


def measured_hierarchy() -> list[dict[str, str]]:
    return [{"hts10_code": "8542320011", "product_share_status": "measured"}]


class NextStepsTests(unittest.TestCase):
    def test_unresolved_snapshot_and_missing_hts10_data_are_first(self) -> None:
        table = next_steps.build_next_steps(
            tariff_snapshot=[], scope_hts_level=6, product_hierarchy=[], continuity_verified=False
        )
        self.assertEqual(list(table.columns), list(next_steps.NEXT_STEP_COLUMNS))
        self.assertEqual(
            table["action_id"].tolist(),
            ["resolve_tariff_treatment_snapshot", "acquire_hts10_product_mix", "verify_hts_history"],
        )
        self.assertEqual(table.iloc[0]["needs_network_or_new_data"], "official_source_review")
        self.assertIn("cannot produce HTS10 shares", table.iloc[1]["rationale"])

    def test_conditional_snapshot_asks_for_entry_conditions(self) -> None:
        table = next_steps.build_next_steps(
            tariff_snapshot=[{"status": "Conditional"}],
            scope_hts_level=10,
            continuity_verified=True,
        )
        self.assertEqual(table["action_id"].tolist(), ["specify_entry_conditions"])
        self.assertIn("preference claim", table.iloc[0]["required_input"])

    def test_mixed_snapshot_requests_product_split(self) -> None:
        table = next_steps.build_next_steps(
            tariff_snapshot=[{"resolution_status": "Mixed"}],
            scope_hts_level=8,
            continuity_verified=True,
        )
        self.assertEqual(table.iloc[0]["action_id"], "split_mixed_tariff_treatments")

    def test_duty_and_unit_value_flags_are_structured_actions(self) -> None:
        flags = pd.DataFrame([{"rule": "duty_below_timeline"}, {"rule": "unit_value_far_off"}])
        table = next_steps.build_next_steps(
            tariff_snapshot=resolved(),
            scope_hts_level=6,
            product_hierarchy=measured_hierarchy(),
            anomaly_flags=flags,
            continuity_verified=True,
        )
        self.assertEqual(
            table["action_id"].tolist(),
            ["break_out_rate_provisions", "extend_hts10_product_mix_history"],
        )
        self.assertIn("both comparison years", table.iloc[1]["required_input"])
        self.assertTrue(table["expected_output"].str.len().gt(0).all())

    def test_partial_product_mix_never_counts_as_complete(self) -> None:
        table = next_steps.build_next_steps(
            tariff_snapshot=resolved(),
            scope_hts_level=6,
            product_hierarchy=[
                {"product_share_status": "measured"},
                {"product_share_status": "unavailable_missing_independent_hts10_world_query"},
            ],
            continuity_verified=True,
        )
        self.assertEqual(table.iloc[0]["action_id"], "acquire_hts10_product_mix")
        self.assertIn("unavailable_missing", table.iloc[0]["rationale"])

    def test_light_industry_context_is_opt_in_only(self) -> None:
        off = next_steps.build_next_steps(
            tariff_snapshot=resolved(), scope_hts_level=10, continuity_verified=True
        )
        self.assertNotIn("add_light_industry_context", set(off["action_id"]))
        on = next_steps.build_next_steps(
            tariff_snapshot=resolved(), scope_hts_level=10, continuity_verified=True,
            industry_context_mode="light",
        )
        self.assertEqual(on.iloc[0]["action_id"], "add_light_industry_context")
        self.assertEqual(on.iloc[0]["needs_network_or_new_data"], "optional_network_research")

    def test_no_open_item_emits_explicit_no_action_row(self) -> None:
        table = next_steps.build_next_steps(
            tariff_snapshot=[
                {"hts_code": "8542320011", "treatment_status": "Resolved"},
                {"hts_code": "8542320021", "treatment_status": "Resolved"},
            ],
            scope_hts_level=6,
            product_hierarchy=measured_hierarchy(),
            anomaly_flags=[],
            continuity_verified=True,
        )
        self.assertEqual(len(table), 1)
        self.assertEqual(table.iloc[0]["action_id"], "no_action")
        self.assertEqual(table.iloc[0]["needs_network_or_new_data"], "none")

    def test_menu_is_capped_at_three_in_fixed_priority_order(self) -> None:
        table = next_steps.build_next_steps(
            tariff_snapshot=[{"status": "Unresolved"}],
            scope_hts_level=6,
            product_hierarchy=[],
            anomaly_flags=[{"rule": "duty_below_base_rate"}, {"rule": "unit_value_far_off"}],
            continuity_verified=False,
            industry_context_mode="light",
        )
        self.assertEqual(len(table), 3)
        self.assertEqual(table["priority"].tolist(), [10, 30, 40])

    def test_invalid_settings_are_refused(self) -> None:
        with self.assertRaises(next_steps.NextStepsError):
            next_steps.build_next_steps(tariff_snapshot=resolved(), scope_hts_level=7)
        with self.assertRaises(next_steps.NextStepsError):
            next_steps.build_next_steps(
                tariff_snapshot=resolved(), scope_hts_level=10, industry_context_mode="always"
            )


if __name__ == "__main__":
    unittest.main()
