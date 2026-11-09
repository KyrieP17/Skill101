from __future__ import annotations

import contextlib
import csv
from datetime import date, datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SKILL_ROOT = Path(__file__).resolve().parents[1]
WRAPPER = SKILL_ROOT / "scripts" / "tariff_pipeline.py"


def load_module(name: str, relative: str):
    path = SKILL_ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


resolver = load_module("hts_scope_resolver", "scripts/hts_scope_resolver.py")
pipeline_wrapper = load_module("skill_tariff_pipeline", "scripts/tariff_pipeline.py")
behavior_runner = load_module("behavior_eval_runner", "evals/run_behavior_evals.py")


class ResolverTests(unittest.TestCase):
    def test_normalize_deduplicates_and_preserves_queries(self):
        source = {
            "htsno": "8471.30.01.00",
            "description": "Portable automatic data processing machines, weighing not more than <il>10 kg</il>",
            "units": ["No."],
            "general": "Free",
            "special": "",
            "other": "35%",
            "statisticalSuffix": "00",
            "indent": "1",
        }
        rows = resolver.normalize_results(
            product_id="laptops",
            product_name="Portable computers",
            searches=[
                ("portable computers", "https://example.test?q=one", [source]),
                ("automatic data processing", "https://example.test?q=two", [source]),
            ],
            retrieved_at="2026-11-05T00:00:00+00:00",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["hts_code"], "8471300100")
        self.assertEqual(rows[0]["hts6"], "847130")
        self.assertEqual(rows[0]["hts8"], "84713001")
        self.assertEqual(rows[0]["units"], "No.")
        self.assertIn("portable computers", rows[0]["search_queries"])
        self.assertIn("automatic data processing", rows[0]["search_queries"])
        self.assertNotIn("<il>", rows[0]["description"])

    def test_special_chapter_is_flagged_not_silently_dropped(self):
        rows = resolver.normalize_results(
            product_id="stoves",
            product_name="Camping stoves",
            searches=[
                (
                    "camping stoves",
                    "https://example.test?q=stoves",
                    [{"htsno": "9902.14.95", "description": "Temporary provision", "units": []}],
                )
            ],
            retrieved_at="2026-11-05T00:00:00+00:00",
        )
        self.assertEqual(rows[0]["hts_level"], 8)
        self.assertTrue(rows[0]["special_chapter"])

    def test_invalid_code_length_is_not_emitted(self):
        rows = resolver.normalize_results(
            product_id="invalid",
            product_name="Invalid example",
            searches=[("invalid", "https://example.test", [{"htsno": "12345", "description": "Bad code"}])],
            retrieved_at="2026-11-05T00:00:00+00:00",
        )
        self.assertEqual(rows, [])

    def test_tag_removal_leaves_no_space_before_punctuation(self):
        self.assertEqual(resolver.clean_text("not more than <il>10 kg</il>, consisting"), "not more than 10 kg, consisting")

    def run_resolver(self, *argv: str, body: bytes = b"[]") -> tuple[int, list[str], str]:
        """Run the resolver against a fake HTS endpoint; return exit code, URLs asked and stdout."""

        asked: list[str] = []

        def fake_urlopen(request, timeout):
            asked.append(request.full_url)
            return io.BytesIO(body)

        stdout = io.StringIO()
        with mock.patch.object(resolver, "urlopen", fake_urlopen), contextlib.redirect_stdout(stdout):
            with contextlib.redirect_stderr(io.StringIO()):
                code = resolver.main(["--product-id", "phones", "--product-name", "Smartphones", *argv])
        return code, asked, stdout.getvalue()

    def test_phrase_gets_its_quotes_from_the_resolver_not_the_shell(self):
        self.assertEqual(
            resolver.search_terms(["smartphones"], ["telephones for cellular networks", '"already quoted"']),
            ["smartphones", '"telephones for cellular networks"', '"already quoted"'],
        )
        code, asked, _ = self.run_resolver("--phrase", "telephones for cellular networks")
        self.assertEqual(code, 0)
        self.assertEqual(asked, [resolver.search_url(resolver.DEFAULT_ENDPOINT, '"telephones for cellular networks"')])
        self.assertIn("%22telephones+for+cellular+networks%22", asked[0])
        self.assertEqual(self.run_resolver()[0], 2)  # neither --keyword nor --phrase

    def test_structure_output_keeps_the_response_bytes_for_drilldown(self):
        body = b'[{"htsno": "8517.13.00", "description": "Smartphones", "units": ["No."]}]'
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "history" / "hts_rest_search_8517.13.json"
            code, _, stdout = self.run_resolver("--keyword", "8517.13", "--structure-output", str(target), body=body)
            self.assertEqual(code, 0)
            self.assertEqual(target.read_bytes(), body)
            saved = json.loads(stdout)["summary"]["structure_file"]
            self.assertEqual((saved["bytes"], saved["sha256"]), (len(body), resolver.sha256(body).hexdigest()))
            code, _, _ = self.run_resolver("--keyword", "8517.13", "--structure-output", str(target), body=body)
            self.assertEqual(code, 2)  # never overwritten without --force
            code, _, _ = self.run_resolver(
                "--keyword", "8517.13", "--keyword", "8517.14", "--structure-output", str(target) + "2", body=body
            )
            self.assertEqual(code, 2)  # one search per structure file

    def test_fetch_results_keeps_the_two_values_hts_current_check_unpacks(self):
        body = b'[{"htsno": "8517.13.00"}]'
        with mock.patch.object(resolver, "urlopen", lambda request, timeout: io.BytesIO(body)):
            url, rows = resolver.fetch_results(resolver.DEFAULT_ENDPOINT, "8517.13", 5)
        self.assertEqual((url, rows), (resolver.search_url(resolver.DEFAULT_ENDPOINT, "8517.13"), [{"htsno": "8517.13.00"}]))


