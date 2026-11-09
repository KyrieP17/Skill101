"""Registry/HTS adapter tests for the tariff-treatment snapshot."""

from __future__ import annotations

import csv
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from usitc_pipeline.tariff_snapshot_adapter import (  # noqa: E402
    TariffSnapshotAdapterError,
    build_registry_tariff_snapshot,
    load_structure_release_metadata,
    registry_snapshot_inputs,
    structure_snapshot_inputs,
    validate_active_scope_release,
)


STRUCTURE = [
    {"htsno": "8542.32", "description": "Memories"},
    {"htsno": "8542.32.00", "description": "Memories", "general": "Free"},
    {"htsno": "8542.32.00.11", "description": "DRAM", "units": ["No."]},
    {"htsno": "8542.32.00.21", "description": "SRAM", "units": ["No."]},
]


def structure_inputs() -> dict:
    return structure_snapshot_inputs(
        STRUCTURE,
        parent_hts6=["854232"],
        classification_release="2025HTSRev1",
        release_effective_from="2025-01-01",
        source_citation="HTSUS 2025 Revision 1",
        source_sha256="a" * 64,
    )


class StructureAdapterTests(unittest.TestCase):
    def test_hts10_products_keep_the_hts8_general_rate_text(self) -> None:
        result = structure_inputs()
        self.assertEqual([row["hts"] for row in result["products"]], ["8542320011", "8542320021"])
        self.assertEqual(result["base_rates"][0]["hts"], "85423200")
        self.assertEqual(result["base_rates"][0]["rate_text"], "Free")
        self.assertEqual(result["base_rates"][0]["rate_type"], "free")

    def test_release_sidecar_binds_id_date_and_exact_structure_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            structure = root / "structure.json"
            structure.write_text(json.dumps(STRUCTURE), encoding="utf-8")
            metadata = root / "structure.release.json"
            payload = {
                "schema_version": 1,
                "release_id": "2025HTSRev1",
                "effective_date": "2025-01-01",
                "structure_file": structure.name,
                "source_url": "https://hts.usitc.gov/reststop/search?keyword=8542.32",
                "source_sha256": sha256(structure.read_bytes()).hexdigest(),
            }
            metadata.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_structure_release_metadata(
                metadata,
                structure_path=structure,
                expected_release_id="2025HTSRev1",
                expected_effective_date="2025-01-01",
            )
            self.assertEqual(loaded["source_sha256"], payload["source_sha256"])

            for field, value, message in (
                ("release_id", "2025HTSRev2", "release_id"),
                ("effective_date", "2025-02-01", "effective_date"),
                ("source_sha256", "0" * 64, "exact structure file"),
            ):
                with self.subTest(field=field):
                    changed = {**payload, field: value}
                    metadata.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(TariffSnapshotAdapterError, message):
                        load_structure_release_metadata(
                            metadata,
                            structure_path=structure,
                            expected_release_id="2025HTSRev1",
                            expected_effective_date="2025-01-01",
                        )

    def test_all_active_scope_lines_must_use_the_same_bound_release(self) -> None:
        validate_active_scope_release(
            "2025HTSRev1",
            ["2025HTSRev1", "2025HTSRev1"],
            entry_date="2025-06-30",
        )
        with self.assertRaisesRegex(TariffSnapshotAdapterError, "found .*2025HTSRev2"):
            validate_active_scope_release(
                "2025HTSRev1",
                ["2025HTSRev1", "2025HTSRev2"],
                entry_date="2025-06-30",
            )


