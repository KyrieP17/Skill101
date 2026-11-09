"""Opt-in feature tables: focal split, segment series, first-vs-second unit check.

All codes and values are synthetic.
"""

from __future__ import annotations

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

import test_lithium_e2e as e2e  # noqa: E402  (module import, so its tests are not collected twice)
from usitc_pipeline import features as feature_module  # noqa: E402
from usitc_pipeline import plan as plan_module  # noqa: E402
from usitc_pipeline import report as report_module  # noqa: E402
from usitc_pipeline.analysis import build_origin_series, compare_origin_windows  # noqa: E402
from usitc_pipeline.dataweb import DataWebClient  # noqa: E402
from usitc_pipeline.study import Segment, StudyError, load_study  # noqa: E402
from usitc_pipeline.study_pipeline import analyze_study, render_study_charts, report_study  # noqa: E402


DATA = {
    # code: {year: {origin: (value, quantity, duty)}}; World = sum of origins.
    "1111110010": {"2024": {"China": (60, 6, 6), "Japan": (40, 4, 1)}, "2025": {"China": (30, 2, 9), "Japan": (50, 10, 1)}},
    "1111110020": {"2024": {"China": (80, 8, 4), "Japan": (20, 2, 0)}, "2025": {"China": (90, 9, 18), "Japan": (30, 3, 0)}},
}


def panel(quantity_scale: float = 1.0) -> pd.DataFrame:
    """Annual 2024-2025 and the same values in Jan and Feb of each year."""

    rows = []
    for code, years in DATA.items():
        for year, origins in years.items():
            world = [sum(values[index] for values in origins.values()) for index in range(3)]
            for frequency, period in (("annual", year), ("monthly", f"{year}-01"), ("monthly", f"{year}-02")):
                for origin, (value, quantity, duty) in origins.items():
                    rows.append(
                        {
                            "frequency": frequency, "period": period,
                            "period_status": "full_year" if frequency == "annual" else "monthly",
                            "hts_level": 10, "hts_code": code, "origin_name": origin,
                            "customs_value_usd": float(value), "quantity": quantity * quantity_scale,
                            "quantity_unit": "number" if quantity_scale == 1 else "kilograms",
                            "calculated_duty_usd": float(duty), "world_value_usd": float(world[0]),
                            "world_quantity": world[1] * quantity_scale, "world_calculated_duty_usd": float(world[2]),
                        }
                    )
    return pd.DataFrame(rows)


WINDOWS = [
    feature_module.Window("annual_2024_vs_2025", "annual", ("2024",), ("2025",)),
    feature_module.Window("ytd_02_2024_vs_2025", "monthly", ("2024-01", "2024-02"), ("2025-01", "2025-02")),
]


class FocalSplitTests(unittest.TestCase):
    def test_rows_add_up_and_carry_duty_dollars_and_weight(self) -> None:
        split = feature_module.build_focal_split(
            panel(), WINDOWS, focal_origin="China", quantity_unit="No.", quantity_comparable=True,
            weight_panel=panel(2.5), weight_unit="kg",
        )
        self.assertEqual(split["comparison"].unique().tolist(), ["annual_2024_vs_2025", "ytd_02_2024_vs_2025"])
        annual = split[split["comparison"].eq("annual_2024_vs_2025")].set_index("group")
        self.assertEqual(list(annual.index), ["focal", "other", "total"])
        self.assertEqual(annual.loc["total", "base_value_usd"], 200.0)
        self.assertEqual(annual.loc["focal", "base_value_usd"] + annual.loc["other", "base_value_usd"], 200.0)
        self.assertEqual(
            (annual.loc["focal", "base_calculated_duty_usd"], annual.loc["focal", "compare_calculated_duty_usd"]), (10.0, 27.0)
        )
        self.assertEqual(
            (annual.loc["total", "base_calculated_duty_usd"], annual.loc["total", "compare_calculated_duty_usd"]), (11.0, 28.0)
        )
        self.assertAlmostEqual(annual.loc["focal", "base_share_pct"], 70.0)
        self.assertAlmostEqual(annual.loc["focal", "compare_realized_duty_pct"], 27 / 120 * 100)
        self.assertEqual((annual.loc["focal", "base_quantity"], annual.loc["focal", "base_quantity_2"]), (14.0, 35.0))
        self.assertAlmostEqual(annual.loc["focal", "base_quantity_2_share_pct"], 70.0)
        ytd = split[split["comparison"].str.startswith("ytd")].set_index("group")
        self.assertEqual(ytd.loc["total", "base_value_usd"], 400.0)  # two months

    def test_incompletely_observed_window_is_left_out(self) -> None:
        partial = panel()[lambda frame: frame["period"].ne("2024-02")]
        split = feature_module.build_focal_split(
            partial, WINDOWS, focal_origin="China", quantity_unit="No.", quantity_comparable=True
        )
        self.assertEqual(split["comparison"].unique().tolist(), ["annual_2024_vs_2025"])
        self.assertNotIn("base_quantity_2", split)


