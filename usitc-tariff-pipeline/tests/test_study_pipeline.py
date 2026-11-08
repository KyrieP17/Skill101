"""Regression tests for the generic, scope-driven analysis.

The laptop case is run as an ordinary study (a labeled legacy replay) and
must reproduce the original research.toml baseline: three stable artifacts
byte for byte, and the baseline's event, reallocation and coverage numbers.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
from pathlib import Path
import json
import re
import shutil
import sys
import tempfile
import tomllib
import unittest

import pandas as pd


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from usitc_pipeline.analysis import (  # noqa: E402
    build_display_series,
    build_event_path,
    build_origin_series,
    derive_data_through,
)
from usitc_pipeline.charts import plot_study_event_path  # noqa: E402
from usitc_pipeline.config import load_config  # noqa: E402
from usitc_pipeline.plan import period_codes  # noqa: E402
from usitc_pipeline import report as report_module  # noqa: E402
from usitc_pipeline import study_pipeline  # noqa: E402
from usitc_pipeline.analysis import ValidationError  # noqa: E402
from usitc_pipeline.study import EventWindow  # noqa: E402
from usitc_pipeline.study_pipeline import (  # noqa: E402
    _label_periods,
    _within_data_through,
    analyze_study,
    render_study_charts,
    report_study,
)


LAPTOP_REPLAY = Path("config/replays/laptops-legacy-baseline.toml")
LAPTOP_FILES = (
    "config/research.toml",
    "config/policy_events.csv",
    "config/hts_naics_proxy.csv",
    "config/studies/laptops-2017-2026.toml",
    LAPTOP_REPLAY.as_posix(),
    "scopes/laptops/v2/reviewed.csv",
)
# Byte-for-byte artifacts of the original baseline, re-run with the legacy
# pipeline on 2026-11-06 (outputs/tables/laptop_monthly_panel.csv and
# outputs/charts/03_laptop_event_path.{csv,events.csv}).
GOLDEN = {
    "tables/monthly_panel.csv": "72dff5e3744b0d99eb8e2daaeeeee4d0966db15f9e716a0793fba5cf18dea12b",
    "charts/03_event_path.csv": "c0a8ef8a1705134c53c035f9be0287794fe2b3035eb154b27e4bf12403d2a7f3",
    "charts/03_event_path.events.csv": "1f1e4df07a9385167da5acdb8905ffd5bfe12c1a182571897c14545e5fc7306f",
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def laptop_snapshots() -> list[str]:
    replay = tomllib.loads((PROJECT_ROOT / LAPTOP_REPLAY).read_text(encoding="utf-8"))
    raw = [
        entry["template"].format(origins=label, year=year)
        for entry in replay["snapshots"]
        for year in entry["years"]
        for label in ("all", "world")
    ]
    return raw + [name.removesuffix(".json") + ".meta.json" for name in raw]


def laptop_data_present() -> bool:
    try:
        return all((PROJECT_ROOT / name).is_file() for name in (*LAPTOP_FILES, *laptop_snapshots()))
    except OSError:
        return False


@unittest.skipUnless(laptop_data_present(), "the laptop baseline snapshots are not in this checkout")
class LaptopReplayRegressionTests(unittest.TestCase):
    """The laptop study, run by the generic engine in a throwaway copy of the project."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        root = Path(cls._tmp.name).resolve()
        for name in (*LAPTOP_FILES, *laptop_snapshots()):
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(PROJECT_ROOT / name, target)
        cls.config = load_config(root / "config" / "research.toml")
        replay = root / LAPTOP_REPLAY
        cls.result = analyze_study(cls.config, replay=replay)
        cls.charts = render_study_charts(cls.config, replay=replay)
        cls.report = report_study(cls.config, replay=replay)
        cls.out = Path(cls.result["analysis_dir"])

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_stable_artifacts_match_the_baseline_byte_for_byte(self) -> None:
        for name, expected in GOLDEN.items():
            with self.subTest(name):
                self.assertEqual(digest(self.out / name), expected)

    def test_event_numbers_match_the_baseline(self) -> None:
        metrics = json.loads((self.out / "tables" / "event_metrics.json").read_text(encoding="utf-8"))
        windows, summary = metrics["event_windows"], metrics["summary"]
        self.assertEqual(
            [summary[key] for key in ("low_window", "low_month", "reference_window", "latest_window", "latest_max_month")],
            ["shock", "2025-11", "baseline", "recovery", "2026-07"],
        )
        for actual, expected in (
            (windows["baseline"]["weighted_focal_value_share_pct"], 66.00969843769846),
            (summary["low_share_pct"], 1.7371872294070736),
            (summary["low_vs_reference_pp"], -64.27251120829139),
            (summary["latest_max_share_pct"], 11.403680183967747),
            (summary["latest_max_vs_low_pp"], 9.666492954560674),
            (windows["pre_covid"]["share_slope_pp_per_month"], -0.12531536269653848),
            (windows["covid"]["share_slope_pp_per_month"], 0.07506706032157315),
        ):
            self.assertAlmostEqual(actual, expected, places=10)

    def test_product_reallocation_matches_the_baseline(self) -> None:
        table = pd.read_csv(self.out / "tables" / "product_reallocation.csv", dtype={"hts_code": "string"})
        row = table.set_index("hts_code").loc["847130"]
        self.assertEqual((row["top_gaining_origin"], row["pattern"]), ("Vietnam", "share_loss_with_demand_contraction"))
        for column, expected in (
            ("focal_share_base_pct", 66.35226541109938),
            ("focal_share_compare_pct", 18.986385622802963),
            ("focal_share_change_pp", -47.36587978829642),
            ("focal_import_value_growth_pct", -70.88196880724563),
            ("focal_import_quantity_growth_pct", -70.01446606520875),
            ("world_import_value_growth_pct", 1.7596172506834762),
            ("world_import_quantity_growth_pct", -0.35731862980920015),
            ("top_origin_share_gain_pp", 33.609384025104674),
        ):
            self.assertAlmostEqual(float(row[column]), expected, places=10, msg=column)

    def test_monthly_coverage_matches_the_baseline(self) -> None:
        coverage = pd.read_csv(self.out / "tables" / "coverage_audit.csv", dtype=str)
        monthly = coverage[coverage["frequency"].eq("monthly")].iloc[0]
        self.assertEqual(
            [monthly[key] for key in ("first_observed_period", "last_observed_period", "observed_period_count", "missing_period_count")],
            ["2017-01", "2026-08", "116", "0"],
        )
        self.assertEqual(self.result["data_through"], {"annual": "2025", "monthly": "2026-08"})

    def test_replay_is_labeled_everywhere_and_never_lands_in_gated_runs(self) -> None:
        self.out.relative_to(self.config.paths.outputs / "replays" / "laptops-legacy-baseline")
        self.assertFalse((self.config.paths.outputs / "runs").exists())
        manifest = json.loads((self.out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["gate"]["mode"], "legacy_replay")
        self.assertFalse(manifest["gate"]["scope_approved"])
        findings = json.loads((self.out / "findings.json").read_text(encoding="utf-8"))
        self.assertTrue({"legacy_replay", "continuity_unverified"} <= {item["id"] for item in findings["findings"]})
        report = Path(self.report["report"]).read_text(encoding="utf-8")
        self.assertIn("回放结果，不是已批准研究", report)
        self.assertIn("| HTS 连续性 | **未核实** |", report)

    def test_every_chart_has_chart_data_and_the_report_has_five_to_eight_key_points(self) -> None:
        chart_manifest = json.loads((self.out / "chart_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(self.charts), len(chart_manifest["charts"]))
        for chart in chart_manifest["charts"]:
            with self.subTest(chart["stem"]):
                self.assertTrue((self.out / chart["png"]["path"]).is_file())
                self.assertTrue((self.out / chart["chart_data"]["path"]).is_file())
        # One HTS6 code: the product-reallocation chart has nothing to compare.
        self.assertEqual(set(chart_manifest["skipped"]), {"02_product_reallocation"})
        key_points = Path(self.report["key_points"]).read_text(encoding="utf-8").splitlines()
        key_points = [line for line in key_points if line[:1].isdigit()]
        self.assertTrue(5 <= len(key_points) <= 8, key_points)


class DataThroughTests(unittest.TestCase):
    def test_incomplete_active_code_crops_every_later_month_from_analysis_input(self) -> None:
        world = pd.DataFrame(
            [
                {
                    "period": period,
                    "frequency": "monthly",
                    "hts_code": code,
                    "origin_name": "World",
                    "customs_value_usd": value,
                }
                for period, code, value in (
                    ("2025-01", "A", 10),
                    ("2025-01", "B", 20),
                    ("2025-02", "A", 11),
                    ("2025-02", "B", 21),
                    ("2025-03", "A", 12),
                    # B disappears in March while A continues in April.
                    ("2025-04", "A", 13),
                    ("2025-04", "B", 23),
                )
            ]
        )
        plan = {"jobs": [{"frequency": "monthly", "years": [2025], "codes": ["A", "B"], "break_out_origins": False}]}
        data_through = derive_data_through(world, period_codes(plan))
        self.assertEqual(data_through, {"monthly": "2025-02"})
        kept = world.loc[_within_data_through(world, data_through), "period"].unique().tolist()
        self.assertEqual(kept, ["2025-01", "2025-02"])

    def test_partial_last_year_is_never_labeled_a_full_year(self) -> None:
        panel = pd.DataFrame({"frequency": ["annual", "annual", "monthly"], "period": ["2024", "2025", "2025-08"]})
        cases = (
            ({"annual": "2025", "monthly": "2025-08"}, None, "ytd_through_08"),  # months known: same-period YTD
            ({"annual": "2025", "monthly": "2025-12"}, None, "full_year"),  # December observed
            ({"annual": "2025"}, date(2026, 3, 1), "full_year"),  # annual-only, retrieved after February
            ({"annual": "2025"}, date(2025, 12, 15), "partial_year_unverified"),  # retrieved too early
            ({"annual": "2025"}, None, "partial_year_unverified"),  # retrieval date unknown
        )
        for data_through, retrieved_on, expected in cases:
            with self.subTest(data_through=data_through, retrieved_on=retrieved_on):
                status = _label_periods(panel, data_through, retrieved_on).set_index("period")["period_status"]
                self.assertEqual((status["2024"], status["2025"]), ("full_year", expected))


class GenericEventPathTests(unittest.TestCase):
    def test_non_china_focal_origin_uses_neutral_windows_and_renders(self) -> None:
        panel = pd.DataFrame(
            {
                "frequency": ["monthly"] * 4,
                "period": ["2025-01", "2025-01", "2025-02", "2025-02"],
                "period_status": ["monthly"] * 4,
                "hts_code": ["850760"] * 4,
                "origin_name": ["Japan", "China", "Japan", "China"],
                "customs_value_usd": [30.0, 70.0, 40.0, 60.0],
                "quantity": [3.0, 7.0, 4.0, 6.0],
                "calculated_duty_usd": [0.0, 7.0, 0.0, 6.0],
                "quantity_unit": ["number"] * 4,
                "quantity_comparable": [True] * 4,
                "world_value_usd": [100.0] * 4,
                "world_quantity": [10.0] * 4,
                "world_calculated_duty_usd": [7.0, 7.0, 6.0, 6.0],
            }
        )
        display = build_display_series(build_origin_series(panel), focal_origin="Japan", highlight_origins=("China",))
        monthly, metrics = build_event_path(
            display,
            focal_origin="Japan",
            windows=(EventWindow("observed", "Observed interval", "2025-01", "2025-02"),),
        )

        self.assertEqual(metrics["focal_origin"], "Japan")
        self.assertEqual(metrics["interpretation"], "descriptive_event_windows_no_causal_claim")
        self.assertAlmostEqual(metrics["event_windows"]["observed"]["weighted_focal_value_share_pct"], 35.0)
        self.assertEqual(metrics["summary"]["low_month"], "2025-01")
        self.assertNotIn("reference_window", metrics["summary"])
        self.assertFalse(any(str(key).startswith("china_") for key in metrics))

        events = pd.DataFrame([{"date": "2025-02-01", "short_label": "Context marker", "status": "context"}])
        with tempfile.TemporaryDirectory() as raw_directory:
            output = Path(raw_directory)
            plot_study_event_path(
                monthly,
                events,
                output,
                title="Japan-focused battery sourcing",
                focal_origin="Japan",
                product_name="Lithium-ion batteries",
                windows=[{"id": "observed", "label": "Observed interval", "start": "2025-01", "end": "2025-02"}],
            )
            for name in ("03_event_path.png", "03_event_path.csv", "03_event_path.events.csv", "03_event_path.windows.csv"):
                self.assertTrue((output / name).is_file(), name)


class SecondUnitReportTests(unittest.TestCase):
    """The report says a second unit exists and was left for later; it never analyzes it."""

    @staticmethod
    def findings(**identity_extra):
        identity = {"quantity_unit": "no.", "quantity_comparable": True, "data_through": {"annual": "2025"}, "gate": {}}
        tables = {
            "scope_series": pd.DataFrame({"frequency": ["annual"], "period_status": ["full_year"], "quantity_comparable": [True]}),
            "world_check": pd.DataFrame({"world_value_gap_pct": [0.0], "status": ["ok"]}),
            "coverage_audit": pd.DataFrame({"hts_code": ["850760"], "missing_period_count": [0], "missing_periods": [""]}),
        }
        return report_module.build_findings(
            identity={**identity, **identity_extra}, focal_origin="China", validation={}, tables=tables,
            event_metrics={}, skips={},
        )

    def test_second_unit_is_reported_as_left_for_later(self) -> None:
        claims = {item["id"]: item for item in self.findings(second_unit="kg", second_units=["kg"])["findings"]}
        self.assertEqual(claims["second_unit"]["label"], "Unknown")
        self.assertIn("第二计量单位 kg", claims["second_unit"]["claim"])
        self.assertIn("留待后续分析", claims["second_unit"]["claim"])

        partial = {item["id"]: item for item in self.findings(second_unit=None, second_units=["kg"])["findings"]}
        self.assertIn("部分编码", partial["second_unit"]["claim"])
        none = {item["id"] for item in self.findings(second_unit=None, second_units=[])["findings"]}
        self.assertNotIn("second_unit", none)

    def test_report_header_shows_the_second_unit(self) -> None:
        identity = {
            "study_title": "Synthetic", "product_name": "Batteries", "hts_level": 6, "included_codes": ["850760"],
            "scope_id": "batteries", "scope_version": "1", "scope_sha256": "a" * 64, "study_id": "batteries-2024-2025",
            "study_sha256": "b" * 64, "plan_sha256": "c" * 64, "data_through": {"annual": "2025"}, "gate": {},
            "second_unit": "kg", "second_units": ["kg"],
        }
        findings = {"identity": identity, "findings": [], "key_points": [], "evidence_labels": {}, "boundaries": []}
        with tempfile.TemporaryDirectory() as directory:
            report_path, _ = report_module.render_report(findings, {}, validation={}, output_dir=Path(directory))
            self.assertIn("| 第二计量单位 | kg，未取，留待后续分析 |", report_path.read_text(encoding="utf-8"))
            identity.update(quantity_basis="second", quantity_unit="kg", first_unit="no.")
            report_path, _ = report_module.render_report(findings, {}, validation={}, output_dir=Path(directory))
            text = report_path.read_text(encoding="utf-8")
            self.assertIn("| 数量口径 | 第二计量单位 kg（第一单位 no. 不用于数量和单位价值） |", text)
            self.assertNotIn("未取", text)
            identity.update(second_unit=None, second_units=[], quantity_basis=None)
            report_path, _ = report_module.render_report(findings, {}, validation={}, output_dir=Path(directory))
            self.assertNotIn("第二计量单位", report_path.read_text(encoding="utf-8"))


class SecondQuantityBasisTests(unittest.TestCase):
    """A second-basis study analyzes kg in place of the first unit and says so."""

    def test_second_basis_swaps_the_quantity_columns(self) -> None:
        tidy = pd.DataFrame(
            {"quantity": [5.0], "quantity_unit": ["number"], "quantity_2": [80.0], "quantity_2_unit": ["kilograms"]}
        )
        swapped = study_pipeline._on_quantity_basis(tidy, "second")
        self.assertEqual(list(swapped.columns), ["quantity", "quantity_unit"])
        self.assertEqual((swapped.loc[0, "quantity"], swapped.loc[0, "quantity_unit"]), (80.0, "kilograms"))
        self.assertEqual(study_pipeline._on_quantity_basis(tidy, "first").loc[0, "quantity"], 5.0)
        with self.assertRaisesRegex(ValidationError, "Second Unit of Quantity"):
            study_pipeline._on_quantity_basis(tidy[["quantity", "quantity_unit"]], "second")

    def test_report_states_the_second_basis(self) -> None:
        claims = {
            item["id"]: item
            for item in SecondUnitReportTests.findings(
                quantity_unit="kg", quantity_basis="second", first_unit="no.", second_unit="kg", second_units=["kg"]
            )["findings"]
        }
        self.assertIn("第二计量单位 kg", claims["quantity_basis"]["claim"])
        self.assertNotIn("second_unit", claims)


class GenericEngineTests(unittest.TestCase):
    """A new product needs only a scope and a study: no module holds a product's codes."""

    LEGACY = {"pipeline.py"}  # the original research.toml baseline, kept only to reproduce it

    def test_generic_modules_hard_code_no_hts_code(self) -> None:
        literal = re.compile(r"[\"']\d{6,10}[\"']")
        for path in sorted((SRC_DIR / "usitc_pipeline").glob("*.py")):
            if path.name in self.LEGACY:
                continue
            with self.subTest(module=path.name):
                self.assertEqual(literal.findall(path.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
