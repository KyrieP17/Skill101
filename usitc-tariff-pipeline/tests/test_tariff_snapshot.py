"""Product-level HTS tariff-treatment snapshot tests."""

from __future__ import annotations

from pathlib import Path
import sys
import unittest


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from usitc_pipeline.tariff_snapshot import (  # noqa: E402
    TariffSnapshotError,
    build_tariff_treatment_snapshot,
    snapshot_table_rows,
)


def base(hts: str, rate_text: str = "Free", rate_type: str = "free", **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "hts": hts,
        "rate_text": rate_text,
        "rate_type": rate_type,
        "rate_column": "Column 1-General",
        "effective_from": "2025-01-01",
        "status": "verified",
        "source": "HTS 2025 Revision 1",
    }
    row.update(extra)
    return row


def measure(measure_id: str, rate_text: str = "+10%", **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "measure_id": measure_id,
        "origins": "China",
        "effective_from": "2025-02-04",
        "action": "add",
        "rate_text": rate_text,
        "rate_type": "ad_valorem",
        "heading": "9903.01.20",
        "layer": "IEEPA",
        "stack_group": "ieepa-opioid",
        "status": "verified",
        "source": "EO 14195; 90 FR 9121",
    }
    row.update(extra)
    return row


def coverage(measure_id: str, hts: str = "", value: str = "applies", **extra: object) -> dict[str, object]:
    row: dict[str, object] = {
        "measure_id": measure_id,
        "hts": hts,
        "coverage": value,
        "status": "verified",
        "evidence": "HTS chapter 99 note",
    }
    row.update(extra)
    return row


def snapshot(
    *,
    products: list[dict[str, str]] | None = None,
    bases: list[dict[str, object]] | None = None,
    measures: list[dict[str, object]] | None = None,
    coverages: list[dict[str, object]] | None = None,
    day: str = "2025-02-10",
    **context: object,
) -> dict[str, object]:
    return build_tariff_treatment_snapshot(
        products or [{"hts": "8542320051", "description": "DRAM"}],
        bases if bases is not None else [base("85423200")],
        measures if measures is not None else [measure("china-opioid")],
        coverages if coverages is not None else [coverage("china-opioid")],
        origin="China",
        entry_date=day,
        **context,
    )


class SnapshotResolutionTests(unittest.TestCase):
    def test_resolves_general_plus_verified_chapter_99_and_preserves_compound_formula(self) -> None:
        result = snapshot(bases=[base("85423200", "$0.05/kg + 3%", "compound")])
        self.assertEqual(result["snapshot_status"], "Resolved")
        row = result["rows"][0]
        self.assertEqual(row["base_rate"]["rate_text"], "$0.05/kg + 3%")
        self.assertEqual(row["chapter_99_layers"][0]["rate_text"], "+10%")
        self.assertEqual(row["treatment_formula"], "Column 1-General $0.05/kg + 3% + 9903.01.20 +10%")
        flat = snapshot_table_rows(result)[0]
        self.assertEqual(flat["base_rate_type"], "compound")
        self.assertEqual(flat["additional_rate_text"], "+10%")

    def test_exact_exclusion_overrides_generic_applies(self) -> None:
        result = snapshot(
            coverages=[
                coverage("china-opioid"),
                coverage("china-opioid", "8542320051", "exempt", evidence="HTS note 2(s) exclusion"),
            ]
        )
        row = result["rows"][0]
        self.assertEqual(result["snapshot_status"], "Resolved")
        self.assertEqual(row["chapter_99_layers"], [])
        self.assertEqual(row["excluded_measures"][0]["coverage"], "exempt")
        self.assertEqual(row["excluded_measures"][0]["matched_hts"], "8542.32.00.51")

    def test_missing_or_unverified_coverage_fails_closed(self) -> None:
        missing = snapshot(coverages=[])
        self.assertEqual(missing["snapshot_status"], "Unresolved")
        self.assertIn("coverage is missing (fail closed)", missing["rows"][0]["unresolved_reasons"][0])
        self.assertEqual(missing["rows"][0]["chapter_99_layers"], [])

        unchecked = snapshot(coverages=[coverage("china-opioid", status="draft")])
        self.assertEqual(unchecked["snapshot_status"], "Unresolved")
        self.assertIn("not verified", unchecked["rows"][0]["unresolved_reasons"][0])

    def test_finer_coverage_requires_product_expansion(self) -> None:
        result = snapshot(
            products=[{"hts": "85423200", "description": "Memory integrated circuits"}],
            coverages=[coverage("china-opioid", "8542320051")],
        )
        self.assertEqual(result["snapshot_status"], "Unresolved")
        self.assertIn("expand this product", result["rows"][0]["unresolved_reasons"][0])
        self.assertIn("8542.32.00.51", result["rows"][0]["unresolved_reasons"][0])

    def test_different_hts10_treatments_make_scope_mixed(self) -> None:
        result = snapshot(
            products=[
                {"hts": "8542320051", "description": "DRAM"},
                {"hts": "8542320061", "description": "SRAM"},
            ],
            coverages=[
                coverage("china-opioid", "8542320051", "applies"),
                coverage("china-opioid", "8542320061", "exempt"),
            ],
        )
        self.assertEqual(result["snapshot_status"], "Mixed")
        self.assertEqual({row["status"] for row in result["rows"]}, {"Mixed"})
        self.assertEqual(len(result["rows"][0]["chapter_99_layers"]), 1)
        self.assertEqual(result["rows"][1]["chapter_99_layers"], [])