class SegmentSeriesTests(unittest.TestCase):
    def test_each_segment_and_the_scope_total(self) -> None:
        segments = (Segment("A", ("1111110010",)), Segment("B", ("1111110020",)))
        series = feature_module.build_segment_series(
            panel(), segments, WINDOWS, focal_origin="China", weight_panel=panel(2.5), weight_unit="kg"
        )
        self.assertEqual(sorted(series["segment"].unique()), ["A", "B", "Scope total"])
        self.assertEqual(
            series[series["segment"].eq("A")]["period"].tolist(), ["2024", "2025", "2024-01..2024-02", "2025-01..2025-02"]
        )
        row = series[series["segment"].eq("A") & series["period"].eq("2025")].iloc[0]
        self.assertEqual(row["world_value_usd"], 80.0)
        self.assertAlmostEqual(row["focal_value_share_pct"], 37.5)
        self.assertAlmostEqual(row["focal_realized_duty_pct"], 30.0)
        self.assertAlmostEqual(row["focal_quantity_2_share_pct"], 2 / 12 * 100)
        total = series[series["segment"].eq("Scope total") & series["period"].eq("2025")].iloc[0]
        self.assertEqual(total["world_value_usd"], 200.0)


class QuantityCheckTests(unittest.TestCase):
    def test_opposite_signs_of_at_least_five_percent_are_flagged(self) -> None:
        weight = panel(2.5)
        # Code ...10: count +20% (10 -> 12) while weight falls to a tenth.
        mask = weight["hts_code"].eq("1111110010") & weight["period"].str.startswith("2025")
        weight.loc[mask, ["quantity", "world_quantity"]] = weight.loc[mask, ["quantity", "world_quantity"]] / 10
        check = feature_module.build_quantity_check(
            panel(), weight, WINDOWS, segments=(Segment("A", ("1111110010",)),), first_unit="No.", second_unit="kg",
            total_comparable=True,
        )
        annual = check[check["comparison"].eq("annual_2024_vs_2025")]
        flags = {(row.level, row.key): bool(row.conflict) for row in annual.itertuples()}
        self.assertEqual(
            flags,
            {("code", "1111110010"): True, ("code", "1111110020"): False, ("segment", "A"): True, ("total", "Scope total"): True},
        )
        row = annual.set_index("key").loc["1111110010"]
        self.assertAlmostEqual(row["quantity_growth_pct"], 20.0)
        self.assertAlmostEqual(row["quantity_2_growth_pct"], -88.0)

    def test_same_direction_or_small_moves_are_not_flagged(self) -> None:
        weight = panel(2.5)
        mask = weight["hts_code"].eq("1111110010") & weight["period"].str.startswith("2025")
        weight.loc[mask, ["quantity", "world_quantity"]] = weight.loc[mask, ["quantity", "world_quantity"]] * (0.97 / 1.2)
        check = feature_module.build_quantity_check(
            panel(), weight, WINDOWS[:1], first_unit="No.", second_unit="kg", total_comparable=False
        )
        self.assertFalse(check["conflict"].any())  # -3% by kg is under the 5% threshold
        self.assertNotIn("total", set(check["level"]))


