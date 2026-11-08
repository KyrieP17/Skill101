"""Offline end-to-end regression for a new, scope-driven product.

All HTS-like codes and trade values are synthetic.  The fixture tests workflow
contracts only; it is not evidence for a real customs classification.
"""

from __future__ import annotations

import copy
from datetime import date
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest


TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"
FIXTURE_ROOT = TESTS_DIR / "fixtures" / "lithium"
for entry in (SRC_DIR, TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from test_scope import approve  # noqa: E402
from usitc_pipeline import plan as plan_module  # noqa: E402
from usitc_pipeline import scope as scope_module  # noqa: E402
from usitc_pipeline.config import load_config  # noqa: E402
from usitc_pipeline.dataweb import DataWebClient  # noqa: E402
from usitc_pipeline.scope import ScopeGateError  # noqa: E402
from usitc_pipeline.study_pipeline import (  # noqa: E402
    analyze_study,
    render_study_charts,
    report_study,
    validate_study_outputs,
)


TOKEN = "OFFLINE-FIXTURE-TOKEN"
FIXED_TODAY = date(2026, 11, 5)
MEASURES = (
    ("Customs Value", 0),
    ("First Unit of Quantity", 1),
    ("Calculated Duties", 2),
)


def _entry(value: object) -> dict[str, object]:
    return {"value": value}


def _fixture_response(payload: dict, fixture: dict) -> dict:
    """Build a DataWeb-shaped response for exactly the requested plan job."""

    search = payload["searchOptions"]
    codes = [str(code) for code in search["commodities"]["commodities"]]
    years = [str(year) for year in search["componentSettings"]["years"]]
    breakout = search["countries"]["aggregation"] == "Break Out Countries"
    tables: list[dict] = []

    for tab_name, measure_index in MEASURES:
        labels = (["Country"] if breakout else []) + [
            "HTS Number",
            "Description",
            "Quantity Description",
            *years,
        ]
        rows: list[dict] = []
        for code in codes:
            code_record = fixture["codes"][code]
            origins = sorted(
                {
                    origin
                    for year in years
                    for origin in code_record["years"][year]
                }
            )
            row_origins = origins if breakout else ["World"]
            for origin in row_origins:
                values: list[object] = []
                if breakout:
                    values.append(origin)
                values.extend(
                    [
                        code,
                        code_record["description"],
                        code_record["quantity_unit"]
                        if tab_name == "First Unit of Quantity"
                        else f"Value for: {code_record['quantity_unit']}",
                    ]
                )
                for year in years:
                    by_origin = code_record["years"][year]
                    if origin == "World":
                        measured = sum(float(item[measure_index]) for item in by_origin.values())
                    else:
                        measured = by_origin[origin][measure_index]
                    values.append(measured)
                rows.append({"rowEntries": [_entry(value) for value in values]})
        tables.append(
            {
                "tab_name": tab_name,
                "column_groups": [{"columns": [{"label": label} for label in labels]}],
                "row_groups": [{"rowsNew": rows}],
            }
        )
    return {"dto": {"title": "Synthetic lithium-ion offline fixture", "tables": tables}}


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return copy.deepcopy(self._payload)


class LithiumFixtureSession:
    """Answer DataWeb POSTs locally and retain only non-secret request facts."""

    def __init__(self, fixture: dict) -> None:
        self.fixture = fixture
        self.calls: list[dict[str, object]] = []

    def post(self, _url: str, **kwargs) -> FakeResponse:
        payload = kwargs["json"]
        search = payload["searchOptions"]
        self.calls.append(
            {
                "codes": list(search["commodities"]["commodities"]),
                "years": list(search["componentSettings"]["years"]),
                "frequency": search["componentSettings"]["yearsTimeline"],
                "breakout": search["countries"]["aggregation"],
            }
        )
        return FakeResponse(_fixture_response(payload, self.fixture))


EXPECTED_CHARTS = {
    "01_scope_trend",
    "02_product_reallocation",
    "04_origin_shares",
    "05_origin_change",
    "07_concentration",
    "09_realized_duty",
    "10_world_check",
}
EXPECTED_SKIPPED_CHARTS = {"03_event_path", "06_ytd_comparison", "08_unit_value"}


class LithiumIonOfflineE2ETests(unittest.TestCase):
    def setUp(self) -> None:
        # Keep generated projects out of the source tree so the suite also
        # runs against a read-only checkout.
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

        config_dir = self.root / "config"
        studies_dir = config_dir / "studies"
        studies_dir.mkdir(parents=True)
        shutil.copy2(PROJECT_ROOT / "config" / "research.toml", config_dir / "research.toml")
        shutil.copy2(PROJECT_ROOT / "config" / "policy_events.csv", config_dir / "policy_events.csv")
        shutil.copy2(PROJECT_ROOT / "config" / "hts_naics_proxy.csv", config_dir / "hts_naics_proxy.csv")
        self.study = studies_dir / "lithium-ion-annual-2017-2025.toml"
        shutil.copy2(FIXTURE_ROOT / "study.toml", self.study)
        shutil.copy2(FIXTURE_ROOT / "decision.md", self.root / "decisions.md")

        self.scope_dir = self.root / "scopes" / "lithium-ion-cells-modules" / "v1"
        (self.scope_dir / "history").mkdir(parents=True)
        for name in ("reviewed.csv", "hts-history.json"):
            shutil.copy2(FIXTURE_ROOT / name, self.scope_dir / name)
        for name in ("catalog.csv", "official-editions.txt"):
            shutil.copy2(FIXTURE_ROOT / "history" / name, self.scope_dir / "history" / name)

        # The reviewer's steps: read the card, then confirm it in a terminal.
        evidence = self.scope_dir / "hts-history.json"
        card = self.scope_dir / "approval-card.json"
        scope_module.build_approval_card(self.scope_dir / "reviewed.csv", output_path=card, history_evidence=evidence)
        self.approval = approve(
            self.scope_dir / "reviewed.csv",
            card,
            self.scope_dir / "scope.csv",
            self.scope_dir / "manifest.json",
            history=evidence,
        )
        self.config = load_config(config_dir / "research.toml")
        self.fixture = json.loads((FIXTURE_ROOT / "trade_values.json").read_text(encoding="utf-8"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_complete_offline_scope_to_validated_charts(self) -> None:
        manifest = scope_module.verify_scope_manifest(self.scope_dir / "manifest.json")
        self.assertEqual(manifest.scope_sha256, self.approval["scope_sha256"])
        self.assertFalse(manifest.quantity_comparable)
        self.assertIsNone(manifest.quantity_unit)
        self.assertEqual(
            manifest.product_name,
            "Rechargeable lithium-ion cells and modules (excluding primary, lead-acid, starter batteries, and parts)",
        )
        self.assertTrue(manifest.continuity_verified)
        self.assertEqual(self.approval["approval"]["method"], scope_module.APPROVAL_METHOD)

        plan, plan_path = plan_module.plan_study(self.config, self.study)
        self.assertFalse(plan["scope"]["quantity_comparable"])
        self.assertEqual(
            plan_module.codes_by_period(plan),
            [
                {"frequency": "annual", "start": 2017, "end": 2021, "codes": ["8507600010"]},
                {
                    "frequency": "annual",
                    "start": 2022,
                    "end": 2025,
                    "codes": ["8507600020", "8507600030"],
                },
            ],
        )

        session = LithiumFixtureSession(self.fixture)
        client = DataWebClient(self.config, token=TOKEN, session=session)
        acquisition = plan_module.acquire_plan(
            self.config,
            plan_path,
            client=client,
            today=FIXED_TODAY,
        )
        self.assertEqual((acquisition["downloaded"], acquisition["cached"]), (4, 0))
        self.assertEqual(acquisition["scope_sha256"], manifest.scope_sha256)
        self.assertEqual(acquisition["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(len(session.calls), 4)
        self.assertEqual(acquisition["query_through"], {"annual": "2025"})
        self.assertEqual(acquisition["data_through"], {"annual": "2025"})

        result = analyze_study(self.config, self.study)
        charts = render_study_charts(self.config, self.study)
        validation = validate_study_outputs(self.config, self.study)
        self.assertTrue(result["validation"]["ok"])
        self.assertTrue(validation["ok"])
        self.assertEqual(result["scope_sha256"], manifest.scope_sha256)
        self.assertEqual(result["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(result["study_sha256"], validation["study_sha256"])
        self.assertEqual(result["query_through"], {"annual": "2025"})
        self.assertEqual(result["data_through"], {"annual": "2025"})
        self.assertEqual(validation["data_through"], {"annual": "2025"})
        self.assertEqual({Path(chart).stem for chart in charts}, EXPECTED_CHARTS)

        analysis_dir = Path(result["analysis_dir"])
        analysis_manifest = json.loads((analysis_dir / "analysis_manifest.json").read_text(encoding="utf-8"))
        chart_manifest = json.loads((analysis_dir / "chart_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(analysis_manifest["scope_sha256"], manifest.scope_sha256)
        self.assertEqual(analysis_manifest["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(analysis_manifest["study_sha256"], result["study_sha256"])
        self.assertEqual(analysis_manifest["data_through"], {"annual": "2025"})
        self.assertEqual(chart_manifest["analysis_data_through"], {"annual": "2025"})
        skips = analysis_manifest["optional_skips"]
        self.assertEqual(skips["monthly_panel"], "study has no periods.monthly")
        self.assertEqual(skips["policy_events"], "study lists no [events] ids")
        self.assertIsNone(skips["product_reallocation"])
        self.assertIsNone(skips["origin_change"])
        self.assertEqual(set(chart_manifest["skipped"]), EXPECTED_SKIPPED_CHARTS)
        self.assertIsNone(analysis_manifest["outputs"]["monthly_panel"])
        self.assertIsNone(analysis_manifest["outputs"]["event_metrics"])
        self.assertFalse((analysis_dir / "tables" / "monthly_panel.csv").exists())
        self.assertFalse((analysis_dir / "tables" / "event_metrics.json").exists())

        reallocation = (analysis_dir / "tables" / "product_reallocation.csv").read_text(encoding="utf-8")
        self.assertIn("8507600020", reallocation)
        self.assertIn("8507600030", reallocation)
        self.assertNotIn("limited_share_change", reallocation)

        report = report_study(self.config, self.study)
        report_text = Path(report["report"]).read_text(encoding="utf-8")
        self.assertIn("| HTS 连续性 | 已核实 |", report_text)
        self.assertIn("| 批准 | 已批准 |", report_text)
        self.assertNotIn("回放结果", report_text)
        for stem in EXPECTED_CHARTS:
            self.assertIn(f"](charts/{stem}.png)", report_text)
            self.assertIn(f"`charts/{stem}.csv`", report_text)
        key_points = [
            line for line in Path(report["key_points"]).read_text(encoding="utf-8").splitlines() if line[:1].isdigit()
        ]
        self.assertTrue(5 <= len(key_points) <= 8, key_points)
        self.assertTrue(all("**[" in line and "来源：`" in line for line in key_points), key_points)
        findings = json.loads((analysis_dir / "findings.json").read_text(encoding="utf-8"))
        self.assertIn("quantity_blocked", findings["key_points"])
        self.assertFalse({"unit_value", "continuity_unverified", "legacy_replay"} & {item["id"] for item in findings["findings"]})
        self.assertIn("not configuration or approval", (self.root / "decisions.md").read_text(encoding="utf-8"))
        self.assertNotIn("850760", (self.root / "config" / "research.toml").read_text(encoding="utf-8"))
        application_source = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((SRC_DIR / "usitc_pipeline").glob("*.py"))
        )
        self.assertNotIn("8507600010", application_source)
        self.assertNotIn("lithium-ion-cells-modules", application_source)
        for generated in [*analysis_dir.rglob("*"), *self.config.paths.raw_dataweb.rglob("*")]:
            if generated.is_file():
                generated.resolve().relative_to(self.root)
        leaked = [
            path.relative_to(self.root).as_posix()
            for path in self.root.rglob("*")
            if path.is_file() and TOKEN.encode() in path.read_bytes()
        ]
        self.assertEqual(leaked, [])

        chart_data = analysis_dir / "charts" / "02_product_reallocation.csv"
        original_chart_data = chart_data.read_bytes()
        chart_data.write_bytes(original_chart_data + b"\nmutated")
        with self.assertRaisesRegex(ScopeGateError, "Chart artifact changed"):
            validate_study_outputs(self.config, self.study)
        chart_data.write_bytes(original_chart_data)

        manifest_path = analysis_dir / "analysis_manifest.json"
        original_manifest = manifest_path.read_text(encoding="utf-8")
        incomplete_manifest = json.loads(original_manifest)
        del incomplete_manifest["outputs"]["trade_panel"]
        manifest_path.write_text(json.dumps(incomplete_manifest), encoding="utf-8")
        with self.assertRaisesRegex(ScopeGateError, "output set is incomplete"):
            validate_study_outputs(self.config, self.study)
        manifest_path.write_text(original_manifest, encoding="utf-8")

    def test_generic_lithium_battery_phrase_stops_at_scope_gate(self) -> None:
        """A generic phrase lacks rechargeability, use, product form, and exclusions."""

        generic = self.root / "config" / "studies" / "generic-lithium-battery.toml"
        generic.write_text(
            """[study]
study_id = "generic-lithium-battery"
title = "Generic lithium battery request"
scope_manifest = "scopes/generic-lithium-battery/v1/manifest.json"

[periods]
annual = { start = 2024, end = 2025 }

[origins]
focal = "China"
highlight = []
""",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ScopeGateError, "Scope manifest not found"):
            plan_module.plan_study(self.config, generic)
        self.assertFalse(
            (self.config.paths.outputs / "runs" / "generic-lithium-battery").exists(),
            "an ambiguous product must not reach planning or acquisition",
        )


if __name__ == "__main__":
    unittest.main()
