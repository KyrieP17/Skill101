from __future__ import annotations

from datetime import date
from pathlib import Path
import sys
import tempfile
import unittest


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
if str(PROJECT_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT / "src"))

from usitc_pipeline.study import StudyError, load_study  # noqa: E402


BASE = """[study]
study_id = "snapshot-test"
title = "Snapshot test"
scope_manifest = "scopes/x/v1/manifest.json"

[periods]
annual = { start = 2024, end = 2025 }

[origins]
focal = "China"
highlight = []
"""


class TariffSnapshotStudyTests(unittest.TestCase):
    def load(self, suffix: str):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            study = root / "study.toml"
            study.write_text(BASE + suffix, encoding="utf-8")
            return load_study(study, project_root=root, today=date(2026, 11, 9))

    def test_absent_snapshot_preserves_the_default_hash_shape(self) -> None:
        spec = self.load("")
        self.assertIsNone(spec.tariff_snapshot)

    def test_explicit_entry_context_is_loaded_and_hashed(self) -> None:
        baseline = self.load('\n[drilldown]\nstructure = "scopes/x/v1/history/structure.json"\n')
        spec = self.load(
            '\n[drilldown]\nstructure = "scopes/x/v1/history/structure.json"\n'
            'release_metadata = "scopes/x/v1/history/structure.release.json"\n'
            '\n[tariff_snapshot]\nentry_date = "2026-09-30"\nhts_release_id = "2026HTSRev19"\n'
            'hts_release_date = "2026-08-01"\norigins = ["China", "Taiwan"]\n'
            'rate_column = "general"\npreference_claim = "none"\nentry_type = "consumption"\nad_cvd = true\n'
        )
        self.assertIsNotNone(spec.tariff_snapshot)
        self.assertEqual(spec.tariff_snapshot.origins, ("China", "Taiwan"))
        self.assertTrue(spec.tariff_snapshot.ad_cvd)
        self.assertNotEqual(spec.study_sha256, baseline.study_sha256)

    def test_snapshot_requires_structure_and_explicit_nonfuture_date(self) -> None:
        with self.assertRaisesRegex(StudyError, r"needs \[drilldown\] structure"):
            self.load(
                '\n[tariff_snapshot]\nentry_date = "2026-09-30"\nhts_release_id = "2026HTSRev19"\n'
                'hts_release_date = "2026-08-01"\n'
            )
        with self.assertRaisesRegex(StudyError, r"needs \[drilldown\] release_metadata"):
            self.load(
                '\n[drilldown]\nstructure = "scopes/x/v1/history/structure.json"\n'
                '\n[tariff_snapshot]\nentry_date = "2026-09-30"\nhts_release_id = "2026HTSRev19"\n'
                'hts_release_date = "2026-08-01"\n'
            )
        with self.assertRaisesRegex(StudyError, "cannot be in the future"):
            self.load(
                '\n[drilldown]\nstructure = "scopes/x/v1/history/structure.json"\n'
                'release_metadata = "scopes/x/v1/history/structure.release.json"\n'
                '\n[tariff_snapshot]\nentry_date = "2027-01-01"\nhts_release_id = "2026HTSRev19"\n'
                'hts_release_date = "2026-08-01"\n'
            )

    def test_disabled_snapshot_cannot_hide_unused_settings(self) -> None:
        with self.assertRaisesRegex(StudyError, "cannot include snapshot settings"):
            self.load('\n[tariff_snapshot]\nenabled = false\nentry_date = "2026-09-30"\n')


if __name__ == "__main__":
    unittest.main()