class SecondUnitColumnsTests(unittest.TestCase):
    def test_second_unit_sits_beside_the_first(self) -> None:
        first, second = build_origin_series(panel()), build_origin_series(panel(2.5))
        merged = feature_module.add_second_unit_columns(first, second, keys=["frequency", "period", "origin_name"], kind="series")
        row = merged[merged["period"].eq("2024") & merged["origin_name"].eq("China")].iloc[0]
        self.assertEqual((row["quantity"], row["quantity_2"]), (14.0, 35.0))
        self.assertAlmostEqual(row["unit_value_2_usd"], 140 / 35)
        changes = compare_origin_windows(first, comparison="c", frequency="annual", base_periods=["2024"], compare_periods=["2025"])
        changes_2 = compare_origin_windows(second, comparison="c", frequency="annual", base_periods=["2024"], compare_periods=["2025"])
        both = feature_module.add_second_unit_columns(changes, changes_2, keys=["comparison", "origin_name"], kind="change")
        self.assertEqual(len(both), len(changes))
        china = both[both["origin_name"].eq("China")].iloc[0]
        self.assertAlmostEqual(china["base_quantity_2_share_pct"], 70.0)
        self.assertAlmostEqual(china["quantity_2_growth_pct"], china["quantity_growth_pct"])


class FeatureFindingTests(unittest.TestCase):
    def test_conflict_finding_and_duty_dollars(self) -> None:
        common = {"comparison": "annual_2024_vs_2025", "base_periods": "2024", "compare_periods": "2025",
                  "first_unit": "No.", "second_unit": "kg"}
        features = {
            "quantity_check": [
                {**common, "level": "code", "key": "8507600010", "quantity_growth_pct": 216.7, "quantity_2_growth_pct": -34.7, "conflict": True},
                {**common, "level": "total", "key": "Scope total", "quantity_growth_pct": 21.5, "quantity_2_growth_pct": 4.2, "conflict": False},
            ],
            "focal_split": [
                {**common, "group": group, "label": label, "base_calculated_duty_usd": base, "compare_calculated_duty_usd": compare}
                for group, label, base, compare in (("focal", "China", 1.10e9, 2.37e9), ("total", "All origins", 1.30e9, 3.21e9))
            ],
        }
        claims = {item["id"]: item["claim"] for item in report_module._feature_findings(features, "China", {})}
        self.assertIn("数量结论不可靠（按个 +216.7%，按 kg -34.7%）", claims["quantity_conflict_code_8507600010_annual_2024_vs_2025"])
        self.assertEqual([key for key in claims if key.startswith("quantity_conflict")], ["quantity_conflict_code_8507600010_annual_2024_vs_2025"])
        self.assertIn(
            "全部来源 13.0 亿美元 → 32.1 亿美元，China 11.0 亿美元 → 23.7 亿美元", claims["calculated_duty_annual_2024_vs_2025"]
        )


