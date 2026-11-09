"""Tests for study specs, query plans, and the gated scope-driven acquisition.

Everything runs offline in a throwaway project.  A fake HTTP session returns
the synthetic DataWeb fixture, so no token, network, or pandas is needed.
"""

from __future__ import annotations

import copy
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"
for entry in (SRC_DIR, TESTS_DIR):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from test_scope import freeze_scope, history_rows, reviewed, run_captured, write_scope_csv  # noqa: E402
from usitc_pipeline import cli  # noqa: E402
from usitc_pipeline import plan as plan_module  # noqa: E402
from usitc_pipeline import scope as scope_module  # noqa: E402
from usitc_pipeline.config import load_config  # noqa: E402
from usitc_pipeline.dataweb import DataWebClient, DataWebError  # noqa: E402
from usitc_pipeline.pipeline import acquire_all  # noqa: E402
from usitc_pipeline.scope import ScopeGateError  # noqa: E402
from usitc_pipeline.study import StudyError  # noqa: E402
from usitc_pipeline.study_pipeline import load_study_context  # noqa: E402


TODAY = date(2026, 11, 5)
TOKEN = "SECRET-TOKEN-123"
FIXTURE = json.loads((TESTS_DIR / "fixtures" / "dataweb_sample.json").read_text(encoding="utf-8"))

RESEARCH_TOML = """
[study]
annual_start_year = 2019
annual_end_year = 2024
monthly_start_year = 2021
monthly_end_year = 2022
ytd_end_month = 12
compare_base_year = 2023
compare_year = 2024
selected_chapters = ["11"]

[dataweb]
base_url = "https://example.invalid/dataweb"
timeout_seconds = 5
max_records = 1000
retry_delays = [0]

[[products]]
code = "111111"
name = "Synthetic product"
"""

STUDY_TOML = """
[study]
study_id = "synthetic-study"
title = "Synthetic study"
scope_manifest = "scopes/synthetic/v1/manifest.json"

[periods]
annual = { start = 2019, end = 2024 }
monthly = { start = 2021, end = 2022 }

[origins]
focal = "China"
highlight = ["Vietnam"]

[comparison]
base_year = 2023
compare_year = 2024

[[event_windows]]
id = "window_a"
label = "Window A"
start = "2021-06"
end = "2022-03"

[events]
ids = ["evt_a"]
"""


def build_project(root: Path, rows: list[dict[str, str]] | None = None) -> dict[str, Path]:
    """Create a minimal project with one frozen, approved synthetic scope."""

    config_dir = root / "config"
    (config_dir / "studies").mkdir(parents=True)
    (config_dir / "research.toml").write_text(RESEARCH_TOML, encoding="utf-8")
    (config_dir / "policy_events.csv").write_text(
        "event_date,event_id,event_label,event_type,status,scope,notes\n"
        "2021-07-01,evt_a,Synthetic event,tariff,implemented,synthetic,test only\n",
        encoding="utf-8",
    )
    (config_dir / "hts_naics_proxy.csv").write_text(
        "hts_code,hts_level,series_id,naics,industry_name,mapping_quality,notes\n", encoding="utf-8"
    )
    scope_csv = write_scope_csv(root / "scopes" / "synthetic" / "v1" / "scope.csv", rows or history_rows())
    manifest = scope_csv.with_name("manifest.json")
    freeze_scope(scope_csv, manifest)
    study = config_dir / "studies" / "synthetic-study.toml"
    study.write_text(STUDY_TOML, encoding="utf-8")
    return {"config": config_dir / "research.toml", "scope_csv": scope_csv, "manifest": manifest, "study": study}


def plan_for(paths: dict[str, Path]) -> dict:
    """Build, without writing, the plan `plan` would write for a project from :func:`build_project`."""

    config = load_config(paths["config"])
    study = plan_module.load_project_study(config, paths["study"], today=TODAY)
    scope = scope_module.verify_scope_manifest(paths["manifest"])
    return plan_module.build_query_plan(scope, study, max_records=config.dataweb.max_records)


