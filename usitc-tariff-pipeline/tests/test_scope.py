"""Tests for the approved-scope contract (pure Python; no pandas needed).

All HTS codes below are synthetic.  The tests check the rules that make an
approved scope safe to drive DataWeb queries, not any real classification.
"""

from __future__ import annotations

import contextlib
import csv
import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from usitc_pipeline import scope as scope_module  # noqa: E402
from usitc_pipeline import hts_history  # noqa: E402
from usitc_pipeline.scope import ScopeError, ScopeGateError  # noqa: E402


def make_row(**overrides: str) -> dict[str, str]:
    row = {
        "scope_id": "synthetic",
        "scope_version": "1",
        "product_id": "synthetic-product",
        "product_name": "Synthetic product",
        "hts_release": "2026 Basic (synthetic)",
        "hts_level": "10",
        "hts_code": "1111110010",
        "description": "Synthetic finished good",
        "quantity_unit": "No.",
        "decision": "include",
        "rationale": "Synthetic finished-good line used by tests.",
        "valid_from": "2017-01-01",
        "valid_to": "2026-12-31",
        "review_status": "approved",
        "reviewer": "test-analyst",
        "approved_at_utc": "2026-11-05T00:00:00+00:00",
        "source_url": "https://hts.usitc.gov/",
    }
    row.update(overrides)
    return row


def history_rows() -> list[dict[str, str]]:
    """A code split in 2022: A until 2021, then B1 + B2; parts line C excluded."""

    return [
        make_row(hts_code="1111110010", valid_to="2021-12-31", hts_release="2017 Basic (synthetic)"),
        make_row(hts_code="1111110020", valid_from="2022-01-01", hts_release="2022 Basic (synthetic)"),
        make_row(hts_code="1111110030", valid_from="2022-01-01", hts_release="2022 Basic (synthetic)"),
        make_row(
            hts_code="1111119000",
            decision="exclude",
            quantity_unit="kg",
            description="Synthetic parts",
            rationale="Parts are not the finished good.",
        ),
    ]