def display_frame() -> pd.DataFrame:
    """Display rows: months 2025-01, 2025-02 and 2025-04 (no March), full years 2024-2025, 2026 YTD."""

    data = {
        # (frequency, period, status): {origin: (value, quantity, duty)}
        ("monthly", "2025-01", "monthly"): {"China": (40, 4, 4), "India": (10, 2, 0), "Other reported origins": (50, 5, 1)},
        ("monthly", "2025-02", "monthly"): {"China": (10, 1, 2), "India": (40, 8, 0), "Other reported origins": (50, 5, 1)},
        ("monthly", "2025-04", "monthly"): {"China": (10, 1, 2), "India": (40, 8, 0), "Other reported origins": (150, 15, 1)},
        ("annual", "2024", "full_year"): {"China": (70, 7, 0), "India": (10, 2, 0), "Other reported origins": (20, 2, 0)},
        ("annual", "2025", "full_year"): {"China": (50, 5, 5), "India": (30, 6, 0), "Other reported origins": (20, 2, 0)},
        ("annual", "2026", "ytd"): {"China": (5, 1, 0), "India": (5, 1, 0), "Other reported origins": (5, 1, 0)},
    }
    rows = []
    for (frequency, period, status), origins in data.items():
        world = sum(value for value, _, _ in origins.values())
        for origin, (value, quantity, duty) in origins.items():
            rows.append({
                "frequency": frequency, "period": period, "period_status": status, "display_origin": origin,
                "customs_value_usd": float(value), "quantity": float(quantity), "calculated_duty_usd": float(duty),
                "world_value_usd": float(world), "quantity_comparable": origin != "Other reported origins",
            })
    return pd.DataFrame(rows)


TIMELINE = [
    {"date": "2025-02-10", "date_type": "effective", "measure": "Measure A", "rate_change": "+10%", "needs_check": True},
    {"date": "2025-06-01", "date_type": "announced", "measure": "Measure B", "rate_change": float("nan"), "needs_check": False},
]


class ShareDecompositionTests(unittest.TestCase):
    def build(self, **kwargs) -> pd.DataFrame:
        return feature_module.build_share_decomposition(
            display_frame(), [WINDOWS[1]], focal_origin="China", highlight_origins=["India"],
            quantity_unit="No.", timeline=TIMELINE, **kwargs,
        )

    def row(self, table: pd.DataFrame, kind: str, base: str, origin: str) -> pd.Series:
        return table[table["window_kind"].eq(kind) & table["base_periods"].eq(base) & table["display_origin"].eq(origin)].iloc[0]

    def test_windows_are_consecutive_periods_named_months_and_ytd(self) -> None:
        table = self.build(custom_windows=[("2025-02", "2025-04")])
        windows = table.drop_duplicates(["window_kind", "base_periods"])
        self.assertEqual(
            list(zip(windows["window_kind"], windows["base_periods"], windows["compare_periods"])),
            [("custom", "2025-02", "2025-04"), ("annual", "2024", "2025"), ("month", "2025-01", "2025-02")],
            "no 2025-02 -> 2025-04 month row (not consecutive), no 2026 (not a full year), no YTD (2024 months unobserved)",
        )
        self.assertEqual(table["display_origin"].drop_duplicates().tolist(), ["China", "India", "Other reported origins"])
        self.assertEqual(tuple(table.columns), feature_module.DECOMPOSITION_COLUMNS)

    def test_share_change_splits_into_own_and_others_and_adds_up(self) -> None:
        table = self.build(custom_windows=[("2025-02", "2025-04")])
        parts = table["share_change_from_own_pp"] + table["share_change_from_others_pp"]
        self.assertTrue(((parts - table["share_change_pp"]).abs() < 1e-9).all())
        china = self.row(table, "month", "2025-01", "China")
        self.assertAlmostEqual(china["share_change_pp"], -30.0)
        self.assertLess(china["share_change_from_own_pp"], china["share_change_from_others_pp"])
        # China's value is flat from February to April: the whole change comes from the others.
        flat = self.row(table, "custom", "2025-02", "China")
        self.assertAlmostEqual(flat["share_change_from_own_pp"], 0.0)
        self.assertAlmostEqual(flat["share_change_from_others_pp"], -5.0)
        self.assertEqual((flat["others_value_change_usd"], flat["world_value_change_usd"]), (100.0, 100.0))

    def test_value_change_splits_into_quantity_and_unit_value(self) -> None:
        table = self.build()
        china = self.row(table, "month", "2025-01", "China")
        self.assertAlmostEqual(china["value_change_from_quantity_usd"], -30.0)
        self.assertAlmostEqual(china["value_change_from_unit_value_usd"], 0.0)
        self.assertEqual(china["quantity_unit"], "No.")
        self.assertAlmostEqual(china["base_realized_duty_pct"], 10.0)
        annual = self.row(table, "annual", "2024", "China")
        self.assertAlmostEqual(
            annual["value_change_from_quantity_usd"] + annual["value_change_from_unit_value_usd"], annual["value_change_usd"]
        )
        other = self.row(table, "month", "2025-01", "Other reported origins")
        self.assertTrue(pd.isna(other["value_change_from_quantity_usd"]) and pd.isna(other["quantity_unit"]))

    def test_timeline_events_are_dated_inside_the_window(self) -> None:
        table = self.build(custom_windows=[("2025-02", "2025-04")])
        self.assertEqual(
            self.row(table, "month", "2025-01", "India")["timeline_events"],
            "2025-02-10 effective: Measure A (+10%) [to verify]",
        )
        self.assertEqual(
            self.row(table, "annual", "2024", "China")["timeline_events"],
            "2025-02-10 effective: Measure A (+10%) [to verify]; 2025-06-01 announced: Measure B",
        )
        self.assertNotIn("Measure B", self.row(table, "custom", "2025-02", "China")["timeline_events"])


