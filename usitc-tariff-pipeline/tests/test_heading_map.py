"""HTS4 context map: planned, never acquired without the user's separate approval.

All codes and values are synthetic.
"""

from __future__ import annotations

import copy
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

import test_features  # noqa: E402  (module import, so its tests are not collected twice)
import test_lithium_e2e as e2e  # noqa: E402
import test_plan  # noqa: E402
from usitc_pipeline import charts as chart_module  # noqa: E402
from usitc_pipeline import features as feature_module  # noqa: E402
from usitc_pipeline import plan as plan_module  # noqa: E402
from usitc_pipeline.dataweb import DataWebClient, QueryJob, QuerySpec, build_payload, query_record  # noqa: E402
from usitc_pipeline.scope import ScopeGateError  # noqa: E402
from usitc_pipeline.study import StudyError  # noqa: E402


class ValueAndDutyQueryTests(unittest.TestCase):
    def test_default_payload_and_metadata_keep_their_shape(self) -> None:
        spec = QuerySpec(hts_level=10, codes=("8507600010",), years=(2024,), frequency="annual", break_out_origins=True)
        measures = build_payload(spec)["searchOptions"]["componentSettings"]["dataToReport"]
        self.assertEqual(measures, ["CONS_CUSTOMS_VALUE", "CONS_FIR_UNIT_QUANT", "CONS_CALC_DUTY"])
        self.assertNotIn("include_quantity", query_record(spec))

    def test_context_query_asks_for_value_and_duty_only(self) -> None:
        spec = QuerySpec(hts_level=6, codes=("850760",), years=(2024,), frequency="annual",
                         break_out_origins=False, include_quantity=False)
        measures = build_payload(spec)["searchOptions"]["componentSettings"]["dataToReport"]
        self.assertEqual(measures, ["CONS_CUSTOMS_VALUE", "CONS_CALC_DUTY"])
        self.assertFalse(query_record(spec)["include_quantity"])


CONTEXT = '\n[context]\nhts4 = true\nhts6 = ["111111", "111112"]\n'