def write_scope_csv(path: Path, rows: list[dict[str, str]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    optional = [column for column in scope_module.OPTIONAL_COLUMNS if any(row.get(column) for row in rows)]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*scope_module.REQUIRED_COLUMNS, *optional], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path


def reviewed(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [
        {**row, "review_status": "reviewed", "reviewer": "", "approved_at_utc": ""}
        for row in rows
    ]


def write_history_catalog(scope_csv: Path, catalog: Path, *, shorten_first: bool = False) -> Path:
    """Write a synthetic downloaded-edition file and a hash-backed catalog."""

    source = catalog.parent / "official-editions.txt"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("Synthetic official HTS edition fixture.\n", encoding="utf-8")
    source_hash = scope_module.file_sha256(source)
    with scope_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    fields = list(hts_history.CATALOG_COLUMNS)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    with catalog.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for index, row in enumerate(item for item in rows if item["decision"] == "include"):
            writer.writerow(
                {
                    "edition": row["hts_release"],
                    "effective_from": row["valid_from"],
                    "effective_to": "2020-12-31" if shorten_first and index == 0 else row["valid_to"],
                    "hts_level": row["hts_level"],
                    "hts_code": row["hts_code"],
                    "description": row["description"],
                    "quantity_unit": row["quantity_unit"],
                    "source_url": row["source_url"],
                    "source_file": source.name,
                    "source_sha256": source_hash,
                }
            )
    return catalog


def typed(*answers: str):
    """Stand in for the reviewer typing at the terminal."""

    replies = iter(answers)
    return lambda _prompt: next(replies)


def approve(
    reviewed_scope: Path,
    card: Path,
    approved_scope: Path,
    manifest: Path,
    *,
    history: Path | None = None,
    answers: tuple[str, ...] | None = None,
    interactive: bool | None = True,
) -> dict:
    """Run scope-approve the way a reviewer would, with typed answers injected."""

    if answers is None:
        prefix = scope_module.validate_scope(
            scope_module._parse_scope(reviewed_scope.read_bytes()), require_approved=False
        )["scope_sha256"][:8]
        answers = (prefix,) if history is not None else (prefix, "UNVERIFIED")
    return scope_module.approve_scope(
        reviewed_scope,
        approval_card=card,
        approved_scope=approved_scope,
        manifest_path=manifest,
        reviewer="test-reviewer",
        history_evidence=history,
        continuity_unverified=history is None,
        prompt=typed(*answers),
        out=lambda _text: None,
        interactive=interactive,
    )


def freeze_scope(scope_csv: Path, manifest: Path, *, with_history: bool = True) -> dict:
    """Review card -> (history evidence) -> scope-approve -> frozen manifest."""

    with scope_csv.open("r", encoding="utf-8-sig", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    reviewed_scope = write_scope_csv(scope_csv.with_name("reviewed.csv"), reviewed(source_rows))
    scope_csv.unlink()
    card = scope_csv.with_name("approval-card.json")
    history = None
    if with_history:
        history = scope_csv.with_name("hts-history.json")
        catalog = write_history_catalog(reviewed_scope, scope_csv.parent / "history" / "catalog.csv")
        hts_history.build_history_evidence(reviewed_scope, catalog, output_path=history)
    scope_module.build_approval_card(reviewed_scope, output_path=card, history_evidence=history)
    return approve(reviewed_scope, card, scope_csv, manifest, history=history)


def run_captured(main, argv: list[str]) -> tuple[int, str, str]:
    """Run a command-line ``main`` and return its exit code, stdout, and stderr."""

    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            code = main(argv)
        except SystemExit as exc:  # argparse usage errors exit with 2
            code = exc.code
    return code, stdout.getvalue(), stderr.getvalue()


class ScopeValidationTests(unittest.TestCase):
    def test_semantic_hash_ignores_rationale_but_record_hash_does_not(self) -> None:
        first = scope_module.validate_scope(history_rows(), require_approved=True)
        edited = history_rows()
        edited[0]["rationale"] = "Same decision, clearer wording."
        second = scope_module.validate_scope(edited, require_approved=True)
        self.assertEqual(first["scope_sha256"], second["scope_sha256"])
        self.assertNotEqual(first["record_sha256"], second["record_sha256"])

    def test_hash_does_not_depend_on_row_order(self) -> None:
        rows = history_rows()
        forward = scope_module.validate_scope(rows, require_approved=True)
        backward = scope_module.validate_scope(list(reversed(rows)), require_approved=True)
        self.assertEqual(forward["scope_sha256"], backward["scope_sha256"])
        self.assertEqual(forward["record_sha256"], backward["record_sha256"])

    def test_include_and_exclude_of_one_code_in_one_period_is_rejected(self) -> None:
        rows = [make_row(), make_row(decision="exclude", rationale="Contradiction.")]
        with self.assertRaisesRegex(ScopeError, "conflicting decisions"):
            scope_module.validate_scope(rows, require_approved=True)

    def test_overlapping_windows_for_one_code_are_rejected(self) -> None:
        rows = [make_row(valid_to="2022-12-31"), make_row(valid_from="2022-01-01")]
        with self.assertRaisesRegex(ScopeError, "overlapping validity windows"):
            scope_module.validate_scope(rows, require_approved=True)

    def test_mixed_included_levels_are_rejected(self) -> None:
        rows = [make_row(), make_row(hts_level="6", hts_code="222222")]
        with self.assertRaisesRegex(ScopeError, "one HTS level"):
            scope_module.validate_scope(rows, require_approved=True)

    def test_chapter_99_line_cannot_be_included(self) -> None:
        with self.assertRaisesRegex(ScopeError, "Chapter 98/99"):
            scope_module.validate_scope([make_row(hts_code="9903881500")], require_approved=True)

    def test_chapter_99_line_may_be_recorded_as_an_exclusion(self) -> None:
        rows = [make_row(), make_row(hts_code="9903881500", decision="exclude", rationale="Policy provision.")]
        summary = scope_module.validate_scope(rows, require_approved=True)
        self.assertEqual(summary["excluded_codes"], ["9903881500"])

    def test_exclusion_inside_a_coarser_included_code_is_rejected(self) -> None:
        rows = [
            make_row(hts_level="6", hts_code="111111"),
            make_row(hts_code="1111110010", decision="exclude", rationale="Cannot be subtracted at HTS6."),
        ]
        with self.assertRaisesRegex(ScopeError, "cannot subtract"):
            scope_module.validate_scope(rows, require_approved=True)

    def test_history_scope_allows_several_releases(self) -> None:
        summary = scope_module.validate_scope(history_rows(), require_approved=True)
        self.assertEqual(
            summary["hts_releases"],
            ["2017 Basic (synthetic)", "2022 Basic (synthetic)", "2026 Basic (synthetic)"],
        )
        self.assertEqual(summary["coverage"], {"start": "2017-01-01", "end": "2026-12-31", "gaps": []})
        self.assertTrue(summary["quantity_comparable"])
        self.assertEqual(summary["quantity_unit"], "no.")

    def test_coverage_gap_is_reported(self) -> None:
        rows = [
            make_row(hts_code="1111110010", valid_to="2019-12-31"),
            make_row(hts_code="1111110020", valid_from="2021-01-01"),
        ]
        summary = scope_module.validate_scope(rows, require_approved=True)
        self.assertEqual(summary["coverage"]["gaps"], [["2020-01-01", "2020-12-31"]])

    def test_quantity_needs_one_known_unit(self) -> None:
        mixed = [make_row(), make_row(hts_code="1111110020", quantity_unit="kg")]
        self.assertFalse(scope_module.validate_scope(mixed, require_approved=True)["quantity_comparable"])
        unknown = [make_row(quantity_unit="")]
        self.assertFalse(scope_module.validate_scope(unknown, require_approved=True)["quantity_comparable"])

    def test_second_unit_is_recorded_without_touching_scopes_that_have_none(self) -> None:
        plain = scope_module.validate_scope([make_row()], require_approved=True)
        blank = scope_module.validate_scope([make_row(second_unit="")], require_approved=True)
        self.assertEqual((blank["scope_sha256"], blank["record_sha256"]), (plain["scope_sha256"], plain["record_sha256"]))
        self.assertEqual((plain["second_unit"], plain["second_units"]), (None, []))

        both = scope_module.validate_scope([make_row(second_unit="KG")], require_approved=True)
        self.assertEqual((both["second_unit"], both["second_units"]), ("kg", ["kg"]))
        self.assertNotEqual(both["scope_sha256"], plain["scope_sha256"])
        self.assertEqual(both["quantity_unit"], "no.")  # the first unit still drives quantity

        partial = scope_module.validate_scope(
            [make_row(second_unit="kg"), make_row(hts_code="1111110020")], require_approved=True
        )
        self.assertEqual((partial["second_unit"], partial["second_units"]), (None, ["kg"]))
        self.assertIn("some included lines only", scope_module.second_unit_text(None, partial["second_units"]))
        self.assertEqual(scope_module.second_unit_text(None, []), "none")

    def test_second_unit_must_differ_from_the_first(self) -> None:
        with self.assertRaisesRegex(ScopeError, "repeats quantity_unit"):
            scope_module.validate_scope([make_row(second_unit="no.")], require_approved=True)
        with self.assertRaisesRegex(ScopeError, "needs a quantity_unit"):
            scope_module.validate_scope([make_row(quantity_unit="", second_unit="kg")], require_approved=True)

    def test_require_approved_rejects_candidate_rows(self) -> None:
        rows = [make_row(review_status="reviewed", reviewer="", approved_at_utc="")]
        self.assertFalse(scope_module.validate_scope(rows, require_approved=False)["all_records_approved"])
        with self.assertRaisesRegex(ScopeError, "require-approved"):
            scope_module.validate_scope(rows, require_approved=True)


class ApprovalAndHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.reviewed_scope = write_scope_csv(self.root / "reviewed.csv", reviewed(history_rows()))
        self.card = self.root / "approval-card.json"
        self.catalog = self.root / "history" / "catalog.csv"
        self.evidence = self.root / "hts-history.json"
        self.approved_scope = self.root / "scope.csv"
        self.manifest = self.root / "manifest.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def history(self, *, shorten_first: bool = False) -> dict:
        write_history_catalog(self.reviewed_scope, self.catalog, shorten_first=shorten_first)
        return hts_history.build_history_evidence(self.reviewed_scope, self.catalog, output_path=self.evidence)

    def card_for(self, *, verified: bool) -> dict:
        if verified:
            self.history()
        return scope_module.build_approval_card(
            self.reviewed_scope, output_path=self.card, history_evidence=self.evidence if verified else None
        )

    def approve(self, *, verified: bool = True, **options) -> dict:
        return approve(
            self.reviewed_scope,
            self.card,
            self.approved_scope,
            self.manifest,
            history=self.evidence if verified else None,
            **options,
        )

    def assert_nothing_written(self) -> None:
        self.assertFalse(self.approved_scope.exists())
        self.assertFalse(self.manifest.exists())

    def test_history_evidence_carries_edition_dates_description_unit_source_and_hash(self) -> None:
        evidence = self.history()
        self.assertTrue(evidence["continuity"]["ok"])
        self.assertEqual(len(evidence["matches"]), 3)
        required = {
            "edition",
            "effective_from",
            "effective_to",
            "description",
            "quantity_unit",
            "source_url",
            "source_file",
            "source_sha256",
            "record_sha256",
        }
        self.assertTrue(all(required <= set(row) for row in evidence["matches"]))
        self.assertEqual(
            hts_history.verify_history_evidence(self.evidence, expected_scope_sha256=evidence["scope_sha256"])[
                "evidence_sha256"
            ],
            evidence["evidence_sha256"],
        )

    def test_card_has_no_approval_effect_and_is_never_overwritten(self) -> None:
        card = self.card_for(verified=False)
        self.assertEqual(card["approval_effect"], scope_module.CARD_EFFECT)
        self.assertEqual(card["continuity"]["status"], "unverified")
        self.assertIn("连续性未核实", scope_module.format_approval_card(card))
        self.assertNotIn("approved_at_utc", json.dumps(card))
        with self.assertRaises(FileExistsError):
            scope_module.build_approval_card(self.reviewed_scope, output_path=self.card)

    def test_card_shows_the_second_unit_as_kept_for_later(self) -> None:
        rows = [{**row, "second_unit": "kg" if row["decision"] == "include" else ""} for row in reviewed(history_rows())]
        with self.reviewed_scope.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[*scope_module.REQUIRED_COLUMNS, *scope_module.OPTIONAL_COLUMNS])
            writer.writeheader()
            writer.writerows(rows)
        card = self.card_for(verified=False)
        self.assertEqual((card["second_unit"], card["second_units"]), ("kg", ["kg"]))
        self.assertIn("second unit   kg on every included line (fetched only by a study", scope_module.format_approval_card(card))
        scope = scope_module.read_unapproved_scope(self.reviewed_scope)
        self.assertEqual((scope.second_unit, scope.second_units), ("kg", ("kg",)))

    def test_typed_confirmation_freezes_the_scope_and_states_its_boundary(self) -> None:
        self.card_for(verified=True)
        record = self.approve()
        self.assertEqual(record["approval"]["method"], scope_module.APPROVAL_METHOD)
        self.assertEqual(record["approval"]["procedural_boundary"], scope_module.APPROVAL_BOUNDARY)
        self.assertIn("not cryptographically authenticated", record["approval"]["not_verified"])
        self.assertEqual(record["approval"]["approval_card_sha256"], scope_module.file_sha256(self.card))
        self.assertEqual(record["approval"]["reviewer"], "test-reviewer")
        self.assertNotIn("user_confirmation", record["approval"])
        approved = scope_module.verify_scope_manifest(self.manifest)
        self.assertEqual(approved.scope_sha256, record["scope_sha256"])
        self.assertTrue(approved.continuity_verified)
        self.assertEqual(approved.approval_method, scope_module.APPROVAL_METHOD)
        with self.approved_scope.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual({row["review_status"] for row in rows}, {"approved"})
        self.assertEqual({row["approved_at_utc"] for row in rows}, {record["approval"]["approved_at_utc"]})

    def test_non_interactive_stdin_is_refused_even_with_the_right_answers(self) -> None:
        """An agent's shell pipes stdin; scope-approve refuses it before asking anything."""

        self.card_for(verified=True)
        prefix = scope_module.build_manifest(self.reviewed_scope, require_approved=False)["scope_sha256"][:8]
        with mock.patch.object(sys, "stdin", io.StringIO(prefix + "\n")):
            with self.assertRaisesRegex(ScopeError, "interactive terminal"):
                self.approve(interactive=None, answers=(prefix,))
        with self.assertRaisesRegex(ScopeError, "interactive terminal"):
            self.approve(interactive=False)
        self.assert_nothing_written()

    def chat_approve(self, words: str | None = "批准", prefix: str | None = "card", *, verified: bool = True) -> dict:
        """Run scope-approve the way an agent does after the user approved the card in chat."""

        if prefix == "card":
            prefix = scope_module.build_manifest(self.reviewed_scope, require_approved=False)["scope_sha256"][:8]

        def no_terminal(_prompt: str) -> str:  # pragma: no cover - reaching this is the failure
            raise AssertionError("chat approval must not ask at a terminal")

        return scope_module.approve_scope(
            self.reviewed_scope,
            approval_card=self.card,
            approved_scope=self.approved_scope,
            manifest_path=self.manifest,
            reviewer="test-reviewer",
            history_evidence=self.evidence if verified else None,
            continuity_unverified=not verified,
            prompt=no_terminal,
            out=lambda _text: None,
            interactive=False,
            chat_confirmation=words,
            confirm_prefix=prefix,
        )

    def test_chat_confirmation_records_the_users_words_and_its_boundary(self) -> None:
        """No terminal needed: the user's words and the card's prefix are the confirmation."""

        self.card_for(verified=False)
        record = self.chat_approve("  批准，就按\n这张卡来 ", verified=False)
        approval = record["approval"]
        self.assertEqual(approval["method"], scope_module.CHAT_APPROVAL_METHOD)
        self.assertEqual(approval["procedural_boundary"], scope_module.CHAT_APPROVAL_BOUNDARY)
        self.assertEqual(approval["user_confirmation"], "批准，就按 这张卡来")
        self.assertIn("agent ran scope-approve", approval["not_verified"])
        self.assertEqual(approval["approval_card_sha256"], scope_module.file_sha256(self.card))
        self.assertTrue(record["hts_history"]["acknowledged_unverified"])
        approved = scope_module.verify_scope_manifest(self.manifest)
        self.assertEqual(approved.approval_method, scope_module.CHAT_APPROVAL_METHOD)
        self.assertFalse(approved.continuity_verified)

    def test_chat_confirmation_needs_the_prefix_the_card_showed(self) -> None:
        self.card_for(verified=True)
        with self.assertRaisesRegex(ScopeError, "--confirm does not match"):
            self.chat_approve(prefix="deadbeef")
        self.assert_nothing_written()

    def test_chat_words_and_prefix_go_together_and_words_cannot_be_blank(self) -> None:
        self.card_for(verified=True)
        for words, prefix, message in (("批准", None, "go together"), (None, "card", "go together"), ("  ", "card", "exact words")):
            with self.subTest(words=words, prefix=prefix):
                with self.assertRaisesRegex(ScopeError, message):
                    self.chat_approve(words, prefix)
        self.assert_nothing_written()

    def test_chat_record_without_the_users_words_or_with_another_boundary_does_not_count(self) -> None:
        self.card_for(verified=True)
        self.chat_approve()
        original = self.manifest.read_text(encoding="utf-8")
        for edit in (
            lambda approval: approval.pop("user_confirmation"),
            lambda approval: approval.update(user_confirmation=" "),
            lambda approval: approval.update(procedural_boundary=scope_module.APPROVAL_BOUNDARY),
        ):
            record = json.loads(original)
            edit(record["approval"])
            self.manifest.write_text(json.dumps(record), encoding="utf-8")
            with self.subTest(approval=record["approval"]):
                with self.assertRaisesRegex(ScopeGateError, "hand-edited approval fields do not count"):
                    scope_module.verify_scope_manifest(self.manifest)

    def test_wrong_or_missing_confirmation_writes_nothing(self) -> None:
        self.card_for(verified=True)
        with self.assertRaisesRegex(ScopeError, "did not match"):
            self.approve(answers=("deadbeef",))

        def end_of_input(_prompt: str) -> str:
            raise EOFError

        with self.assertRaisesRegex(ScopeError, "No confirmation was typed"):
            scope_module.approve_scope(
                self.reviewed_scope,
                approval_card=self.card,
                approved_scope=self.approved_scope,
                manifest_path=self.manifest,
                reviewer="test-reviewer",
                history_evidence=self.evidence,
                prompt=end_of_input,
                out=lambda _text: None,
                interactive=True,
            )
        self.assert_nothing_written()

    def test_unverified_continuity_needs_its_own_acknowledgement_and_stays_visible(self) -> None:
        self.card_for(verified=False)
        summary = scope_module.build_manifest(self.reviewed_scope, require_approved=False)
        with self.assertRaisesRegex(ScopeError, "not acknowledged"):
            self.approve(verified=False, answers=(summary["scope_sha256"][:8], "yes"))
        self.assert_nothing_written()
        record = self.approve(verified=False)
        self.assertEqual(record["hts_history"]["status"], "unverified")
        self.assertTrue(record["hts_history"]["acknowledged_unverified"])
        self.assertFalse(record["hts_history"]["continuity_verified"])
        self.assertFalse(scope_module.verify_scope_manifest(self.manifest).continuity_verified)

    def test_card_and_command_must_show_the_same_continuity(self) -> None:
        self.card_for(verified=False)
        self.history()
        with self.assertRaisesRegex(ScopeError, "different HTS-continuity status"):
            self.approve(verified=True)
        self.assert_nothing_written()

    def test_exactly_one_continuity_choice_is_required(self) -> None:
        self.card_for(verified=True)
        for history, unverified in ((self.evidence, True), (None, False)):
            with self.subTest(history=history, unverified=unverified):
                with self.assertRaisesRegex(ScopeError, "HTS continuity"):
                    scope_module.approve_scope(
                        self.reviewed_scope,
                        approval_card=self.card,
                        approved_scope=self.approved_scope,
                        manifest_path=self.manifest,
                        reviewer="test-reviewer",
                        history_evidence=history,
                        continuity_unverified=unverified,
                        interactive=True,
                    )

    def test_reviewer_name_is_required(self) -> None:
        self.card_for(verified=True)
        with self.assertRaisesRegex(ScopeError, "--reviewer"):
            scope_module.approve_scope(
                self.reviewed_scope,
                approval_card=self.card,
                approved_scope=self.approved_scope,
                manifest_path=self.manifest,
                reviewer="  ",
                history_evidence=self.evidence,
                interactive=True,
            )

    def test_scope_edited_after_its_card_is_refused(self) -> None:
        self.card_for(verified=False)
        rows = reviewed(history_rows())
        rows[0]["rationale"] = "Reworded after the card was shown."
        write_scope_csv(self.reviewed_scope, rows)
        with self.assertRaisesRegex(ScopeError, "missing or changed"):
            self.approve(verified=False)
        self.assert_nothing_written()

    def test_candidate_rows_cannot_get_a_card(self) -> None:
        write_scope_csv(self.reviewed_scope, [make_row(review_status="candidate", reviewer="", approved_at_utc="")])
        with self.assertRaisesRegex(ScopeError, "review_status=reviewed"):
            scope_module.build_approval_card(self.reviewed_scope, output_path=self.card)

    def test_history_gap_is_written_for_review_but_blocks_the_card(self) -> None:
        evidence = self.history(shorten_first=True)
        self.assertFalse(evidence["continuity"]["ok"])
        self.assertIn("uncovered dates", " ".join(evidence["continuity"]["errors"]))
        with self.assertRaisesRegex(ScopeError, "historical continuity"):
            scope_module.build_approval_card(self.reviewed_scope, output_path=self.card, history_evidence=self.evidence)


class FreezeAndVerifyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.scope_csv = write_scope_csv(self.root / "scopes" / "synthetic" / "v1" / "scope.csv", history_rows())
        self.manifest = self.root / "scopes" / "synthetic" / "v1" / "manifest.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_manifest_records_a_relative_source_and_is_never_overwritten(self) -> None:
        record = freeze_scope(self.scope_csv, self.manifest)
        self.assertEqual(record["source_scope"], "scope.csv")
        with self.assertRaises(FileExistsError):
            scope_module.write_manifest(record, self.manifest)

    def test_verified_scope_selects_codes_valid_in_each_year(self) -> None:
        freeze_scope(self.scope_csv, self.manifest)
        approved = scope_module.verify_scope_manifest(self.manifest)
        self.assertEqual(approved.codes_for_year(2021), ("1111110010",))
        self.assertEqual(approved.codes_for_year(2022), ("1111110020", "1111110030"))
        self.assertEqual(
            approved.uncovered(date(2015, 1, 1), date(2026, 12, 31)), [(date(2015, 1, 1), date(2016, 12, 31))]
        )

    def test_editing_the_csv_after_freezing_is_detected(self) -> None:
        freeze_scope(self.scope_csv, self.manifest)
        rows = history_rows()
        rows[1]["hts_code"] = "1111110099"
        write_scope_csv(self.scope_csv, rows)
        with self.assertRaisesRegex(ScopeGateError, "changed after it was frozen"):
            scope_module.verify_scope_manifest(self.manifest)

    def test_manifest_without_approval_is_not_accepted(self) -> None:
        record = scope_module.build_manifest(self.scope_csv, require_approved=False, manifest_path=self.manifest)
        self.manifest.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ScopeGateError, "not an approved freeze"):
            scope_module.verify_scope_manifest(self.manifest)

    def test_hand_edited_approval_fields_do_not_count(self) -> None:
        """Rows typed in as approved, with a manifest written by hand, never open the gate."""

        record = scope_module.build_manifest(self.scope_csv, require_approved=True, manifest_path=self.manifest)
        with self.assertRaisesRegex(ScopeError, "only by scope-approve"):
            scope_module.write_manifest(record, self.manifest)
        self.manifest.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ScopeGateError, "hand-edited approval fields do not count"):
            scope_module.verify_scope_manifest(self.manifest)

    def test_continuity_cannot_be_upgraded_by_editing_the_manifest(self) -> None:
        freeze_scope(self.scope_csv, self.manifest, with_history=False)
        record = json.loads(self.manifest.read_text(encoding="utf-8"))
        record["hts_history"].update(status="verified", continuity_verified=True, acknowledged_unverified=False)
        self.manifest.write_text(json.dumps(record), encoding="utf-8")
        with self.assertRaisesRegex(ScopeGateError, "differs between the approval card"):
            scope_module.verify_scope_manifest(self.manifest)

    def test_scope_validate_cannot_approve(self) -> None:
        argv = ["--scope", str(self.scope_csv), "--manifest", str(self.manifest)]
        code, _, stderr = run_captured(scope_module.main, argv)
        self.assertEqual(code, 2)
        self.assertIn("scope-validate cannot approve", stderr)
        self.assertFalse(self.manifest.exists())


if __name__ == "__main__":
    unittest.main()