current_check =load_module("hts_current_check", "scripts/hts_current_check.py")

LITHIUM_CURRENT = [
    {"htsno": "8507.60.00", "description": "Lithium-ion batteries", "units": []},
    {"htsno": "8507.60.00.10", "description": "Of a kind used as the primary source of electrical power", "units": ["No.", "kg"]},
    {"htsno": "", "description": "Other:", "units": []},
    {"htsno": "8507.60.00.30", "description": "Battery energy storage systems", "units": ["No.", "kg"]},
    {"htsno": "8507.60.00.90", "description": "Other", "units": ["No.", "kg"]},
    {"htsno": "9902.16.20", "description": "Rechargeable lithium batteries (provided for in subheading 8507.60.00)", "units": []},
]


class CurrentReleaseCheckTests(unittest.TestCase):
    """The pre-acquisition check compares scope lines with the live release, offline here."""

    def check(self, rows, *, as_of=date(2026, 11, 6), current=LITHIUM_CURRENT):
        fields = ["decision", "hts_level", "hts_code", "description", "quantity_unit", "valid_from", "valid_to", "second_unit"]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "scope.csv"
            with path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                writer.writerows(dict(zip(fields, (*row, "")[: len(fields)])) for row in rows)
            return current_check.build_report(
                path, as_of, lambda keyword: (f"https://example.test?keyword={keyword}", current), endpoint="test"
            )

    def test_dotted_codes(self):
        self.assertEqual(current_check.dotted("850760"), "8507.60")
        self.assertEqual(current_check.dotted("8507600010"), "8507.60.00.10")

    def test_unchanged_hts6_line_passes(self):
        report = self.check([("include", "6", "850760", "Lithium-ion batteries", "No.", "2017-01-01", "2026-12-31", "kg")])
        self.assertTrue(report["ok"])
        line = report["checked"][0]
        self.assertEqual(line["current_line"]["htsno"], "8507.60.00")
        self.assertEqual(len(line["current_children"]), 3)
        self.assertFalse(report["dataweb_token_used"])

    def test_split_code_and_changed_text_are_flagged(self):
        report = self.check(
            [
                ("include", "10", "8507600010", "Old wording for EV batteries", "No.", "2022-01-01", "2026-12-31", "kg"),
                ("include", "10", "8507600020", "Other", "No.", "2022-01-01", "2026-12-31"),
            ]
        )
        self.assertFalse(report["ok"])
        findings = {line["hts_code"]: line["findings"] for line in report["checked"]}
        self.assertEqual(findings["8507600010"], ["description_changed"])
        self.assertTrue(findings["8507600020"][0].startswith("code_missing"))

    def test_unit_change_is_flagged(self):
        report = self.check([("include", "10", "8507600090", "Other", "kg", "2022-01-01", "2026-12-31")])
        self.assertIn("unit_changed", report["checked"][0]["findings"][0])

    def test_unrecorded_second_unit_is_flagged(self):
        report = self.check([("include", "6", "850760", "Lithium-ion batteries", "No.", "2017-01-01", "2026-12-31")])
        self.assertEqual(report["checked"][0]["findings"], ["second_unit_changed: scope '', current 'kg'"])

    def test_rows_not_valid_on_as_of_are_not_checked(self):
        report = self.check(
            [
                ("include", "6", "850760", "Lithium-ion batteries", "No.", "2017-01-01", "2021-12-31"),
                ("exclude", "6", "850650", "Lithium", "", "2017-01-01", "2026-12-31"),
            ]
        )
        self.assertEqual(report["checked"], [])
        self.assertEqual(report["not_valid_on_as_of"][0]["hts_code"], "850760")
        self.assertFalse(report["ok"])