class RegistryAdapterTests(unittest.TestCase):
    def test_memory_lifecycle_replaces_25_with_50(self) -> None:
        snapshot = build_registry_tariff_snapshot(
            structure_inputs=structure_inputs(),
            registry_directory=PROJECT_ROOT / "config" / "tariff_registry",
            origin="China",
            entry_date="2025-01-15",
        )
        self.assertEqual(snapshot["snapshot_status"], "Resolved")
        for row in snapshot["rows"]:
            self.assertEqual([layer["measure_id"] for layer in row["chapter_99_layers"]], ["s301-semiconductors-50"])
            self.assertEqual(row["treatment_formula"], "Column 1-General Free + 9903.91.05 +50%")

    def test_verified_ieepa_end_leaves_only_section_301_on_2026_11_09(self) -> None:
        snapshot = build_registry_tariff_snapshot(
            structure_inputs=structure_inputs(),
            registry_directory=PROJECT_ROOT / "config" / "tariff_registry",
            origin="China",
            entry_date="2026-11-09",
        )
        self.assertEqual(snapshot["snapshot_status"], "Resolved")
        for row in snapshot["rows"]:
            self.assertEqual(row["unresolved_reasons"], [])
            self.assertEqual([layer["measure_id"] for layer in row["chapter_99_layers"]], ["s301-semiconductors-50"])
            self.assertEqual(row["treatment_formula"], "Column 1-General Free + 9903.91.05 +50%")
            ended = [event for event in row["lifecycle_events"] if event["measure_id"] == "ieepa-ended"]
            self.assertEqual(len(ended), 1)
            self.assertEqual(ended[0]["action"], "end")
            self.assertEqual(ended[0]["removed_active_layers"], "ieepa-fentanyl-china-10")

    def test_verified_broad_exemption_does_not_need_a_treatment_rule(self) -> None:
        snapshot = build_registry_tariff_snapshot(
            structure_inputs=structure_inputs(),
            registry_directory=PROJECT_ROOT / "config" / "tariff_registry",
            origin="China",
            entry_date="2025-04-06",
        )
        self.assertEqual(snapshot["snapshot_status"], "Resolved")
        self.assertFalse(
            any(
                "ieepa-reciprocal-baseline" in reason
                for reason in snapshot["rows"][0]["unresolved_reasons"]
            )
        )

    def test_missing_active_applicable_rule_forces_unresolved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "registry"
            shutil.copytree(PROJECT_ROOT / "config" / "tariff_registry", target)
            rule_path = target / "treatment_rules.csv"
            with rule_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
                fields = list(rows[0])
            rows = [row for row in rows if row["measure_id"] != "s301-semiconductors-50"]
            with rule_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)
            snapshot = build_registry_tariff_snapshot(
                structure_inputs=structure_inputs(),
                registry_directory=target,
                origin="China",
                entry_date="2025-01-15",
            )
            self.assertEqual(snapshot["snapshot_status"], "Unresolved")
            self.assertTrue(any("no machine-readable treatment rule" in reason for reason in snapshot["rows"][0]["unresolved_reasons"]))

    def test_all_products_measure_without_coverage_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "registry"
            shutil.copytree(PROJECT_ROOT / "config" / "tariff_registry", target)
            coverage_path = target / "coverage.csv"
            with coverage_path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.DictReader(handle))
                fields = list(rows[0])
            rows = [row for row in rows if row["measure_id"] != "ieepa-fentanyl-china"]
            with coverage_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(rows)

            snapshot = build_registry_tariff_snapshot(
                structure_inputs=structure_inputs(),
                registry_directory=target,
                origin="China",
                entry_date="2025-02-15",
            )

            self.assertEqual(snapshot["snapshot_status"], "Unresolved")
            self.assertTrue(
                any(
                    "ieepa-fentanyl-china: coverage is missing (fail closed)" in reason
                    for reason in snapshot["rows"][0]["unresolved_reasons"]
                )
            )

    def test_finer_coverage_is_retained_for_longest_match(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "registry"
            shutil.copytree(PROJECT_ROOT / "config" / "tariff_registry", target)
            with (target / "coverage.csv").open("a", encoding="utf-8", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow([
                    "s301-semiconductors-50", "8542320011", "exempt", "hts-2026rev22-ch99 exact exception", "verified", "test"
                ])
            inputs = registry_snapshot_inputs(
                target,
                product_codes=["8542320011", "8542320021"],
                origin="China",
                entry_date="2025-01-15",
            )
            exact = [
                row for row in inputs["policy_coverage"]
                if row["measure_id"] == "s301-semiconductors-50" and row["hts"] == "8542320011"
            ]
            self.assertEqual(exact[0]["coverage"], "exempt")
            snapshot = build_registry_tariff_snapshot(
                structure_inputs=structure_inputs(),
                registry_directory=target,
                origin="China",
                entry_date="2025-01-15",
            )
            first, second = snapshot["rows"]
            self.assertEqual(
                [layer["measure_id"] for layer in first["chapter_99_layers"]],
                ["s301-list2"],
            )
            self.assertEqual(second["chapter_99_layers"][0]["measure_id"], "s301-semiconductors-50")


if __name__ == "__main__":
    unittest.main()
