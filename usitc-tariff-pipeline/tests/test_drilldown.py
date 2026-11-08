"""Drill-down menu: verdicts from the HTS structure, scope windows and timeline only.

All codes, rates and windows are synthetic.
"""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import sys
import unittest

import pandas as pd

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
for entry in (PROJECT_ROOT / "src", TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import test_features  # noqa: E402  (module import, so its tests are not collected twice)
import test_plan  # noqa: E402
from usitc_pipeline import drilldown  # noqa: E402
from usitc_pipeline.study import StudyError  # noqa: E402


STRUCTURE = [
    {"htsno": "8507.60.00", "general": "3.4%", "units": None, "footnotes": [], "additionalDuties": None},
    {"htsno": "8507.60.00.10", "general": "", "units": ["No.", "kg"]},
    {"htsno": "8507.60.00.30", "general": "", "units": ["No.", "kg"]},
    {"htsno": "8507.60.00.90", "general": "", "units": ["No.", "kg"]},
    {"htsno": "9902.16.20", "general": "Free", "units": []},
]
START, END = date(2017, 1, 1), date(2026, 12, 31)


def codes(*rows: tuple[str, str, str]) -> list[drilldown.ScopeCode]:
    return [
        drilldown.ScopeCode(code, date.fromisoformat(start), date.fromisoformat(end), ("no.", "kg"))
        for code, start, end in rows
    ]


LITHIUM = codes(
    ("8507600010", "2017-01-01", "2026-12-31"),
    ("8507600020", "2017-01-01", "2026-04-07"),
    ("8507600030", "2026-01-16", "2026-12-31"),
    ("8507600090", "2026-01-16", "2026-12-31"),
)


def duty(day: str, applies_to: str, date_type: str = "effective") -> dict[str, str]:
    return {"date": day, "date_type": date_type, "measure": f"Synthetic duty {day}", "applies_to": applies_to}


class MenuTests(unittest.TestCase):
    def test_hts8_same_and_hts10_split_by_a_duty_on_one_line(self) -> None:
        menu = drilldown.build_menu(
            STRUCTURE, LITHIUM, [duty("2024-09-27", "8507600010"), duty("2026-01-01", "85076000")],
            period_start=START, period_end=END,
        )
        hts8, hts10 = menu
        self.assertEqual((hts8["level"], hts8["line_count"], hts8["verdict"]), ("HTS8", 1, drilldown.VERDICT_SAME))
        self.assertEqual(hts8["hts8_base_rate"], "8507.60.00: 3.4%")
        self.assertEqual(hts8["units"], "no.; kg")
        self.assertEqual((hts10["level"], hts10["line_count"], hts10["verdict"]), ("HTS10", 4, drilldown.VERDICT_DRILL))
        self.assertTrue(hts10["partial_duty"])
        # Only the 2024 row is partial: on 2024-09-27 two lines existed and it covers one;
        # the 2026 row covers the whole HTS8.
        self.assertIn("2024-09-27", hts10["partial_duty_rows"])
        self.assertIn("同期 2 行中的 1 行", hts10["partial_duty_rows"])
        self.assertNotIn("2026-01-01", hts10["partial_duty_rows"])
        self.assertEqual(
            hts10["splits_in_period"],
            "8507.60.00.20 到 2026-04-07; 8507.60.00.30 从 2026-01-16; 8507.60.00.90 从 2026-01-16",
        )
        self.assertEqual(tuple(hts8), drilldown.MENU_COLUMNS)

    def test_without_a_partial_duty_more_lines_are_only_a_statistical_split(self) -> None:
        announced = duty("2024-05-14", "8507600010", date_type="announced")
        hts8, hts10 = drilldown.build_menu(STRUCTURE, LITHIUM, [announced], period_start=START, period_end=END)
        self.assertEqual(hts8["verdict"], drilldown.VERDICT_SAME)
        self.assertEqual(hts10["verdict"], drilldown.VERDICT_STATISTICAL)
        self.assertFalse(hts10["partial_duty"])

    def test_different_hts8_base_rates_split_the_hts8_level(self) -> None:
        structure = [
            {"htsno": "8471.30.01", "general": "Free", "units": ["No."]},
            {"htsno": "8471.30.02", "general": "2.5%", "units": ["No."]},
        ]
        scope = codes(("84713001", "2017-01-01", "2026-12-31"), ("84713002", "2017-01-01", "2026-12-31"))
        (hts8,) = [row for row in drilldown.build_menu(structure, scope, period_start=START, period_end=END)
                   if row["level"] == "HTS8"]
        self.assertTrue(hts8["base_rate_split"])
        self.assertEqual(hts8["verdict"], drilldown.VERDICT_DRILL)
        self.assertEqual(hts8["hts8_base_rate"], "8471.30.01: Free; 8471.30.02: 2.5%")

    def test_an_hts8_scope_takes_its_hts10_lines_from_the_current_structure(self) -> None:
        scope = codes(("85076000", "2017-01-01", "2026-12-31"))
        hts8, hts10 = drilldown.build_menu(STRUCTURE, scope, [duty("2024-09-27", "8507600010")],
                                           period_start=START, period_end=END)
        self.assertEqual(hts8["verdict"], drilldown.VERDICT_SAME)
        self.assertEqual(hts10["lines"], "8507.60.00.10; 8507.60.00.30; 8507.60.00.90")
        self.assertEqual(hts10["verdict"], drilldown.VERDICT_DRILL)

    def test_a_line_missing_from_the_current_structure_is_named(self) -> None:
        scope = codes(("85079080", "2017-01-01", "2026-12-31"))
        (hts8, *_) = drilldown.build_menu(STRUCTURE, scope, period_start=START, period_end=END)
        self.assertEqual(hts8["hts8_base_rate"], "8507.90.80: 不在现行 HTS 结构里")

    def test_rate_on_the_only_statistical_line_counts_as_the_hts8_rate(self) -> None:
        """8517.13: the search returns only 8517.13.00.00, with the rate on it."""

        structure = [{"htsno": "8517.13.00.00", "general": "Free", "units": ["No."]}]
        scope = [drilldown.ScopeCode("851713", START, END, ("no.",))]
        (hts10,) = drilldown.build_menu(structure, scope, period_start=START, period_end=END)
        self.assertEqual((hts10["level"], hts10["hts8_base_rate"]), ("HTS10", "8517.13.00: Free"))
        self.assertEqual(hts10["verdict"], drilldown.VERDICT_SAME)

    def test_structure_must_be_an_hts_rest_search_response(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            path.write_text(json.dumps({"htsno": "8507.60.00"}), encoding="utf-8")
            with self.assertRaisesRegex(drilldown.DrilldownError, "not an HTS REST search response"):
                drilldown.read_structure(path)
            path.write_text("[", encoding="utf-8")
            with self.assertRaisesRegex(drilldown.DrilldownError, "Cannot read"):
                drilldown.read_structure(path)


class DrilldownStudyHashTests(test_plan.ProjectTestCase):
    """[drilldown] enters study_sha256 (with the file's hash) and never the plan."""

    def write_structure(self) -> Path:
        path = self.root / "config" / "hts_structure.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(STRUCTURE), encoding="utf-8")
        return path

    def test_structure_changes_the_study_hash_but_not_the_plan(self) -> None:
        default_study, default_plan = self.load_study().study_sha256, test_plan.plan_for(self.paths)["plan_sha256"]
        path = self.write_structure()
        self.rewrite_study(test_plan.STUDY_TOML + '\n[drilldown]\nstructure = "config/hts_structure.json"\n')
        with_menu = self.load_study()
        self.assertNotEqual(with_menu.study_sha256, default_study)
        self.assertEqual(test_plan.plan_for(self.paths)["plan_sha256"], default_plan)
        path.write_text(json.dumps(STRUCTURE[:2]), encoding="utf-8")
        self.assertNotEqual(self.load_study().study_sha256, with_menu.study_sha256)

    def test_missing_or_invalid_drilldown_is_refused_at_load(self) -> None:
        self.rewrite_study(test_plan.STUDY_TOML + '\n[drilldown]\nstructure = "config/missing.json"\n')
        with self.assertRaisesRegex(StudyError, "Cannot read drilldown.structure"):
            self.load_study()
        self.write_structure()
        self.rewrite_study(test_plan.STUDY_TOML + '\n[drilldown]\nfile = "config/hts_structure.json"\n')
        with self.assertRaisesRegex(StudyError, "only structure"):
            self.load_study()


class DrilldownEndToEndTests(unittest.TestCase):
    """The offline lithium fixture with [drilldown]: table, manifest entry and report table."""

    setUp = test_features.TimelineEndToEndTests.setUp
    tearDown = test_features.TimelineEndToEndTests.tearDown
    run_study = test_features.TimelineEndToEndTests.run_study

    def test_menu_reaches_table_manifest_and_report(self) -> None:
        path = self.root / "config" / "hts_structure.json"
        path.write_text(json.dumps(STRUCTURE), encoding="utf-8")
        out = self.run_study('\n[drilldown]\nstructure = "config/hts_structure.json"\n')
        table = pd.read_csv(out / "tables" / "drilldown_menu.csv")
        self.assertEqual(list(table.columns), list(drilldown.MENU_COLUMNS))
        self.assertEqual(list(table["level"]), ["HTS8", "HTS10"])
        self.assertEqual(table.loc[0, "verdict"], drilldown.VERDICT_SAME)
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(manifest["outputs"]["drilldown_menu"])
        report = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("下钻菜单（只看产品和税率，不看贸易数据）", report)
        self.assertIn("| 8507.60 | HTS8 | 1 | 8507.60.00 |", report)

    def test_default_study_has_no_menu(self) -> None:
        out = self.run_study("")
        self.assertFalse((out / "tables" / "drilldown_menu.csv").exists())
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertNotIn("drilldown_menu", manifest["outputs"])


if __name__ == "__main__":
    unittest.main()