class PipelineContractTests(unittest.TestCase):
    """The skill's scope tools must speak the pipeline's contract exactly.

    The rules themselves are tested in the pipeline project; these tests check
    that the wrapper really delegates and that the templates still match.
    """

    @classmethod
    def setUpClass(cls):
        try:
            cls.scope = pipeline_wrapper.load_pipeline_module("scope")
            cls.study = pipeline_wrapper.load_pipeline_module("study")
        except (pipeline_wrapper.SetupError, ImportError) as exc:
            raise unittest.SkipTest(f"pipeline project not available: {exc}")

    def valid_row(self, **overrides):
        row = {
            "scope_id": "laptops",
            "scope_version": "1",
            "product_id": "laptops",
            "product_name": "Portable computers",
            "hts_release": "2026 Revision 20",
            "hts_level": "10",
            "hts_code": "8471300100",
            "description": "Portable automatic data processing machines",
            "quantity_unit": "No.",
            "decision": "include",
            "rationale": "Matches the finished portable-computer product definition.",
            "valid_from": "2026-01-01",
            "valid_to": "2026-12-31",
            "review_status": "approved",
            "reviewer": "analyst",
            "approved_at_utc": datetime(2026, 11, 5, tzinfo=timezone.utc).isoformat(),
            "source_url": "https://hts.usitc.gov/",
        }
        row.update(overrides)
        return row

    def write_scope(self, directory, rows):
        path = Path(directory) / "scope.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=self.scope.REQUIRED_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)
        return path

    def run_wrapper(self, *args, stdin_text=""):
        """Run the real wrapper as the agent would: a child process whose stdin is a pipe."""

        environment = {key: value for key, value in os.environ.items() if key != "DATAWEB_TOKEN"}
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, str(WRAPPER), "--python", sys.executable, *args],
            input=stdin_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
            timeout=120,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def test_scope_template_header_matches_pipeline_contract(self):
        with (SKILL_ROOT / "assets" / "product_scope_template.csv").open(encoding="utf-8-sig", newline="") as handle:
            header = next(csv.reader(handle))
        self.assertEqual(header, [*self.scope.REQUIRED_COLUMNS, *self.scope.OPTIONAL_COLUMNS])

    def reviewed_row(self, **overrides):
        return self.valid_row(review_status="reviewed", reviewer="", approved_at_utc="", **overrides)

    def test_wrapper_applies_the_pipeline_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_scope(directory, [self.valid_row()])
            code, stdout, stderr = self.run_wrapper("scope-validate", "--scope", str(path), "--require-approved")
            self.assertEqual(code, 0, stderr)
            self.assertEqual(json.loads(stdout)["included_codes"], ["8471300100"])

            path = self.write_scope(directory, [self.valid_row(), self.valid_row(decision="exclude")])
            code, _, stderr = self.run_wrapper("scope-validate", "--scope", str(path))
            self.assertEqual(code, 2)
            self.assertIn("conflicting decisions", stderr)

    def test_scope_validate_never_approves(self):
        with tempfile.TemporaryDirectory() as directory:
            path = self.write_scope(directory, [self.reviewed_row()])
            manifest = Path(directory) / "manifest.json"
            code, _, stderr = self.run_wrapper("scope-validate", "--scope", str(path), "--manifest", str(manifest))
            self.assertEqual(code, 2)
            self.assertIn("cannot approve", stderr)
            self.assertFalse(manifest.exists())

    def test_scope_approve_through_the_wrapper_refuses_an_agent_shell(self):
        """Even with the right answers piped in, nothing is approved outside an interactive terminal."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reviewed = self.write_scope(directory, [self.reviewed_row()])
            card = root / "approval-card.json"
            code, _, stderr = self.run_wrapper("scope-validate", "--scope", str(reviewed), "--approval-card", str(card))
            self.assertEqual(code, 0, stderr)
            prefix = json.loads(card.read_text(encoding="utf-8"))["scope_sha256"][:8]
            approved, manifest = root / "approved.csv", root / "manifest.json"
            code, _, stderr = self.run_wrapper(
                "scope-approve",
                "--scope", str(reviewed),
                "--approval-card", str(card),
                "--reviewer", "agent",
                "--continuity-unverified",
                "--approved-scope", str(approved),
                "--manifest", str(manifest),
                stdin_text=f"{prefix}\nUNVERIFIED\n",
            )
            self.assertEqual(code, 2)
            self.assertIn("interactive terminal", stderr)
            self.assertFalse(approved.exists())
            self.assertFalse(manifest.exists())

    def test_study_template_is_a_valid_study(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "config" / "studies" / "example.toml"
            target.parent.mkdir(parents=True)
            template = SKILL_ROOT / "assets" / "study_template.toml"
            target.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
            spec = self.study.load_study(target, project_root=root, today=date(2026, 11, 5))
        self.assertEqual(spec.periods(), {"annual": [2017, 2025], "monthly": [2023, 2026]})
        self.assertEqual(spec.focal_origin, "China")


class BehaviorEvalContractTests(unittest.TestCase):
    def test_behavior_cases_have_unique_ids_and_routing_labels(self):
        cases = behavior_runner.load_cases(SKILL_ROOT / "evals" / "behavior_cases.json")
        summary = behavior_runner.validate_cases(cases)
        self.assertEqual(summary, {"total": 21, "trigger": 14, "nontrigger": 7})
        paraphrases = [case for case in cases if "variant_of" in case]
        self.assertGreaterEqual(sum(case["should_trigger"] for case in paraphrases), 2)
        self.assertGreaterEqual(sum(not case["should_trigger"] for case in paraphrases), 2)

    def test_suite_rejects_a_paraphrase_of_nothing(self):
        cases = behavior_runner.load_cases(SKILL_ROOT / "evals" / "behavior_cases.json")
        cases[0] = {**cases[0], "variant_of": "no-such-case"}
        with self.assertRaisesRegex(behavior_runner.ContractError, "variant_of"):
            behavior_runner.validate_cases(cases)

    def test_behavior_runner_scores_contracts_and_detects_forbidden_behavior(self):
        cases = behavior_runner.load_cases(SKILL_ROOT / "evals" / "behavior_cases.json")
        results = [
            {
                "id": case["id"],
                "triggered": case["should_trigger"],
                "mode": case["expected_mode"],
                "behaviors": list(case["required_behavior"]),
            }
            for case in cases
        ]
        passing = behavior_runner.evaluate_results(cases, results)
        self.assertTrue(passing["ok"])
        self.assertEqual(passing["passed"], len(cases))

        first_forbidden = cases[0]["forbidden_behavior"][0]
        results[0]["behaviors"].append(first_forbidden)
        failing = behavior_runner.evaluate_results(cases, results)
        self.assertFalse(failing["ok"])
        self.assertEqual(failing["failures"][0]["id"], cases[0]["id"])



FAKE_MODEL = """
import json, sys
prompt = sys.stdin.buffer.read().decode("utf-8")
request = prompt.split("User request:\\n", 1)[1].split("\\n\\nReply with JSON only", 1)[0]
answers = json.loads(open(sys.argv[1], encoding="utf-8").read())
sys.stdout.buffer.write(("Sure. " + json.dumps({"skill": answers[request]}) + " Done.").encode("utf-8"))
"""


class TriggerEvalTests(unittest.TestCase):
    """The trigger runner, driven offline by a stand-in model command."""

    def setUp(self):
        self.cases = behavior_runner.load_cases(SKILL_ROOT / "evals" / "behavior_cases.json")
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        (self.tmp / "model.py").write_text(FAKE_MODEL, encoding="utf-8")

    def executor(self, answer):
        answers = {case["prompt"]: answer(case) for case in self.cases}
        target = self.tmp / f"answers-{len(list(self.tmp.iterdir()))}.json"
        target.write_text(json.dumps(answers, ensure_ascii=False), encoding="utf-8")
        return f'"{sys.executable}" "{self.tmp / "model.py"}" "{target}"'

    def test_dry_run_prints_one_prompt_per_case_and_sends_nothing(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(behavior_runner.main(["--trigger", "--dry-run"]), 0)
        prompts = json.loads(out.getvalue())
        self.assertEqual([item["id"] for item in prompts], [case["id"] for case in self.cases])
        for item, case in zip(prompts, self.cases):
            self.assertIn("tariff-sourcing-research", item["prompt"])
            self.assertIn(case["prompt"], item["prompt"])
            for name, _ in behavior_runner.DISTRACTORS:
                self.assertIn(name, item["prompt"])

    def test_trigger_eval_scores_a_model_command(self):
        skill = "tariff-sourcing-research"
        right = behavior_runner.run_trigger_eval(
            self.cases, self.executor(lambda case: skill if case["should_trigger"] else "none")
        )
        self.assertTrue(right["ok"])
        self.assertEqual(right["accuracy"], 1.0)

        eager = behavior_runner.run_trigger_eval(self.cases, self.executor(lambda case: skill))
        self.assertFalse(eager["ok"])
        self.assertEqual(eager["correct"], 14)
        self.assertEqual(eager["false_triggers"], [case["id"] for case in self.cases if not case["should_trigger"]])

    def test_unreadable_answers_count_as_wrong(self):
        choices = {case["id"]: None for case in self.cases}
        report = behavior_runner.score_triggers(self.cases, choices, "tariff-sourcing-research")
        self.assertEqual((report["correct"], len(report["unreadable"])), (0, 21))
        self.assertEqual(behavior_runner.parse_choice('noise {"skill": "none"} tail'), "none")

    def test_trigger_needs_an_executor(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(behavior_runner.main(["--trigger"]), 2)


class NewProductEndToEndTests(unittest.TestCase):
    """A product with no code of its own goes scope -> card -> approval -> plan and stops before acquisition.

    Only data files are copied (the real lithium-ion reviewed scope and study);
    the engine is the project's unchanged code, driven through the wrapper the
    agent uses.  The approval step stands in for the user's own terminal.
    """

    STUDY = "config/studies/lithium-ion-2017-2026.toml"
    SCOPE = "scopes/lithium-ion/v1"

    @classmethod
    def setUpClass(cls):
        try:
            cls.real = pipeline_wrapper.find_project_root()
            cls.scope = pipeline_wrapper.load_pipeline_module("scope")
        except (pipeline_wrapper.SetupError, ImportError) as exc:
            raise unittest.SkipTest(f"pipeline project not available: {exc}")
        if not ((cls.real / cls.SCOPE / "reviewed.csv").is_file() and (cls.real / cls.STUDY).is_file()):
            raise unittest.SkipTest("the lithium-ion example scope or study is not in this project")

    def setUp(self):
        self.root = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        ignore = shutil.ignore_patterns("__pycache__")
        shutil.copytree(self.real / "src" / "usitc_pipeline", self.root / "src" / "usitc_pipeline", ignore=ignore)
        shutil.copy2(self.real / "run.py", self.root / "run.py")
        (self.root / "config" / "studies").mkdir(parents=True)
        for name in ("research.toml", "policy_events.csv", "hts_naics_proxy.csv"):
            shutil.copy2(self.real / "config" / name, self.root / "config" / name)
        shutil.copy2(self.real / self.STUDY, self.root / self.STUDY)
        (self.root / self.SCOPE).mkdir(parents=True)
        shutil.copy2(self.real / self.SCOPE / "reviewed.csv", self.root / self.SCOPE / "reviewed.csv")

    def wrapper(self, *args):
        environment = {key: value for key, value in os.environ.items() if key not in {"DATAWEB_TOKEN", "TARIFF_PIPELINE_ROOT"}}
        environment["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            [sys.executable, str(WRAPPER), "--project-root", str(self.root), "--python", sys.executable, *args],
            input="",
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
            timeout=300,
        )
        return completed.returncode, completed.stdout, completed.stderr

    def test_new_product_reaches_plan_and_stops_before_acquisition_without_a_token(self):
        scope_dir = self.root / self.SCOPE
        code, _, stderr = self.wrapper(
            "scope-validate", "--scope", f"{self.SCOPE}/reviewed.csv", "--approval-card", f"{self.SCOPE}/approval-card.json"
        )
        self.assertEqual(code, 0, stderr)
        card = json.loads((scope_dir / "approval-card.json").read_text(encoding="utf-8"))
        self.assertEqual(card["included_codes"], ["850760"])
        self.assertEqual(card["second_unit"], "kg")
        self.assertEqual(card["continuity"]["status"], "unverified")

        code, _, stderr = self.wrapper("plan", "--study", self.STUDY)
        self.assertEqual(code, 2, "no plan before the user approves")
        self.assertIn("scope-approve", stderr)

        answers = iter([card["scope_sha256"][:8], "UNVERIFIED"])
        self.scope.approve_scope(
            scope_dir / "reviewed.csv",
            approval_card=scope_dir / "approval-card.json",
            approved_scope=scope_dir / "scope.csv",
            manifest_path=scope_dir / "manifest.json",
            reviewer="test-reviewer",
            continuity_unverified=True,
            prompt=lambda _question: next(answers),
            out=lambda _text: None,
            interactive=True,
        )

        code, stdout, stderr = self.wrapper("plan", "--study", self.STUDY)
        self.assertEqual(code, 0, stderr)
        plan = json.loads(stdout)
        self.assertEqual(
            plan["codes_by_period"],
            [
                {"frequency": "annual", "start": 2017, "end": 2025, "codes": ["850760"]},
                {"frequency": "monthly", "start": 2017, "end": 2026, "codes": ["850760"]},
            ],
        )
        self.assertFalse(plan["scope"]["continuity_verified"])
        self.assertTrue(plan["needs_dataweb_token"])

        code, stdout, stderr = self.wrapper("acquire", "--plan", plan["plan_path"])
        self.assertEqual(code, 2)
        self.assertIn("DATAWEB_TOKEN is required", stderr)
        self.assertEqual(list((self.root / "data").rglob("*.json")), [])
        self.assertFalse((Path(plan["plan_path"]).parent / "acquisition.json").exists())


class WrapperTests(unittest.TestCase):
    """Project lookup, interpreter choice and token handling, with no real project needed."""

    TOKEN = "test-token-not-a-real-credential"

    def setUp(self):
        self.project = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.project, ignore_errors=True)
        (self.project / "src" / "usitc_pipeline").mkdir(parents=True)
        (self.project / "run.py").write_text("", encoding="utf-8")
        environment = mock.patch.dict(os.environ, {"DATAWEB_TOKEN": self.TOKEN})
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop("TARIFF_PIPELINE_ROOT", None)
        config = mock.patch.object(pipeline_wrapper, "load_config", return_value={"project_root": str(self.project)})
        config.start()
        self.addCleanup(config.stop)

    def test_wrapper_passes_through_the_pipeline_commands(self):
        expected = {"status", "scope-validate", "hts-history", "scope-approve", "plan", "acquire"}
        expected |= {"analyze", "charts", "validate", "report", "all", "brief-check", "report-pdf", "registry-check"}
        self.assertEqual(set(pipeline_wrapper.PIPELINE_COMMANDS), expected)
        self.assertEqual(pipeline_wrapper.TOKEN_COMMANDS, {"acquire"})

    def test_only_acquire_receives_the_token(self):
        for command, forwarded in (("acquire", True), ("analyze", False), ("scope-approve", False), ("test", False)):
            with self.subTest(command=command), mock.patch.object(pipeline_wrapper.subprocess, "run") as run:
                run.return_value = subprocess.CompletedProcess([], 0)
                self.assertEqual(pipeline_wrapper.main(["--python", sys.executable, command]), 0)
                self.assertEqual("DATAWEB_TOKEN" in run.call_args.kwargs["env"], forwarded)
                self.assertEqual(Path(run.call_args.kwargs["cwd"]), self.project)

    def test_dry_run_reports_forwarding_but_never_the_token(self):
        for command, forwarded in (("acquire", True), ("plan", False)):
            out = io.StringIO()
            with self.subTest(command=command), contextlib.redirect_stdout(out):
                self.assertEqual(pipeline_wrapper.main(["--dry-run", "--python", sys.executable, command]), 0)
            self.assertIs(json.loads(out.getvalue())["dataweb_token_forwarded"], forwarded)
            self.assertNotIn(self.TOKEN, out.getvalue())

    def test_missing_or_wrong_project_says_to_ask_the_user(self):
        err = io.StringIO()
        with mock.patch.object(pipeline_wrapper, "load_config", return_value={}), contextlib.redirect_stderr(err):
            self.assertEqual(pipeline_wrapper.main(["status"]), 2)
        self.assertIn("Ask the user", err.getvalue())
        self.assertIn("config.json", err.getvalue())
        # A wrong explicit path is an error, never a silent fall-back to config.json.
        with self.assertRaisesRegex(pipeline_wrapper.SetupError, "no run.py"):
            pipeline_wrapper.find_project_root(str(self.project / "missing"), config={"project_root": str(self.project)})

    def test_configured_python_is_relative_to_the_project(self):
        python = self.project / "env" / "python.exe"
        python.parent.mkdir()
        python.write_text("", encoding="utf-8")
        self.assertEqual(pipeline_wrapper.project_python(self.project, None, {"python": "env/python.exe"}), python)
        with self.assertRaisesRegex(pipeline_wrapper.SetupError, "does not exist"):
            pipeline_wrapper.project_python(self.project, None, {"python": "env/missing.exe"})
        explicit = pipeline_wrapper.project_python(self.project, sys.executable, {"python": "env/missing.exe"})
        self.assertEqual(explicit, Path(sys.executable).absolute())


class SkillStructureTests(unittest.TestCase):
    """The layout the skill promises: short SKILL.md, working links, no stored credential."""

    def test_skill_md_is_short_and_holds_no_project_history(self):
        text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        self.assertLessEqual(len(text.splitlines()), 60)
        for stale in ("research.toml", "--legacy-baseline", "import-legacy", "D:\\", "Current Limits"):
            self.assertNotIn(stale, text)

    def test_every_relative_link_resolves(self):
        documents = [SKILL_ROOT / "SKILL.md", *sorted((SKILL_ROOT / "references").rglob("*.md"))]
        documents += sorted((SKILL_ROOT / "assets").glob("*.md"))
        for document in documents:
            for target in re.findall(r"\]\(([^)#\s]+)[^)]*\)", document.read_text(encoding="utf-8")):
                if "://" in target:
                    continue
                with self.subTest(document=document.name, link=target):
                    self.assertTrue((document.parent / target).exists())

    def test_example_says_it_is_an_example(self):
        lines = (SKILL_ROOT / "references" / "examples" / "laptops.md").read_text(encoding="utf-8").splitlines()
        self.assertIn("这是例子，不是模板", "\n".join(lines[:4]))

    def test_no_credential_is_stored_in_the_skill(self):
        jwt = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.")
        for path in sorted(SKILL_ROOT.rglob("*")):
            if path.is_file() and path.suffix in {".md", ".py", ".json", ".toml", ".csv", ".yaml"}:
                with self.subTest(path=path.relative_to(SKILL_ROOT).as_posix()):
                    self.assertIsNone(jwt.search(path.read_text(encoding="utf-8", errors="ignore")))
        # A fresh clone has only config.example.json; the local config.json is not committed.
        for name in ("config.json", "config.example.json"):
            if (SKILL_ROOT / name).is_file():
                with self.subTest(config=name):
                    config = json.loads((SKILL_ROOT / name).read_text(encoding="utf-8"))
                    self.assertLessEqual(set(config), {"project_root", "python"})

    def test_example_config_does_not_hard_code_a_platform_interpreter(self):
        config = json.loads((SKILL_ROOT / "config.example.json").read_text(encoding="utf-8"))
        self.assertEqual(set(config), {"project_root"})


if __name__ == "__main__":
    unittest.main()