class ContextPlanTests(test_plan.ProjectTestCase):
    def test_context_jobs_sit_beside_the_scope_jobs_and_need_approval(self) -> None:
        default = test_plan.plan_for(self.paths)
        self.rewrite_study(test_plan.STUDY_TOML + CONTEXT)
        plan = test_plan.plan_for(self.paths)
        self.assertEqual(plan["jobs"], default["jobs"])
        self.assertNotIn("context", default)
        context = plan["context"]
        self.assertTrue(context["needs_user_approval"])
        self.assertEqual(context["hts4"], ["1111"])
        self.assertEqual(context["hts6"], ["111111", "111112"])
        self.assertTrue(context["jobs"])
        for job in context["jobs"]:
            self.assertEqual((job["hts_level"], job["include_quantity"]), (6, False))
            self.assertIn("/context/hts6_", job["cache_stem"])
        self.assertEqual(sum(job["break_out_origins"] for job in context["jobs"]), len(context["jobs"]) // 2)

    def test_context_codes_must_cover_the_scope_and_stay_in_its_headings(self) -> None:
        self.rewrite_study(test_plan.STUDY_TOML + '\n[context]\nhts4 = true\nhts6 = ["111112"]\n')
        with self.assertRaisesRegex(ScopeGateError, "missing \\['111111'\\]"):
            test_plan.plan_for(self.paths)
        self.rewrite_study(test_plan.STUDY_TOML + '\n[context]\nhts4 = true\nhts6 = ["111111", "222222"]\n')
        with self.assertRaisesRegex(ScopeGateError, "outside \\['222222'\\]"):
            test_plan.plan_for(self.paths)

    def test_context_table_is_validated_at_load(self) -> None:
        for extra, message in (
            ("\n[context]\nhts4 = true\n", "needs hts6"),
            ('\n[context]\nhts6 = ["111111"]\n', "only with hts4"),
            ('\n[context]\nhts4 = true\nhts6 = ["1111"]\n', "distinct 6-digit"),
            ('\n[context]\nhts4 = true\nhts6 = ["111111", "111111"]\n', "duplicates"),
        ):
            self.rewrite_study(test_plan.STUDY_TOML + extra)
            with self.assertRaisesRegex(StudyError, message):
                self.load_study()


def _frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for period, values in (("2024", {"850760": 100.0, "850710": 300.0}), ("2025", {"850760": 150.0, "850710": 250.0})):
        for code, value in values.items():
            rows.append({"frequency": "annual", "period": period, "hts_code": code, "origin_name": "World",
                         "customs_value_usd": value, "calculated_duty_usd": value * 0.1})
    world = pd.DataFrame(rows)
    breakout = world.assign(origin_name="China", customs_value_usd=world["customs_value_usd"] / 2)
    return breakout, world


class HeadingMapTableTests(unittest.TestCase):
    def test_rows_per_hts6_plus_heading_total(self) -> None:
        breakout, world = _frames()
        window = feature_module.Window("annual_2024_vs_2025", "annual", ("2024",), ("2025",))
        table = feature_module.build_heading_map(breakout, world, [window], focal_origin="China", scope_hts6={"850760"})
        self.assertEqual(list(table.columns), list(feature_module.HEADING_MAP_COLUMNS))
        rows = table.set_index("hts6")
        self.assertEqual(list(rows.index), ["850710", "850760", "HTS4 total"])
        self.assertTrue(rows.loc["850760", "in_scope"])
        self.assertFalse(rows.loc["HTS4 total", "in_scope"])
        self.assertAlmostEqual(rows.loc["850760", "value_growth_pct"], 50.0)
        self.assertAlmostEqual(rows.loc["850760", "share_of_heading_pct"], 37.5)
        self.assertAlmostEqual(rows.loc["850760", "focal_share_pct"], 50.0)
        self.assertAlmostEqual(rows.loc["850760", "realized_duty_pct"], 10.0)
        self.assertAlmostEqual(rows.loc["HTS4 total", "value_usd"], 400.0)

    def test_window_not_fully_observed_is_skipped(self) -> None:
        breakout, world = _frames()
        window = feature_module.Window("annual_2023_vs_2025", "annual", ("2023",), ("2025",))
        self.assertTrue(feature_module.build_heading_map(breakout, world, [window], focal_origin="China",
                                                         scope_hts6={"850760"}).empty)

    def test_chart_has_its_csv(self) -> None:
        breakout, world = _frames()
        window = feature_module.Window("annual_2024_vs_2025", "annual", ("2024",), ("2025",))
        table = feature_module.build_heading_map(breakout, world, [window], focal_origin="China", scope_hts6={"850760"})
        labels = chart_module.ChartLabels(product="Synthetic", title="t", focal_origin="China",
                                          highlight_origins=(), quantity_unit="no.", source="Source: synthetic.")
        with tempfile.TemporaryDirectory() as tmp:
            png = chart_module.plot_heading_map(table, tmp, labels)
            self.assertTrue(png.is_file())
            plotted = pd.read_csv(Path(tmp) / "11_heading_map.csv", dtype={"hts6": str})
            self.assertEqual(list(plotted["hts6"]), ["850760", "850710"])


class HeadingMapEndToEndTests(unittest.TestCase):
    """The offline lithium fixture with [context] hts4: never fetched, then a simulated approved fetch."""

    setUp = test_features.TimelineEndToEndTests.setUp
    tearDown = test_features.TimelineEndToEndTests.tearDown
    run_study = test_features.TimelineEndToEndTests.run_study
    CONTEXT = '\n[context]\nhts4 = true\nhts6 = ["850710", "850760"]\n'

    def test_acquire_does_not_fetch_context_jobs_and_the_report_says_so(self) -> None:
        session = e2e.LithiumFixtureSession(self.fixture)
        self.study.write_text(self.study.read_text(encoding="utf-8") + self.CONTEXT, encoding="utf-8")
        _, plan_path = plan_module.plan_study(self.config, self.study)
        client = DataWebClient(self.config, token=e2e.TOKEN, session=session)
        result = plan_module.acquire_plan(self.config, plan_path, client=client, today=e2e.FIXED_TODAY)
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        self.assertEqual(result["context_jobs_not_fetched"], len(plan["context"]["jobs"]))
        self.assertTrue(all(len(code) == 10 for call in session.calls for code in call["codes"]))
        from usitc_pipeline.study_pipeline import analyze_study, render_study_charts, report_study

        out = Path(analyze_study(self.config, self.study)["analysis_dir"])
        render_study_charts(self.config, self.study)
        report_study(self.config, self.study)
        self.assertFalse((out / "tables" / "heading_map.csv").exists())
        findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
        claim = next(item for item in findings["findings"] if item["id"] == "heading_map_not_fetched")
        self.assertIn("HTS4 地图：未取数", claim["claim"])
        charts = json.loads((out / "chart_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("11_heading_map", json.dumps(charts))
        self.assertIn("HTS4 地图：未取数", (out / "report.md").read_text(encoding="utf-8"))

    def test_after_a_separately_approved_fetch_the_map_is_built(self) -> None:
        self.fixture = copy.deepcopy(self.fixture)
        source = self.fixture["codes"]
        merged = copy.deepcopy(next(iter(source.values())))
        merged["years"] = {}
        for record in source.values():
            for year, origins in record["years"].items():
                merged["years"].setdefault(year, copy.deepcopy(origins))
        everyone = {origin for origins in merged["years"].values() for origin in origins}
        for origins in merged["years"].values():
            width = len(next(iter(origins.values())))
            for origin in everyone - set(origins):
                origins[origin] = [0] * width
        source["850760"] = merged
        source["850710"] = merged
        self.study.write_text(self.study.read_text(encoding="utf-8") + self.CONTEXT, encoding="utf-8")
        _, plan_path = plan_module.plan_study(self.config, self.study)
        client = DataWebClient(self.config, token=e2e.TOKEN, session=e2e.LithiumFixtureSession(self.fixture))
        plan_module.acquire_plan(self.config, plan_path, client=client, today=e2e.FIXED_TODAY)
        plan = json.loads(Path(plan_path).read_text(encoding="utf-8"))
        for job in plan["context"]["jobs"]:  # stands in for the user's separate approval and fetch
            client.fetch(QueryJob(cache_stem=job["cache_stem"], spec=plan_module._spec(job)),
                         provenance={"approved_codes": job["codes"], "context": True})
        from usitc_pipeline.study_pipeline import analyze_study, render_study_charts

        out = Path(analyze_study(self.config, self.study)["analysis_dir"])
        render_study_charts(self.config, self.study)
        table = pd.read_csv(out / "tables" / "heading_map.csv", dtype={"hts6": str})
        self.assertIn("HTS4 total", set(table["hts6"]))
        self.assertTrue(table.loc[table["hts6"].eq("850760"), "in_scope"].all())
        self.assertTrue((out / "charts" / "11_heading_map.png").is_file())
        self.assertTrue((out / "charts" / "11_heading_map.csv").is_file())


if __name__ == "__main__":
    unittest.main()