class SegmentsAndSplitEndToEndTests(unittest.TestCase):
    """The offline lithium fixture with [[segments]] and [brief] focal_split."""

    def setUp(self) -> None:
        e2e.LithiumIonOfflineE2ETests.setUp(self)

    def tearDown(self) -> None:
        e2e.LithiumIonOfflineE2ETests.tearDown(self)

    def run_study(self, extra: str) -> Path:
        self.study.write_text(self.study.read_text(encoding="utf-8") + extra, encoding="utf-8")
        _, plan_path = plan_module.plan_study(self.config, self.study)
        client = DataWebClient(self.config, token=e2e.TOKEN, session=e2e.LithiumFixtureSession(self.fixture))
        plan_module.acquire_plan(self.config, plan_path, client=client, today=e2e.FIXED_TODAY)
        out = Path(analyze_study(self.config, self.study)["analysis_dir"])
        render_study_charts(self.config, self.study)
        report_study(self.config, self.study)
        return out

    def test_segments_and_focal_split_tables_reach_the_report(self) -> None:
        out = self.run_study(
            '\n[[segments]]\nlabel = "Cells"\ncodes = ["8507600010", "8507600020"]\n'
            '\n[[segments]]\nlabel = "Modules"\ncodes = ["8507600030"]\n'
            "\n[brief]\nfocal_split = true\n"
        )
        split = pd.read_csv(out / "tables" / "focal_split.csv")
        self.assertEqual(split["group"].tolist(), ["focal", "other", "total"])
        self.assertAlmostEqual(split["base_value_usd"].iloc[0] + split["base_value_usd"].iloc[1], split["base_value_usd"].iloc[2])
        self.assertTrue(split["quantity_unit"].isna().all(), "mixed No./kg units: no quantity in the split")
        series = pd.read_csv(out / "tables" / "segment_series.csv", dtype={"period": str})
        self.assertEqual(sorted(series["segment"].unique()), ["Cells", "Modules", "Scope total"])
        self.assertTrue(series[series["segment"].eq("Modules") & series["period"].eq("2017")].empty)
        report = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("焦点来源地、其他来源地和总计", report)
        self.assertIn("| 时期 | Cells 进口额 |", report)
        findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
        self.assertIn("calculated_duty_annual_2024_vs_2025", {item["id"] for item in findings["findings"]})
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({"focal_split", "segment_series"} & set(manifest["outputs"]), {"focal_split", "segment_series"})
        self.assertNotIn("quantity_check", manifest["outputs"])

    def test_segment_codes_must_be_in_the_scope_and_unique(self) -> None:
        original = self.study.read_text(encoding="utf-8")
        with self.assertRaisesRegex(Exception, "not included in the approved scope"):
            self.run_study('\n[[segments]]\nlabel = "Primary"\ncodes = ["8506500000"]\n')
        self.study.write_text(original, encoding="utf-8")
        with self.assertRaisesRegex(StudyError, "only one segment"):
            self.run_study('\n[[segments]]\nlabel = "A"\ncodes = ["8507600010"]\n[[segments]]\nlabel = "B"\ncodes = ["8507600010"]\n')

    def test_decomposition_is_off_by_default_and_reaches_findings_when_on(self) -> None:
        original = self.study.read_text(encoding="utf-8")
        default_hash = load_study(self.study, project_root=self.config.paths.root).study_sha256
        self.study.write_text(original + "\n[decomposition]\nenabled = false\n", encoding="utf-8")
        self.assertEqual(load_study(self.study, project_root=self.config.paths.root).study_sha256, default_hash)
        self.study.write_text(original, encoding="utf-8")
        out = self.run_study("\n[decomposition]\nenabled = true\n")
        self.assertNotEqual(load_study(self.study, project_root=self.config.paths.root).study_sha256, default_hash)
        table = pd.read_csv(out / "tables" / "share_decomposition.csv", dtype={"base_periods": str})
        self.assertEqual(set(table["window_kind"]), {"annual"}, "annual-only study: consecutive full years only")
        self.assertEqual(table["base_periods"].drop_duplicates().tolist()[:2], ["2017", "2018"])
        parts = table["share_change_from_own_pp"] + table["share_change_from_others_pp"]
        self.assertTrue(((parts - table["share_change_pp"]).abs() < 1e-9).all())
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertIn("share_decomposition", manifest["outputs"])
        findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual(len(findings["share_decomposition"]), len(table))

    def test_decomposition_windows_need_monthly_periods_and_enabled(self) -> None:
        original = self.study.read_text(encoding="utf-8")
        for extra, message in (
            ('\n[decomposition]\nwindows = [["2020-01", "2020-03"]]\n', "only with enabled = true"),
            ('\n[decomposition]\nenabled = true\nwindows = [["2020-01", "2020-03"]]\n', r"inside \[periods\] monthly"),
            ('\n[decomposition]\nenabled = true\nwindows = [["2020-1", "2020-03"]]\n', "YYYY-MM"),
            ('\n[decomposition]\nenabled = "yes"\n', "true or false"),
            ('\n[decomposition]\nenabled = true\nmonths = 3\n', "supports only enabled, windows"),
        ):
            with self.subTest(extra=extra):
                self.study.write_text(original + extra, encoding="utf-8")
                with self.assertRaisesRegex(StudyError, message):
                    load_study(self.study, project_root=self.config.paths.root)


