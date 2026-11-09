"""Opt-in study features: tariff timeline, focal split, segments, kg beside the first unit.

Every feature is off by default, so a study that does not ask for one keeps
its plan, study hash and outputs.  All codes and values are synthetic.
"""

from __future__ import annotations

import csv
from datetime import date
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
for entry in (PROJECT_ROOT / "src", TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import test_lithium_e2e as e2e  # noqa: E402  (module import, so its tests are not collected twice)
import test_plan  # noqa: E402
from usitc_pipeline import charts as chart_module  # noqa: E402
from usitc_pipeline import plan as plan_module  # noqa: E402
from usitc_pipeline import report as report_module  # noqa: E402
from usitc_pipeline import registry as registry_module  # noqa: E402
from usitc_pipeline import timeline as timeline_module  # noqa: E402
from usitc_pipeline.dataweb import DataWebClient  # noqa: E402
from usitc_pipeline.study import StudyError  # noqa: E402
from usitc_pipeline.study_pipeline import analyze_study, render_study_charts, report_study  # noqa: E402


HEADER = ",".join(timeline_module.TIMELINE_COLUMNS)
PRODUCT_ROWS = (
    "2019-09-01,effective,Synthetic duty A,+15%,,China,memory,,draft,\n"
    "2024-05-14,announced,Synthetic review,,,China,memory,,draft,\n"
    "2024-09-27,effective,Synthetic duty B,7.5% -> 25%,{code},China,HTS archive,"
    "https://hts.usitc.gov/reststop/file?release=x,draft,\n"
)
COUNTRY_ROWS = (
    "2025-02-04,effective,Synthetic country duty,+10%,,China; Hong Kong,FR,https://www.federalregister.gov/d/1,draft,\n"
    "2025-04-05,effective,Synthetic reciprocal duty,+99%,,All origins,FR,https://www.federalregister.gov/d/1,draft,\n"
)


def write_timeline(path: Path, rows: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER + "\n" + rows, encoding="utf-8")
    return path


def write_synthetic_registry(root: Path) -> None:
    directory = root / "config" / "tariff_registry"
    directory.mkdir(parents=True, exist_ok=True)
    sources = [
        {"source_id": "fr-1", "kind": "federal_register", "citation": "1 FR 1", "title": "Synthetic",
         "document_number": "1", "publication_date": "2025-01-01", "signing_date": "",
         "url": "https://www.federalregister.gov/d/1", "sha256": "a" * 64,
         "retrieved": "2026-11-08", "notes": ""},
    ]
    measures = []
    for measure_id, day, heading, origins in (
        ("duty-a", "2019-09-01", "9903.88.15", "China"),
        ("review", "2024-05-14", "9903.91.01", "China"),
        ("duty-b", "2024-09-27", "9903.91.02", "China"),
        ("country-duty", "2025-02-04", "9903.01.20", "China; Hong Kong"),
        ("reciprocal", "2025-04-05", "9903.01.25", "All origins"),
    ):
        measures.append(
            {"measure_id": measure_id, "date": day, "date_type": "effective", "authority": "Synthetic",
             "headings": heading, "change": "+10%", "origins": origins, "products": "list", "sources": "fr-1",
             "hts_note": "", "status": "verified", "notes": ""}
        )
    coverage = [
        {"measure_id": row["measure_id"], "hts": "8507.60",
         "coverage": "exempt" if row["measure_id"] == "reciprocal" else "covered", "evidence": "fr-1 annex",
         "status": "verified", "notes": ""}
        for row in measures
    ]
    for name, columns, rows in (
        ("sources.csv", registry_module.SOURCE_COLUMNS, sources),
        ("measures.csv", registry_module.MEASURE_COLUMNS, measures),
        ("coverage.csv", registry_module.COVERAGE_COLUMNS, coverage),
    ):
        with (directory / name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)


class TimelineFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_valid_rows_are_read_with_their_layer_and_source_status(self) -> None:
        rows = timeline_module.annotate(
            timeline_module.read_timeline(write_timeline(self.dir / "p.csv", PRODUCT_ROWS.format(code="11111111")), layer="product")
        )
        self.assertEqual([row["date"] for row in rows], ["2019-09-01", "2024-05-14", "2024-09-27"])
        self.assertEqual({row["layer"] for row in rows}, {"product"})
        self.assertEqual([row["needs_check"] for row in rows], [True, True, False])
        self.assertFalse(any(row["user_checked"] for row in rows))

    def test_primary_sources_are_only_the_hts_archive_fr_ustr_and_cbp(self) -> None:
        for url, expected in (
            ("https://hts.usitc.gov/reststop/file?release=2026HTSRev22", True),
            ("https://www.federalregister.gov/documents/2024/09/18/x", True),
            ("https://ustr.gov/about", True),
            ("https://www.cbp.gov/trade", True),
            ("https://example.com/hts.usitc.gov", False),
            ("https://news.example.org/tariffs", False),
            ("", False),
        ):
            with self.subTest(url):
                self.assertEqual(timeline_module.is_primary_source(url), expected)

    def test_invalid_rows_are_refused(self) -> None:
        good = "2025-01-01,effective,M,,,China,src,,draft,\n"
        cases = {
            "columns must be exactly": ("date,measure\n2025-01-01,M\n", "product"),
            "YYYY-MM-DD": (HEADER + "\n" + good.replace("2025-01-01", "2025-13-01"), "product"),
            "date_type": (HEADER + "\n" + good.replace("effective", "proposed"), "product"),
            "review_status": (HEADER + "\n" + good.replace("draft", "approved"), "product"),
            "applies_to": (HEADER + "\n" + good.replace(",,,China", ",,850760,China"), "product"),
            "not HTS codes": (HEADER + "\n" + good.replace(",,,China", ",,11111111,China"), "country"),
            "name its origins": (HEADER + "\n" + good.replace("China", ""), "country"),
            "source is required": (HEADER + "\n" + good.replace("src", ""), "product"),
            "no rows": (HEADER + "\n", "product"),
        }
        for message, (text, layer) in cases.items():
            with self.subTest(message):
                path = self.dir / "bad.csv"
                path.write_text(text, encoding="utf-8")
                with self.assertRaisesRegex(timeline_module.TimelineError, message):
                    timeline_module.read_timeline(path, layer=layer)

    def test_codes_must_be_the_scope_codes_their_parents_or_children(self) -> None:
        rows = [{"date": "2025-01-01", "measure": "M", "applies_to": "85076000; 8507600010"}]
        timeline_module.check_codes(rows, ["8507600010", "8507600020"])
        timeline_module.check_codes([{**rows[0], "applies_to": "8507600010"}], ["85076000"])
        with self.assertRaisesRegex(timeline_module.TimelineError, "outside the scope"):
            timeline_module.check_codes([{**rows[0], "applies_to": "85071000"}], ["8507600010"])


class TimelineStudyHashTests(test_plan.ProjectTestCase):
    """A timeline changes only study_sha256; editing a row (e.g. user_checked) makes a new analysis."""

    def timeline_study(self, extra: str = "") -> str:
        return test_plan.STUDY_TOML + '\n[timeline]\nfile = "config/tariff_timelines/synthetic.csv"\n' + extra

    def test_timeline_changes_the_study_hash_but_not_the_plan(self) -> None:
        default_study, default_plan = self.load_study().study_sha256, test_plan.plan_for(self.paths)["plan_sha256"]
        product = write_timeline(self.root / "config" / "tariff_timelines" / "synthetic.csv", PRODUCT_ROWS.format(code=""))
        self.rewrite_study(self.timeline_study())
        with_timeline = self.load_study()
        self.assertNotEqual(with_timeline.study_sha256, default_study)
        self.assertEqual(test_plan.plan_for(self.paths)["plan_sha256"], default_plan)

        product.write_text(product.read_text(encoding="utf-8").replace("draft", "user_checked", 1), encoding="utf-8")
        checked = self.load_study()
        self.assertNotEqual(checked.study_sha256, with_timeline.study_sha256)
        self.assertEqual(test_plan.plan_for(self.paths)["plan_sha256"], default_plan)

        write_timeline(self.root / "config" / "tariff_timelines" / "_country.csv", COUNTRY_ROWS)
        self.rewrite_study(self.timeline_study('country = "config/tariff_timelines/_country.csv"\n'))
        self.assertNotEqual(self.load_study().study_sha256, checked.study_sha256)

    def test_missing_or_invalid_timeline_is_refused_at_load(self) -> None:
        self.rewrite_study(self.timeline_study())
        with self.assertRaisesRegex(StudyError, "not found"):
            self.load_study()
        self.rewrite_study(test_plan.STUDY_TOML + '\n[timeline]\ncountry = "config/tariff_timelines/_country.csv"\n')
        with self.assertRaisesRegex(StudyError, "needs file"):
            self.load_study()
        self.rewrite_study(test_plan.STUDY_TOML + '\n[timeline]\nfile = "x.csv"\nlayer = "a"\n')
        with self.assertRaisesRegex(StudyError, "only file and country"):
            self.load_study()


def _display(frequency: str, periods: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = []
    for period in periods:
        for origin, value, duty in (("China", 60.0, 6.0), ("Other reported origins", 40.0, 0.4)):
            rows.append(
                {"frequency": frequency, "period": period, "period_status": "full_year" if frequency == "annual" else "monthly",
                 "display_origin": origin, "customs_value_usd": value, "realized_duty_rate": duty / value,
                 "value_share": value / 100, "unit_value_usd": float("nan"), "quantity_comparable": False}
            )
    display = pd.DataFrame(rows)
    scope = pd.DataFrame(
        {"frequency": frequency, "period": periods, "world_realized_duty_rate_pct": 6.4, "world_value_usd": 100.0}
    )
    return display, scope


class TimelineChartTests(unittest.TestCase):
    TIMELINE = pd.DataFrame(
        [
            {"date": "2019-09-01", "date_type": "effective", "measure": "Duty A", "rate_change": "+15%", "applies_to": "",
             "origins": "China", "primary_source": False},
            {"date": "2024-05-14", "date_type": "announced", "measure": "Review", "rate_change": "", "applies_to": "",
             "origins": "", "primary_source": True},
            {"date": "2030-01-01", "date_type": "effective", "measure": "Outside", "rate_change": "", "applies_to": "",
             "origins": "", "primary_source": True},
        ]
    )

    def test_realized_duty_chart_draws_numbered_timeline_lines_with_their_csv(self) -> None:
        labels = chart_module.ChartLabels(product="Batteries", title="T", focal_origin="China")
        for frequency, periods in (("annual", [str(year) for year in range(2017, 2026)]),
                                   ("monthly", [f"2024-{month:02d}" for month in range(1, 13)])):
            with self.subTest(frequency), tempfile.TemporaryDirectory() as directory:
                display, scope = _display(frequency, periods)
                captured = {}
                real_finish = chart_module._finish

                def finish(fig, png, source, *, footer=()):
                    captured["source"], captured["footer"] = source, list(footer)
                    return real_finish(fig, png, source, footer=footer)

                with mock.patch.object(chart_module, "_finish", side_effect=finish):
                    chart_module.plot_realized_duty(display, scope, Path(directory), labels, timeline=self.TIMELINE)
                plotted = pd.read_csv(Path(directory) / "09_realized_duty.timeline.csv")
                expected = ["Duty A", "Review"] if frequency == "annual" else ["Review"]
                self.assertEqual(plotted["measure"].tolist(), expected)
                self.assertEqual(plotted["number"].tolist(), [f"T{n}" for n in range(1, len(expected) + 1)])
                self.assertIn("Tariff timeline", captured["source"])
                self.assertEqual(len(captured["footer"]), len(expected))
                if frequency == "annual":
                    self.assertIn("[to verify]", captured["footer"][0])

    def test_event_path_mentions_event_lines_only_when_there_are_some(self) -> None:
        monthly = pd.DataFrame(
            {"period_date": ["2025-01-01", "2025-02-01"], "display_origin": ["China", "China"],
             "value_share": [0.6, 0.5], "world_value_usd": [100.0, 90.0]}
        )
        events = pd.DataFrame([{"date": "2025-02-01", "short_label": "Marker", "status": "implemented"}])
        for name, kwargs, has_events, has_timeline in (
            ("none", {"events": events.iloc[0:0]}, False, False),
            ("events", {"events": events}, True, False),
            ("timeline", {"events": events.iloc[0:0], "timeline": self.TIMELINE.assign(date="2025-01-15")}, False, True),
        ):
            with self.subTest(name), tempfile.TemporaryDirectory() as directory:
                captured = {}
                real_finish = chart_module._finish

                def finish(fig, png, source, *, footer=()):
                    captured["source"] = source
                    return real_finish(fig, png, source, footer=footer)

                with mock.patch.object(chart_module, "_finish", side_effect=finish):
                    chart_module.plot_study_event_path(monthly, output_dir=Path(directory), title="T", focal_origin="China",
                                                       product_name="Batteries", **kwargs)
                self.assertEqual("event lines" in captured["source"], has_events)
                self.assertEqual("Tariff timeline" in captured["source"], has_timeline)
                self.assertEqual((Path(directory) / "03_event_path.timeline.csv").exists(), has_timeline)


class TimelineReportTests(unittest.TestCase):
    def test_table_lists_every_row_with_its_primary_source(self) -> None:
        rows = timeline_module.annotate(
            [
                {"date": "2024-09-27", "date_type": "effective", "measure": "Duty B", "rate_change": "25%", "applies_to": "8507600010",
                 "origins": "China", "source": "HTS", "source_url": "https://hts.usitc.gov/x", "review_status": "draft", "notes": "",
                 "layer": "product"},
                {"date": "2024-05-14", "date_type": "announced", "measure": "Review", "rate_change": "", "applies_to": "",
                 "origins": "", "source": "memory", "source_url": "", "review_status": "user_checked", "notes": "", "layer": "product"},
            ]
        )
        table = report_module.timeline_table(rows)
        self.assertEqual(len(table), 4)
        self.assertIn("| 2024-05-14 | 宣布 | Review | — | 整个 scope | 全部 | memory；来源待核实 |", table)
        self.assertTrue(any("HTS ([原文](https://hts.usitc.gov/x))" in line for line in table))
        self.assertFalse(any("未经用户核对" in line or "用户已核对" in line for line in table))


class TimelineEndToEndTests(unittest.TestCase):
    """The offline lithium fixture with a timeline: table, findings, chart lines and report section."""

    def setUp(self) -> None:
        e2e.LithiumIonOfflineE2ETests.setUp(self)

    def tearDown(self) -> None:
        e2e.LithiumIonOfflineE2ETests.tearDown(self)

    def run_study(self, extra: str) -> Path:
        write_synthetic_registry(self.root)
        self.study.write_text(self.study.read_text(encoding="utf-8") + extra, encoding="utf-8")
        _, plan_path = plan_module.plan_study(self.config, self.study)
        client = DataWebClient(self.config, token=e2e.TOKEN, session=e2e.LithiumFixtureSession(self.fixture))
        plan_module.acquire_plan(self.config, plan_path, client=client, today=e2e.FIXED_TODAY)
        out = Path(analyze_study(self.config, self.study)["analysis_dir"])
        render_study_charts(self.config, self.study)
        report_study(self.config, self.study)
        return out

    def test_timeline_reaches_tables_findings_chart_and_report(self) -> None:
        write_timeline(self.root / "config" / "tariff_timelines" / "lithium-ion-cells-modules.csv", PRODUCT_ROWS.format(code="8507600010"))
        write_timeline(self.root / "config" / "tariff_timelines" / "_country.csv", COUNTRY_ROWS)
        out = self.run_study(
            '\n[timeline]\nfile = "config/tariff_timelines/lithium-ion-cells-modules.csv"\n'
            'country = "config/tariff_timelines/_country.csv"\n'
        )
        table = pd.read_csv(out / "tables" / "tariff_timeline.csv")
        self.assertEqual(len(table), 4)
        self.assertEqual(set(table["coverage"]), {"applies"})
        manifest = json.loads((out / "analysis_manifest.json").read_text(encoding="utf-8"))
        self.assertIsNotNone(manifest["outputs"]["tariff_timeline"])
        self.assertEqual(manifest["tariff_timeline_filter"]["excluded"], 1)
        self.assertEqual(manifest["tariff_timeline_filter"]["excluded_by_coverage"], {"exempt": 1})
        self.assertEqual(len(manifest["tariff_registry_inputs"]), 3)
        self.assertEqual(len(manifest["tariff_registry_sha256"]), 64)
        self.assertNotIn("Synthetic reciprocal duty", table["measure"].tolist())
        findings = json.loads((out / "findings.json").read_text(encoding="utf-8"))
        timeline_claim = next(item for item in findings["findings"] if item["id"] == "timeline")
        self.assertIn("2 条有一手来源", timeline_claim["claim"])
        self.assertIn("2 条来源仍待核实", timeline_claim["claim"])
        self.assertTrue((out / "charts" / "09_realized_duty.timeline.csv").is_file())
        report = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("## 4. 关税时间线", report)
        self.assertIn("| 2024-09-27 | 生效 | Synthetic duty B |", report)

    def test_timeline_code_outside_the_scope_is_refused(self) -> None:
        write_timeline(self.root / "config" / "tariff_timelines" / "x.csv", PRODUCT_ROWS.format(code="85071000"))
        with self.assertRaisesRegex(Exception, "outside the scope"):
            self.run_study('\n[timeline]\nfile = "config/tariff_timelines/x.csv"\n')


if __name__ == "__main__":
    unittest.main()