class FakeResponse:
    status_code = 200

    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return copy.deepcopy(self._payload)


class FakeSession:
    """Records requests and answers each one with the synthetic fixture."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append(
            {
                "url": url,
                "authorization": kwargs["headers"]["Authorization"],
                "codes": kwargs["json"]["searchOptions"]["commodities"]["commodities"],
            }
        )
        return FakeResponse(FIXTURE)


class RefusingSession:
    def post(self, *args, **kwargs):  # pragma: no cover - reaching this is the failure
        raise AssertionError("the gate should stop the run before any network request")


class RetrySession:
    """Return one retryable response, then a success, while recording call order."""

    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.calls = 0

    def post(self, *args, **kwargs) -> FakeResponse:
        self.calls += 1
        self.events.append("post")
        response = FakeResponse(FIXTURE)
        response.status_code = 429 if self.calls == 1 else 200
        return response


class ProjectTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.paths = build_project(self.root)
        self.config = load_config(self.paths["config"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def load_study(self, path: Path | None = None):
        return plan_module.load_project_study(self.config, path or self.paths["study"], today=TODAY)

    def write_plan(self) -> tuple[dict, Path]:
        plan = plan_for(self.paths)
        return plan, plan_module.write_plan(plan, outputs_root=self.config.paths.outputs)

    def rewrite_study(self, text: str) -> None:
        self.paths["study"].write_text(text, encoding="utf-8")

    def run_cli(self, *args: str) -> tuple[int, str, str]:
        return run_captured(cli.main, ["--config", str(self.paths["config"]), *args])


class DataWebRetryTests(ProjectTestCase):
    def test_initial_request_is_immediate_and_wait_happens_only_after_failure(self) -> None:
        self.paths["config"].write_text(RESEARCH_TOML.replace("retry_delays = [0]", "retry_delays = [0, 2]"), encoding="utf-8")
        config = load_config(self.paths["config"])
        events: list[str] = []
        session = RetrySession(events)
        client = DataWebClient(config, token=TOKEN, session=session)

        with mock.patch("usitc_pipeline.dataweb.random.uniform", return_value=0.0), mock.patch(
            "usitc_pipeline.dataweb.time.sleep", side_effect=lambda seconds: events.append(f"sleep:{seconds:g}")
        ):
            response = client._post_with_retry({"synthetic": True})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(events, ["post", "sleep:2", "post"])


class QueryPlanTests(ProjectTestCase):
    def test_each_period_queries_only_codes_valid_in_it(self) -> None:
        plan = plan_for(self.paths)
        self.assertEqual(len(plan["jobs"]), 8)
        self.assertEqual(
            plan_module.codes_by_period(plan),
            [
                {"frequency": "annual", "start": 2019, "end": 2021, "codes": ["1111110010"]},
                {"frequency": "annual", "start": 2022, "end": 2024, "codes": ["1111110020", "1111110030"]},
                {"frequency": "monthly", "start": 2021, "end": 2021, "codes": ["1111110010"]},
                {"frequency": "monthly", "start": 2022, "end": 2022, "codes": ["1111110020", "1111110030"]},
            ],
        )
        self.assertEqual(sum(job["break_out_origins"] for job in plan["jobs"]), 4)
        self.assertTrue(all(job["cache_stem"].startswith("scopes/synthetic/v1/hts10_") for job in plan["jobs"]))
        self.assertNotIn("1111119000", json.dumps(plan["jobs"]))

    def test_plan_hash_is_stable_and_does_not_depend_on_project_location(self) -> None:
        plan, path = self.write_plan()
        self.assertEqual(plan_for(self.paths)["plan_sha256"], plan["plan_sha256"])
        self.assertEqual(plan_module.write_plan(plan_for(self.paths), outputs_root=self.config.paths.outputs), path)
        self.assertFalse(Path(plan["scope"]["manifest_path"]).is_absolute())
        self.assertFalse(Path(plan["study"]["path"]).is_absolute())
        with tempfile.TemporaryDirectory() as other:
            other_plan = plan_for(build_project(Path(other).resolve()))
        self.assertEqual(other_plan["plan_sha256"], plan["plan_sha256"])

    def test_years_outside_the_approved_scope_are_refused(self) -> None:
        self.rewrite_study(STUDY_TOML.replace("annual = { start = 2019", "annual = { start = 2015"))
        with self.assertRaisesRegex(ScopeGateError, "outside the approved scope"):
            plan_for(self.paths)

    def test_origins_and_event_windows_do_not_change_the_plan(self) -> None:
        before = plan_for(self.paths)["plan_sha256"]
        self.rewrite_study(
            STUDY_TOML.replace('highlight = ["Vietnam"]', 'highlight = ["Vietnam", "Mexico", "India"]')
            .replace('focal = "China"', 'focal = "Taiwan"')
            .replace('end = "2022-03"', 'end = "2022-12"')
        )
        self.assertEqual(plan_for(self.paths)["plan_sha256"], before)

    def test_title_and_selected_policy_event_content_change_only_the_study_hash(self) -> None:
        before_study = self.load_study()
        before_plan = plan_for(self.paths)["plan_sha256"]

        self.rewrite_study(STUDY_TOML.replace('title = "Synthetic study"', 'title = "Retitled study"'))
        retitled = self.load_study()
        self.assertNotEqual(retitled.study_sha256, before_study.study_sha256)
        self.assertEqual(plan_for(self.paths)["plan_sha256"], before_plan)

        self.rewrite_study(STUDY_TOML)
        events_path = self.paths["config"].parent / "policy_events.csv"
        events_path.write_text(
            "event_date,event_id,event_label,event_type,status,scope,notes\n"
            "2021-08-01,evt_a,Revised event label,tariff,implemented,synthetic,test only\n",
            encoding="utf-8",
        )
        changed_event = self.load_study()
        self.assertNotEqual(changed_event.study_sha256, before_study.study_sha256)
        self.assertEqual(plan_for(self.paths)["plan_sha256"], before_plan)

    def test_invalid_study_settings_are_rejected(self) -> None:
        absolute_manifest = self.paths["manifest"].as_posix()
        cases = {
            "focal repeated": (STUDY_TOML.replace('["Vietnam"]', '["China"]'), "focal"),
            "world as origin": (STUDY_TOML.replace('["Vietnam"]', '["World"]'), "World"),
            "unknown event": (STUDY_TOML.replace('["evt_a"]', '["evt_missing"]'), "evt_missing"),
            "window outside monthly": (STUDY_TOML.replace('end = "2022-03"', 'end = "2023-03"'), "outside"),
            "comparison outside annual": (STUDY_TOML.replace("compare_year = 2024", "compare_year = 2025"), "inside"),
            "future year": (STUDY_TOML.replace("end = 2024 }", "end = 2030 }"), "future"),
            "unknown section": (STUDY_TOML + "\n[surprise]\nvalue = 1\n", "Unknown"),
            "absolute manifest": (
                STUDY_TOML.replace('"scopes/synthetic/v1/manifest.json"', f'"{absolute_manifest}"'),
                "relative",
            ),
        }
        for name, (text, message) in cases.items():
            with self.subTest(name):
                self.rewrite_study(text)
                with self.assertRaisesRegex(StudyError, message):
                    self.load_study()

    def test_default_quantity_basis_keeps_hashes_and_plans_unchanged(self) -> None:
        before_study, before_plan = self.load_study().study_sha256, plan_for(self.paths)
        self.rewrite_study(STUDY_TOML + '\n[quantity]\nunit = "first"\n')
        self.assertEqual(self.load_study().study_sha256, before_study)
        after_plan = plan_for(self.paths)
        self.assertEqual(after_plan["plan_sha256"], before_plan["plan_sha256"])
        self.assertNotIn("second_quantity", json.dumps(after_plan))
        self.assertNotIn("CONS_SEC_UNIT_QUANT", json.dumps(after_plan))

    def test_second_quantity_basis_needs_one_second_unit_in_the_scope(self) -> None:
        default_hash = self.load_study().study_sha256
        self.rewrite_study(STUDY_TOML + '\n[quantity]\nunit = "second"\n')
        self.assertNotEqual(self.load_study().study_sha256, default_hash)
        with self.assertRaisesRegex(ScopeGateError, "second unit"):
            plan_for(self.paths)
        for text, message in (('unit = "kg"', "first.*second"), ('basis = "second"', "only unit")):
            with self.subTest(text):
                self.rewrite_study(STUDY_TOML + f"\n[quantity]\n{text}\n")
                with self.assertRaisesRegex(StudyError, message):
                    self.load_study()

    def test_study_file_must_live_inside_the_project(self) -> None:
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere) / "study.toml"
            outside.write_text(STUDY_TOML, encoding="utf-8")
            with self.assertRaisesRegex(StudyError, "inside the project"):
                self.load_study(outside)


class GatedAcquisitionTests(ProjectTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.plan, self.plan_path = self.write_plan()

    def acquire(self, session) -> dict:
        client = DataWebClient(self.config, token=TOKEN, session=session)
        return plan_module.acquire_plan(self.config, self.plan_path, client=client, today=TODAY)

    def test_acquisition_records_provenance_and_never_writes_the_token(self) -> None:
        session = FakeSession()
        result = self.acquire(session)
        self.assertEqual((result["downloaded"], result["cached"]), (8, 0))
        self.assertEqual(len(session.calls), 8)
        self.assertTrue(all(call["authorization"] == f"Bearer {TOKEN}" for call in session.calls))
        self.assertEqual(
            sorted(tuple(call["codes"]) for call in session.calls),
            sorted(tuple(job["codes"]) for job in self.plan["jobs"]),
        )

        for job in self.plan["jobs"]:
            meta_path = self.config.paths.raw_dataweb / f"{job['cache_stem']}.meta.json"
            provenance = json.loads(meta_path.read_text(encoding="utf-8"))["provenance"]
            self.assertEqual(provenance["scope_sha256"], self.plan["scope"]["scope_sha256"])
            self.assertEqual(provenance["plan_sha256"], self.plan["plan_sha256"])
            self.assertEqual(provenance["approved_codes"], job["codes"])

        record = json.loads((self.plan_path.parent / "acquisition.json").read_text(encoding="utf-8"))
        self.assertFalse(record["secrets_recorded"])
        self.assertEqual(record["query_through"], {"annual": "2024", "monthly": "2022-12"})
        self.assertIn("data_through", record)
        self.assertTrue(all(not Path(item["path"]).is_absolute() for item in record["snapshots"]))

        leaked = [path for path in self.root.rglob("*") if path.is_file() and TOKEN.encode() in path.read_bytes()]
        self.assertEqual(leaked, [])

        again = self.acquire(RefusingSession())
        self.assertEqual((again["downloaded"], again["cached"]), (0, 8))

    def test_modified_cached_response_is_rejected_before_reuse(self) -> None:
        first = self.acquire(FakeSession())
        raw_path = self.root / first["snapshots"][0]["path"]
        raw_path.write_bytes(raw_path.read_bytes() + b"\n")

        with self.assertRaisesRegex(DataWebError, "Cached response hash"):
            self.acquire(RefusingSession())

    def test_payload_identical_cache_is_reusable_by_another_study_and_keeps_source_lineage(self) -> None:
        first = self.acquire(FakeSession())
        self.assertEqual((first["downloaded"], first["cached"]), (8, 0))

        second_study = self.paths["study"].with_name("second-study.toml")
        second_study.write_text(
            STUDY_TOML.replace('study_id = "synthetic-study"', 'study_id = "second-study"'),
            encoding="utf-8",
        )
        second_plan, second_plan_path = plan_module.plan_study(self.config, second_study)
        self.assertNotEqual(second_plan["plan_sha256"], self.plan["plan_sha256"])
        self.assertEqual(
            [job["cache_stem"] for job in second_plan["jobs"]],
            [job["cache_stem"] for job in self.plan["jobs"]],
        )

        second = plan_module.acquire_plan(
            self.config,
            second_plan_path,
            client=DataWebClient(self.config, token=TOKEN, session=RefusingSession()),
            today=TODAY,
        )
        self.assertEqual((second["downloaded"], second["cached"]), (0, 8))
        self.assertTrue(
            all(item["source_plan_sha256"] == self.plan["plan_sha256"] for item in second["snapshots"])
        )
        context = load_study_context(self.config, second_study)
        self.assertEqual(context.plan["plan_sha256"], second_plan["plan_sha256"])
        self.assertEqual(len(context.raw_paths), len(second_plan["jobs"]))

    def test_hand_edited_plan_is_refused(self) -> None:
        stored = json.loads(self.plan_path.read_text(encoding="utf-8"))
        forged = copy.deepcopy(stored)
        forged["jobs"][0]["codes"].append("1111119000")

        with self.subTest("edited without a new hash"):
            self.plan_path.write_text(json.dumps(forged), encoding="utf-8")
            with self.assertRaisesRegex(ScopeGateError, "edited after planning"):
                self.acquire(RefusingSession())

        with self.subTest("edited and re-hashed"):
            forged["plan_sha256"] = plan_module._plan_digest(forged)
            self.plan_path.write_text(json.dumps(forged), encoding="utf-8")
            with self.assertRaisesRegex(ScopeGateError, "no longer matches"):
                self.acquire(RefusingSession())

    def test_scope_csv_edited_after_freezing_is_refused(self) -> None:
        rows = history_rows()
        rows[0]["hts_code"] = "1111110099"
        write_scope_csv(self.paths["scope_csv"], rows)
        with self.assertRaisesRegex(ScopeGateError, "changed after it was frozen"):
            self.acquire(RefusingSession())

    def test_changed_history_source_blocks_acquisition_before_network(self) -> None:
        source = self.paths["manifest"].parent / "history" / "official-editions.txt"
        source.write_text("Changed after approval.\n", encoding="utf-8")
        with self.assertRaisesRegex(ScopeGateError, "historical continuity verification failed"):
            self.acquire(RefusingSession())

    def test_changed_study_period_is_refused(self) -> None:
        self.rewrite_study(STUDY_TOML.replace("annual = { start = 2019", "annual = { start = 2020"))
        with self.assertRaisesRegex(ScopeGateError, "no longer matches"):
            self.acquire(RefusingSession())

    def test_study_pointing_at_another_scope_is_refused(self) -> None:
        self.rewrite_study(STUDY_TOML.replace("synthetic/v1/manifest.json", "synthetic/v2/manifest.json"))
        with self.assertRaisesRegex(ScopeGateError, "points to"):
            self.acquire(RefusingSession())

    def test_changed_origins_keep_the_plan_valid(self) -> None:
        self.rewrite_study(STUDY_TOML.replace('highlight = ["Vietnam"]', 'highlight = ["Mexico"]'))
        self.assertEqual(self.acquire(FakeSession())["downloaded"], 8)

    def test_missing_token_stops_after_the_gate_without_writing_snapshots(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATAWEB_TOKEN", None)
            with self.assertRaisesRegex(DataWebError, "DATAWEB_TOKEN is required"):
                plan_module.acquire_plan(self.config, self.plan_path, today=TODAY)
            code, _, stderr = self.run_cli("acquire", "--plan", str(self.plan_path))
        self.assertEqual(code, 2)
        self.assertIn("DATAWEB_TOKEN is required", stderr)
        self.assertFalse((self.plan_path.parent / "acquisition.json").exists())
        self.assertEqual(list((self.config.paths.raw_dataweb).rglob("*.json")), [])


class SecondQuantityPlanTests(unittest.TestCase):
    """A study with [quantity] unit = "second" also asks DataWeb for the second unit."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        rows = [{**row, "second_unit": "kg" if row["decision"] == "include" else ""} for row in history_rows()]
        self.paths = build_project(self.root, rows)
        self.config = load_config(self.paths["config"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_second_basis_jobs_request_the_second_unit_and_their_snapshots_verify(self) -> None:
        first = plan_for(self.paths)
        self.assertFalse(any("second_quantity" in job for job in first["jobs"]))
        self.paths["study"].write_text(STUDY_TOML + '\n[quantity]\nunit = "second"\n', encoding="utf-8")
        second = plan_for(self.paths)
        self.assertNotEqual(second["plan_sha256"], first["plan_sha256"])
        self.assertTrue(all(job["second_quantity"] for job in second["jobs"]))
        self.assertIn("CONS_SEC_UNIT_QUANT", second["measurement"]["measures"])
        self.assertTrue(
            {job["payload_sha256"] for job in second["jobs"]}.isdisjoint(job["payload_sha256"] for job in first["jobs"])
        )

        plan_path = plan_module.write_plan(second, outputs_root=self.config.paths.outputs)
        client = DataWebClient(self.config, token=TOKEN, session=FakeSession())
        plan_module.acquire_plan(self.config, plan_path, client=client, today=TODAY)
        meta = json.loads(
            (self.config.paths.raw_dataweb / f"{second['jobs'][0]['cache_stem']}.meta.json").read_text(encoding="utf-8")
        )
        self.assertTrue(meta["query"]["second_quantity"])
        context = load_study_context(self.config, self.paths["study"])
        self.assertEqual(len(context.raw_paths), len(second["jobs"]))


class CommandLineTests(ProjectTestCase):
    def test_plan_command_writes_the_plan_and_names_the_next_step(self) -> None:
        code, stdout, _ = self.run_cli("plan", "--study", str(self.paths["study"]))
        self.assertEqual(code, 0)
        report = json.loads(stdout)
        self.assertEqual((report["jobs"], report["jobs_already_cached"]), (8, 0))
        self.assertTrue(report["needs_dataweb_token"])
        self.assertTrue(Path(report["plan_path"]).is_file())
        self.assertIn("acquire --plan outputs/runs/synthetic-study/plan-", report["next_step"])

    def test_legacy_acquisition_needs_an_explicit_flag(self) -> None:
        code, _, stderr = self.run_cli("acquire")
        self.assertEqual(code, 2)
        self.assertIn("--legacy-baseline", stderr)
        with self.assertRaises(ScopeGateError):
            acquire_all(self.config)

    def test_all_never_acquires_even_with_a_token(self) -> None:
        with mock.patch.dict(os.environ, {"DATAWEB_TOKEN": TOKEN}):
            code, stdout, stderr = self.run_cli("all", "--study", str(self.paths["study"]))
        self.assertEqual(code, 2)
        self.assertIn("Query plan not found", stderr)
        self.assertNotIn(TOKEN, stdout + stderr)
        self.assertEqual(list(self.config.paths.raw_dataweb.rglob("*.json")), [])

    def test_legacy_all_never_acquires_even_with_a_token(self) -> None:
        with mock.patch.dict(os.environ, {"DATAWEB_TOKEN": TOKEN}):
            code, stdout, stderr = self.run_cli("all", "--legacy-baseline")
        self.assertEqual(code, 2)
        self.assertIn("acquire --legacy-baseline", stderr)
        self.assertNotIn(TOKEN, stdout + stderr)
        self.assertEqual(list(self.config.paths.raw_dataweb.rglob("*.json")), [])

    def test_scope_validate_cannot_approve(self) -> None:
        code, _, stderr = self.run_cli(
            "scope-validate",
            "--scope",
            str(self.paths["scope_csv"]),
            "--require-approved",
            "--manifest",
            str(self.paths["manifest"]),
        )
        self.assertEqual(code, 2)
        self.assertIn("scope-validate cannot approve", stderr)

    def test_scope_approve_refuses_piped_answers(self) -> None:
        """An agent's shell can pipe the right answers; the command still refuses and writes nothing."""

        directory = self.root / "scopes" / "piped" / "v1"
        reviewed_csv = write_scope_csv(directory / "reviewed.csv", reviewed(history_rows()))
        card = directory / "approval-card.json"
        prefix = scope_module.build_approval_card(reviewed_csv, output_path=card)["scope_sha256"][:8]
        argv = [
            "scope-approve",
            "--scope", str(reviewed_csv),
            "--approval-card", str(card),
            "--reviewer", "agent",
            "--continuity-unverified",
            "--approved-scope", str(directory / "scope.csv"),
            "--manifest", str(directory / "manifest.json"),
        ]
        with mock.patch.object(sys, "stdin", io.StringIO(f"{prefix}\nUNVERIFIED\n")):
            code, _, stderr = self.run_cli(*argv)
        self.assertEqual(code, 2)
        self.assertIn("interactive terminal", stderr)
        self.assertFalse((directory / "scope.csv").exists())
        self.assertFalse((directory / "manifest.json").exists())

    def chat_approve_cli(self, name: str) -> tuple[int, str, str, Path]:
        """scope-approve from an agent's shell (empty stdin) after the user approved the card in chat."""

        directory = self.root / "scopes" / name / "v1"
        reviewed_csv = write_scope_csv(directory / "reviewed.csv", reviewed(history_rows()))
        card = directory / "approval-card.json"
        prefix = scope_module.build_approval_card(reviewed_csv, output_path=card)["scope_sha256"][:8]
        argv = [
            "scope-approve",
            "--scope", str(reviewed_csv),
            "--approval-card", str(card),
            "--reviewer", "Test User",
            "--continuity-unverified",
            "--approved-scope", str(directory / "scope.csv"),
            "--manifest", str(directory / "manifest.json"),
            "--chat-confirmation", "批准",
            "--confirm", prefix,
        ]
        with mock.patch.object(sys, "stdin", io.StringIO("")):
            code, stdout, stderr = self.run_cli(*argv)
        return code, stdout, stderr, directory

    def test_scope_approve_records_a_chat_confirmation_from_an_agent_shell(self) -> None:
        code, stdout, stderr, directory = self.chat_approve_cli("chat")
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["approval"]["user_confirmation"], "批准")
        approved = scope_module.verify_scope_manifest(directory / "manifest.json")
        self.assertEqual(approved.approval_method, scope_module.CHAT_APPROVAL_METHOD)

    def test_a_project_can_require_terminal_approval(self) -> None:
        config = self.paths["config"]
        config.write_text(
            config.read_text(encoding="utf-8") + "\n[approval]\nallow_chat_confirmation = false\n", encoding="utf-8"
        )
        code, _, stderr, directory = self.chat_approve_cli("strict")
        self.assertEqual(code, 2)
        self.assertIn("requires terminal approval", stderr)
        self.assertFalse((directory / "scope.csv").exists())
        self.assertFalse((directory / "manifest.json").exists())

    def test_status_reports_gates_and_limits_from_files(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DATAWEB_TOKEN", None)
            code, stdout, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        status = json.loads(stdout)
        study = next(item for item in status["studies"] if item.get("study_id") == "synthetic-study")
        self.assertEqual((study["gate"], study["continuity_verified"], study["acquired"]), ("open", True, False))
        self.assertEqual(status["scopes"][0]["approval"], "approved")
        self.assertIn("DATAWEB_TOKEN is not set: plan works, acquire --plan does not", status["limits"])

    def test_status_lists_candidate_files_apart_from_the_scope(self) -> None:
        directory = self.root / "scopes" / "drafting" / "v1"
        write_scope_csv(directory / "reviewed.csv", reviewed(history_rows()))
        (directory / "candidates.csv").write_text("candidate_id,hts_code\nabc,1111110010\n", encoding="utf-8")
        code, stdout, _ = self.run_cli("status")
        self.assertEqual(code, 0)
        entry = next(item for item in json.loads(stdout)["scopes"] if item["scope"] == "scopes/drafting/v1")
        self.assertEqual(entry["review_status"], {"reviewed.csv": ["reviewed"]})
        self.assertEqual(entry["candidate_files"], ["candidates.csv"])


if __name__ == "__main__":
    unittest.main()