if __name__ == "__main__":
    unittest.main()


class BothBasisPlanTests(unittest.TestCase):
    """unit = "both" asks DataWeb exactly what unit = "second" asks, so it reuses that cache."""

    def setUp(self) -> None:
        import tempfile

        import test_plan
        from test_scope import history_rows

        self.test_plan = test_plan
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name).resolve()
        rows = [{**row, "second_unit": "kg" if row["decision"] == "include" else ""} for row in history_rows()]
        self.paths = test_plan.build_project(root, rows)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def plan_with(self, unit: str) -> dict:
        self.paths["study"].write_text(self.test_plan.STUDY_TOML + f'\n[quantity]\nunit = "{unit}"\n', encoding="utf-8")
        return self.test_plan.plan_for(self.paths)

    def test_both_and_second_share_every_payload(self) -> None:
        second, both = self.plan_with("second"), self.plan_with("both")
        self.assertEqual([job["payload_sha256"] for job in both["jobs"]], [job["payload_sha256"] for job in second["jobs"]])
        self.assertEqual([job["cache_stem"] for job in both["jobs"]], [job["cache_stem"] for job in second["jobs"]])
        self.assertTrue(all(job["second_quantity"] for job in both["jobs"]))
        self.assertIn("CONS_SEC_UNIT_QUANT", both["measurement"]["measures"])
