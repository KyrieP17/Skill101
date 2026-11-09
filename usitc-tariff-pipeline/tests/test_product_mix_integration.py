"""Offline integration for explicit HTS10 product-mix acquisition under HTS6."""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
for entry in (PROJECT_ROOT / "src", TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import test_lithium_e2e as e2e  # noqa: E402
import test_plan  # noqa: E402
from test_scope import make_row  # noqa: E402
from usitc_pipeline import plan as plan_module  # noqa: E402
from usitc_pipeline import scope as scope_module  # noqa: E402
from usitc_pipeline.config import load_config  # noqa: E402
from usitc_pipeline.dataweb import DataWebClient  # noqa: E402
from usitc_pipeline.study import StudyError  # noqa: E402
from usitc_pipeline.study_pipeline import analyze_study, render_study_charts, validate_study_outputs  # noqa: E402


STRUCTURE = [
    {"htsno": "1111.11.00", "description": "Synthetic legal rate line", "general": "Free", "units": []},
    {"htsno": "1111.11.00.10", "description": "Synthetic product A", "general": "", "units": ["No."]},
    {"htsno": "1111.11.00.20", "description": "Synthetic product B", "general": "", "units": ["No."]},
]

CURRENT_STRUCTURE = [
    {"htsno": "1111.11.00", "description": "Current legal rate line", "general": "Free", "units": []},
    {"htsno": "1111.11.00.30", "description": "Current product C", "general": "", "units": ["No."]},
]

ANNUAL_STUDY = """
[study]
study_id = "synthetic-product-mix"
title = "Synthetic HTS10 product mix"
scope_manifest = "scopes/synthetic/v1/manifest.json"

[periods]
annual = { start = 2023, end = 2024 }

[origins]
focal = "China"
highlight = ["Vietnam"]

[comparison]
base_year = 2023
compare_year = 2024

[drilldown]
structure = "config/hts_structure.json"
release_metadata = "config/hts_structure.release.json"

[product_mix]
period = 2024
"""


def fixture() -> dict:
    def item(description: str, years: dict[str, dict[str, tuple[float, float, float]]]) -> dict:
        return {"description": description, "quantity_unit": "No.", "years": years}

    return {
        "codes": {
            "111111": item(
                "Synthetic HTS6 parent",
                {
                    "2023": {"China": (400.0, 40.0, 0.0), "Vietnam": (400.0, 40.0, 0.0)},
                    "2024": {"China": (300.0, 30.0, 0.0), "Vietnam": (700.0, 70.0, 0.0)},
                },
            ),
            "1111110010": item(
                "Synthetic product A",
                {"2024": {"China": (100.0, 10.0, 0.0), "Vietnam": (300.0, 30.0, 0.0)}},
            ),
            "1111110020": item(
                "Synthetic product B",
                {"2024": {"China": (200.0, 20.0, 0.0), "Vietnam": (400.0, 40.0, 0.0)}},
            ),
        }
    }


class ProductMixIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        scope_row = make_row(
            hts_level="6",
            hts_code="111111",
            hts_release="2026HTSSynthetic",
            valid_from="2017-01-01",
            valid_to="2026-12-31",
        )
        self.paths = test_plan.build_project(self.root, rows=[scope_row])
        self.config = load_config(self.paths["config"])
        self.structure = self.root / "config" / "hts_structure.json"
        self.structure.write_text(json.dumps(STRUCTURE), encoding="utf-8")
        self.metadata = self.root / "config" / "hts_structure.release.json"
        self._write_metadata("2024-01-01")
        self.paths["study"].write_text(ANNUAL_STUDY, encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_metadata(self, effective_date: str) -> None:
        self._write_release_metadata(
            self.metadata,
            self.structure,
            release_id="2024HTSSynthetic",
            effective_date=effective_date,
        )

    @staticmethod
    def _write_release_metadata(
        metadata: Path,
        structure: Path,
        *,
        release_id: str,
        effective_date: str,
    ) -> None:
        metadata.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "release_id": release_id,
                    "effective_date": effective_date,
                    "structure_file": structure.name,
                    "source_url": "https://hts.usitc.gov/synthetic",
                    "source_sha256": sha256(structure.read_bytes()).hexdigest(),
                }
            ),
            encoding="utf-8",
        )

    def _configure_independent_product_mix(
        self,
        *,
        period: int = 2024,
        release_id: str = "2024HTSArchived",
    ) -> tuple[Path, Path]:
        structure = self.root / "config" / f"hts_structure_{period}.json"
        metadata = self.root / "config" / f"hts_structure_{period}.release.json"
        structure.write_text(json.dumps(STRUCTURE), encoding="utf-8")
        self._write_release_metadata(
            metadata,
            structure,
            release_id=release_id,
            effective_date=f"{period}-01-01",
        )
        self.paths["study"].write_text(
            ANNUAL_STUDY.replace(
                "[product_mix]\nperiod = 2024",
                (
                    f"[product_mix]\nperiod = {period}\n"
                    f'structure = "config/{structure.name}"\n'
                    f'release_metadata = "config/{metadata.name}"'
                ),
            ),
            encoding="utf-8",
        )
        return structure, metadata

    def plan(self) -> dict:
        study = plan_module.load_project_study(self.config, self.paths["study"], today=test_plan.TODAY)
        scope = scope_module.verify_scope_manifest(self.paths["manifest"])
        return plan_module.build_query_plan(scope, study, max_records=self.config.dataweb.max_records)

    def test_plan_adds_two_release_bound_hts10_jobs_without_polluting_scope_periods(self) -> None:
        plan = self.plan()
        product_jobs = [job for job in plan["jobs"] if job.get("purpose") == "product_mix"]
        self.assertEqual(len(product_jobs), 2)
        self.assertEqual({tuple(job["codes"]) for job in product_jobs}, {("1111110010", "1111110020")})
        self.assertEqual({job["hts_level"] for job in product_jobs}, {10})
        self.assertEqual({tuple(job["years"]) for job in product_jobs}, {(2024,)})
        self.assertEqual(plan["product_mix"]["hts_release_id"], "2024HTSSynthetic")
        self.assertNotIn("structure", plan["product_mix"])
        self.assertNotIn("release_metadata", plan["product_mix"])
        self.assertEqual(plan_module.period_codes(plan)[("annual", "2024")], ("111111",))
        self.assertEqual(plan_module.query_through(plan), {"annual": "2024"})
        self.assertTrue(all(row["codes"] == ["111111"] for row in plan_module.codes_by_period(plan)))

    def test_release_from_another_year_is_rejected_before_planning(self) -> None:
        self._write_metadata("2023-12-31")
        with self.assertRaisesRegex(StudyError, "effective in that year"):
            self.plan()

    def test_independent_structure_and_metadata_must_be_supplied_together(self) -> None:
        self.paths["study"].write_text(
            ANNUAL_STUDY.replace(
                "[product_mix]\nperiod = 2024",
                (
                    "[product_mix]\nperiod = 2024\n"
                    'structure = "config/hts_structure_2024.json"'
                ),
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(StudyError, "must be supplied together"):
            self.plan()

    def test_2025_product_mix_uses_its_archive_while_snapshot_keeps_2026_drilldown(self) -> None:
        self.structure.write_text(json.dumps(CURRENT_STRUCTURE), encoding="utf-8")
        self._write_release_metadata(
            self.metadata,
            self.structure,
            release_id="2026HTSSynthetic",
            effective_date="2026-01-01",
        )
        archived_structure = self.root / "config" / "hts_structure_2025.json"
        archived_metadata = self.root / "config" / "hts_structure_2025.release.json"
        archived_structure.write_text(
            json.dumps(
                [
                    *STRUCTURE,
                    {
                        "htsno": "2222.22.00.10",
                        "description": "Unrelated HTS10 line",
                        "general": "",
                        "units": ["No."],
                    },
                ]
            ),
            encoding="utf-8",
        )
        self._write_release_metadata(
            archived_metadata,
            archived_structure,
            release_id="2025HTSArchived",
            effective_date="2025-01-01",
        )
        self.paths["study"].write_text(
            """
[study]
study_id = "synthetic-product-mix"
title = "Synthetic HTS10 product mix"
scope_manifest = "scopes/synthetic/v1/manifest.json"

[periods]
annual = { start = 2025, end = 2025 }

[origins]
focal = "China"
highlight = []

[drilldown]
structure = "config/hts_structure.json"
release_metadata = "config/hts_structure.release.json"

[tariff_snapshot]
entry_date = "2026-09-30"
hts_release_id = "2026HTSSynthetic"
hts_release_date = "2026-01-01"
origins = ["China"]
rate_column = "general"
preference_claim = "none"
entry_type = "consumption"

[product_mix]
period = 2025
structure = "config/hts_structure_2025.json"
release_metadata = "config/hts_structure_2025.release.json"
""",
            encoding="utf-8",
        )

        study = plan_module.load_project_study(
            self.config, self.paths["study"], today=test_plan.TODAY
        )
        scope = scope_module.verify_scope_manifest(self.paths["manifest"])
        plan = plan_module.build_query_plan(
            scope, study, max_records=self.config.dataweb.max_records
        )
        self.assertEqual(study.tariff_snapshot.hts_release_id, "2026HTSSynthetic")
        self.assertEqual(study.product_mix.hts_release_id, "2025HTSArchived")
        self.assertEqual(study.product_mix.structure, "config/hts_structure_2025.json")
        self.assertEqual(
            {tuple(job["codes"]) for job in plan["jobs"] if job.get("purpose") == "product_mix"},
            {("1111110010", "1111110020")},
        )
        self.assertEqual(plan["product_mix"]["structure"], "config/hts_structure_2025.json")
        self.assertEqual(
            plan["product_mix"]["release_metadata"],
            "config/hts_structure_2025.release.json",
        )
        self.assertEqual(
            plan["product_mix"]["structure_sha256"],
            sha256(archived_structure.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            plan["product_mix"]["release_metadata_sha256"],
            sha256(archived_metadata.read_bytes()).hexdigest(),
        )

        original_study_sha = study.study_sha256
        original_plan_sha = plan["plan_sha256"]
        sidecar = json.loads(archived_metadata.read_text(encoding="utf-8"))
        sidecar["source_url"] = "https://hts.usitc.gov/synthetic-republished"
        archived_metadata.write_text(json.dumps(sidecar), encoding="utf-8")
        changed_study = plan_module.load_project_study(
            self.config, self.paths["study"], today=test_plan.TODAY
        )
        changed_plan = plan_module.build_query_plan(
            scope, changed_study, max_records=self.config.dataweb.max_records
        )
        self.assertNotEqual(changed_study.study_sha256, original_study_sha)
        self.assertNotEqual(changed_plan["plan_sha256"], original_plan_sha)

    def test_independent_sidecar_sha_must_match_selected_structure(self) -> None:
        archived_structure, _ = self._configure_independent_product_mix()
        archived_structure.write_text(json.dumps(CURRENT_STRUCTURE), encoding="utf-8")
        with self.assertRaisesRegex(StudyError, "source_sha256 does not match"):
            self.plan()

    def test_every_active_approved_parent_needs_an_hts10_child(self) -> None:
        archived_structure, archived_metadata = self._configure_independent_product_mix()
        archived_structure.write_text(
            json.dumps(
                [
                    {
                        "htsno": "2222.22.00.10",
                        "description": "Only an unrelated HTS10 line",
                        "general": "Free",
                        "units": ["No."],
                    }
                ]
            ),
            encoding="utf-8",
        )
        self._write_release_metadata(
            archived_metadata,
            archived_structure,
            release_id="2024HTSArchived",
            effective_date="2024-01-01",
        )
        with self.assertRaisesRegex(StudyError, "no HTS10 statistical categories"):
            self.plan()

    def test_acquired_hts10_world_values_reach_matrix_with_independent_hts6_denominator(self) -> None:
        self.structure.write_text(json.dumps(CURRENT_STRUCTURE), encoding="utf-8")
        self._write_metadata("2024-01-01")
        archived_structure, archived_metadata = self._configure_independent_product_mix()
        plan, plan_path = plan_module.plan_study(self.config, self.paths["study"])
        self.assertEqual(len([job for job in plan["jobs"] if job.get("purpose") == "product_mix"]), 2)
        client = DataWebClient(
            self.config,
            token=e2e.TOKEN,
            session=e2e.LithiumFixtureSession(fixture()),
        )
        plan_module.acquire_plan(self.config, plan_path, client=client, today=test_plan.TODAY)
        out = Path(analyze_study(self.config, self.paths["study"])["analysis_dir"])

        matrix = pd.read_csv(out / "tables" / "product_hierarchy_matrix.csv", dtype={"hts10_code": str})
        matrix = matrix.set_index("hts10_code")
        self.assertEqual(set(matrix["product_share_status"]), {"measured"})
        self.assertAlmostEqual(matrix.loc["1111110010", "latest_full_period_import_value_usd"], 400.0)
        self.assertAlmostEqual(matrix.loc["1111110020", "latest_full_period_import_value_usd"], 600.0)
        self.assertAlmostEqual(matrix.loc["1111110010", "share_of_parent_hts6_pct"], 40.0)
        self.assertAlmostEqual(matrix.loc["1111110020", "share_of_parent_hts6_pct"], 60.0)
        self.assertEqual(set(matrix["classification_release"]), {"2024HTSArchived"})
        self.assertEqual(matrix.loc["1111110010", "hts10_display_label_en"], "Synthetic product A")
        self.assertEqual(matrix.loc["1111110020", "hts10_classification_path_en"],
                         "Synthetic legal rate line > Synthetic product B")

        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(
            sum(item["kind"] == "product_mix_snapshot" for item in manifest["inputs"]),
            2,
        )
        self.assertEqual(
            manifest["product_mix_structure_inputs"],
            [
                {
                    "path": "config/hts_structure_2024.json",
                    "sha256": sha256(archived_structure.read_bytes()).hexdigest(),
                },
                {
                    "path": "config/hts_structure_2024.release.json",
                    "sha256": sha256(archived_metadata.read_bytes()).hexdigest(),
                },
            ],
        )
        self.assertTrue(manifest["product_mix_structure_input_sha256"])
        render_study_charts(self.config, self.paths["study"])
        chart = pd.read_csv(out / "charts" / "12_product_mix.csv", dtype={"hts10_code": str})
        self.assertTrue(
            {"hts10_category_group_en", "hts10_classification_path_en", "hts10_display_label_en"}
            <= set(chart.columns)
        )
        self.assertEqual(set(chart["hts10_display_label_en"]), {"Synthetic product A", "Synthetic product B"})
        validation = validate_study_outputs(self.config, self.paths["study"])
        self.assertEqual(validation["scope_sha256"], plan["scope"]["scope_sha256"])
        self.assertGreater(validation["charts"], 0)


if __name__ == "__main__":
    unittest.main()