class LifecycleTests(unittest.TestCase):
    def test_replace_and_end_use_explicit_supersedes(self) -> None:
        measures = [
            measure("duty-10", "+10%", effective_from="2025-02-04"),
            measure(
                "duty-20",
                "+20%",
                effective_from="2025-03-04",
                action="replace",
                supersedes="duty-10",
                heading="9903.01.24",
            ),
            measure(
                "duty-end",
                "",
                effective_from="2025-11-10",
                action="end",
                supersedes="duty-20",
                heading="9903.01.24",
            ),
        ]
        coverages = [coverage(row["measure_id"]) for row in measures]
        raised = snapshot(measures=measures, coverages=coverages, day="2025-04-01")
        self.assertEqual([row["measure_id"] for row in raised["rows"][0]["chapter_99_layers"]], ["duty-20"])
        self.assertEqual(raised["rows"][0]["lifecycle_events"][0]["removed_active_layers"], "duty-10")

        ended = snapshot(measures=measures, coverages=coverages, day="2025-11-10")
        self.assertEqual(ended["rows"][0]["chapter_99_layers"], [])
        self.assertEqual([row["action"] for row in ended["rows"][0]["lifecycle_events"]], ["replace", "end"])

    def test_never_assumes_stacking_inside_one_group(self) -> None:
        measures = [
            measure("duty-a", stack_group="same"),
            measure("duty-b", stack_group="same", heading="9903.88.15"),
        ]
        result = snapshot(measures=measures, coverages=[coverage("duty-a"), coverage("duty-b")])
        self.assertEqual(result["snapshot_status"], "Unresolved")
        self.assertIn("stacking is not assumed", result["rows"][0]["unresolved_reasons"][0])

    def test_replace_without_targets_is_unresolved(self) -> None:
        result = snapshot(
            measures=[measure("duty-20", action="replace")],
            coverages=[coverage("duty-20")],
        )
        self.assertEqual(result["snapshot_status"], "Unresolved")
        self.assertIn("needs explicit supersedes targets", result["rows"][0]["unresolved_reasons"][0])


class ConditionalAndValidationTests(unittest.TestCase):
    def test_special_cases_are_conditional_not_silently_resolved(self) -> None:
        result = snapshot(
            preference_claim="USMCA",
            entry_type="foreign_trade_zone",
            chapter_98=True,
            quota_or_trq=True,
            ad_cvd=True,
            firm_specific_rate=True,
            requires_binding_classification=True,
        )
        self.assertEqual(result["snapshot_status"], "Conditional")
        reasons = result["rows"][0]["conditional_reasons"]
        self.assertEqual(len(reasons), 7)
        self.assertTrue(any("AD/CVD" in item for item in reasons))
        self.assertTrue(any("CBP" in item for item in reasons))

    def test_other_rate_column_is_conditional(self) -> None:
        result = snapshot(
            bases=[base("85423200", rate_column="Column 2")],
            rate_column="Column 2",
        )
        self.assertEqual(result["snapshot_status"], "Conditional")
        self.assertTrue(any("Column 1-General only" in item for item in result["rows"][0]["conditional_reasons"]))

    def test_invalid_rows_are_rejected(self) -> None:
        with self.assertRaisesRegex(TariffSnapshotError, "6, 8 or 10 digits"):
            snapshot(products=[{"hts": "8542"}])
        with self.assertRaisesRegex(TariffSnapshotError, "unknown policy measures"):
            snapshot(coverages=[coverage("unknown")])
        with self.assertRaisesRegex(TariffSnapshotError, "must be unique"):
            snapshot(products=[{"hts": "8542320051"}, {"hts": "8542320051"}])


if __name__ == "__main__":
    unittest.main()
