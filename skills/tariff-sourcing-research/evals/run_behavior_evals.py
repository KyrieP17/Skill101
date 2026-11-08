#!/usr/bin/env python3
"""Validate, run and score the skill's behavior and trigger evals.

::

    python evals/run_behavior_evals.py                                # validate the suite
    python evals/run_behavior_evals.py --trigger --dry-run            # print the trigger prompts; send nothing
    python evals/run_behavior_evals.py --trigger --executor "<cmd>"   # ask a model; score trigger accuracy
    python evals/run_behavior_evals.py --results judged.json          # score judged transcripts

**Trigger eval.** For every case the runner shows a model this skill's name and
description among a few distractor skills, asks which one (if any) it would
load, and compares the answer with ``should_trigger``. The target is
``--threshold`` (default 0.9) correct. ``--executor`` is any shell command
that reads one prompt on stdin and prints the model's reply, for example a
model CLI in print mode. Running it sends the skill description and the case
prompts to that model, so it is opt-in; ``DATAWEB_TOKEN`` is removed from its
environment.

**Behavior eval.** ``--results`` scores externally produced records against
each case's mode and behavior labels::

    [{"id": "case-id", "triggered": true, "mode": "mode-name",
      "behaviors": ["observed behavior label", "..."]}]

Emit behavior labels only after inspecting the actual response and actions;
copying the expected labels without running a candidate is not an evaluation.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any


DEFAULT_CASES = Path(__file__).with_name("behavior_cases.json")
DEFAULT_SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
TRIGGER_THRESHOLD = 0.9
REQUIRED_KEYS = {"id", "prompt", "should_trigger", "expected_mode", "required_behavior", "forbidden_behavior"}
NOT_APPLICABLE = "not_applicable"
# Plausible neighbours the model must not confuse with this skill.
DISTRACTORS = (
    ("dataviz", "Design any chart, plot or dashboard: chart form, colors, labels and accessibility."),
    (
        "deep-research",
        "Research a topic across many web and document sources and write a long narrative report with "
        "citations: markets, industries, trends, policy, literature reviews.",
    ),
    ("pdf", "Read, merge, split, fill or create PDF files."),
    ("xlsx", "Create, read or edit spreadsheet files (.xlsx, .csv): formulas, cleaning, charts in a workbook."),
)


class ContractError(ValueError):
    """Raised when the case or result file violates the evaluation schema."""


def _load_list(path: Path, label: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ContractError(f"{label} must be a JSON list of objects")
    return payload


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    return _load_list(path, "cases")


def validate_cases(
    cases: list[dict[str, Any]],
    *,
    min_total: int = 10,
    max_total: int = 20,
    min_trigger: int = 5,
    min_nontrigger: int = 5,
) -> dict[str, int]:
    """Check the suite: unique ids, labels, both kinds of case, paraphrases that point at a case."""

    errors: list[str] = []
    ids: set[str] = set()
    trigger_count = 0
    for index, case in enumerate(cases):
        prefix = f"case[{index}]"
        missing = REQUIRED_KEYS - set(case)
        if missing:
            errors.append(f"{prefix} missing keys: {sorted(missing)}")
            continue
        case_id = case["id"]
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{prefix}.id must be a non-empty string")
        elif case_id in ids:
            errors.append(f"duplicate case id: {case_id}")
        else:
            ids.add(case_id)
        if not isinstance(case["prompt"], str) or not case["prompt"].strip():
            errors.append(f"{prefix}.prompt must be a non-empty string")
        if not isinstance(case["should_trigger"], bool):
            errors.append(f"{prefix}.should_trigger must be boolean")
        else:
            trigger_count += int(case["should_trigger"])
            if case["should_trigger"] == (case["expected_mode"] == NOT_APPLICABLE):
                errors.append(f"{prefix}: expected_mode {NOT_APPLICABLE!r} is for exactly the non-trigger cases")
        if not isinstance(case["expected_mode"], str) or not case["expected_mode"].strip():
            errors.append(f"{prefix}.expected_mode must be a non-empty string")
        for key in ("required_behavior", "forbidden_behavior"):
            values = case[key]
            if not isinstance(values, list) or not all(isinstance(value, str) and value.strip() for value in values):
                errors.append(f"{prefix}.{key} must be a list of non-empty strings")
    for case in cases:
        original = case.get("variant_of")
        if original is not None and (original not in ids or original == case.get("id")):
            errors.append(f"{case.get('id')}: variant_of must name another case, got {original!r}")
        elif original is not None:
            source = next(item for item in cases if item.get("id") == original)
            if source.get("should_trigger") != case.get("should_trigger"):
                errors.append(f"{case.get('id')}: a paraphrase must keep should_trigger of {original}")

    nontrigger_count = len(cases) - trigger_count
    if not min_total <= len(cases) <= max_total:
        errors.append(f"suite has {len(cases)} cases; expected {min_total}-{max_total}")
    if trigger_count < min_trigger:
        errors.append(f"suite has {trigger_count} trigger cases; requires at least {min_trigger}")
    if nontrigger_count < min_nontrigger:
        errors.append(f"suite has {nontrigger_count} non-trigger cases; requires at least {min_nontrigger}")
    if errors:
        raise ContractError("; ".join(errors))
    return {"total": len(cases), "trigger": trigger_count, "nontrigger": nontrigger_count}


def read_skill(path: Path = DEFAULT_SKILL) -> tuple[str, str]:
    """Return the skill's frontmatter name and description."""

    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\s*\n(.*?)\n---", text, re.S)
    fields = dict(re.findall(r"^(name|description):\s*(.+)$", match.group(1), re.M)) if match else {}
    if not fields.get("name") or not fields.get("description"):
        raise ContractError(f"{path} needs a frontmatter name and description")
    return fields["name"].strip(), fields["description"].strip()


def trigger_prompt(request: str, skills: list[tuple[str, str]]) -> str:
    listing = "\n".join(f"- {name}: {description}" for name, description in sorted(skills))
    return (
        "You are choosing which skill, if any, to load before answering a user. Load a skill only when its "
        "description says it is meant for this kind of request.\n\n"
        f"Available skills:\n{listing}\n\nUser request:\n{request}\n\n"
        'Reply with JSON only: {"skill": "<one skill name, or none>"}'
    )


def parse_choice(reply: str) -> str | None:
    """Return the skill named in the first ``{"skill": ...}`` object of a reply, if any."""

    for match in re.finditer(r"\{[^{}]*\}", reply):
        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and isinstance(data.get("skill"), str):
            return data["skill"].strip()
    return None


def score_triggers(
    cases: list[dict[str, Any]],
    choices: dict[str, str | None],
    skill_name: str,
    *,
    threshold: float = TRIGGER_THRESHOLD,
) -> dict[str, Any]:
    """Score answers; an unreadable answer counts as wrong whatever the case."""

    rows = []
    for case in cases:
        choice = choices.get(case["id"])
        triggered = choice == skill_name
        rows.append(
            {
                "id": case["id"],
                "should_trigger": case["should_trigger"],
                "choice": choice,
                "correct": choice is not None and triggered is case["should_trigger"],
            }
        )
    correct = sum(row["correct"] for row in rows)
    accuracy = correct / len(rows) if rows else 0.0
    return {
        "ok": bool(rows) and accuracy >= threshold,
        "accuracy": round(accuracy, 4),
        "threshold": threshold,
        "correct": correct,
        "total": len(rows),
        "missed": [row["id"] for row in rows if row["should_trigger"] and row["choice"] not in (None, skill_name)],
        "false_triggers": [row["id"] for row in rows if not row["should_trigger"] and row["choice"] == skill_name],
        "unreadable": [row["id"] for row in rows if row["choice"] is None],
        "cases": rows,
    }


def run_trigger_eval(
    cases: list[dict[str, Any]],
    executor: str,
    *,
    skill_md: Path = DEFAULT_SKILL,
    threshold: float = TRIGGER_THRESHOLD,
    timeout: int = 300,
) -> dict[str, Any]:
    """Send each case's trigger prompt to ``executor`` and score the replies."""

    name, description = read_skill(skill_md)
    skills = [(name, description), *DISTRACTORS]
    environment = {key: value for key, value in os.environ.items() if key != "DATAWEB_TOKEN"}
    choices: dict[str, str | None] = {}
    for case in cases:
        try:
            # Bytes, not text mode: on Windows text mode would turn every
            # newline of the prompt into CRLF on its way to the model.
            completed = subprocess.run(
                executor,
                shell=True,
                input=trigger_prompt(case["prompt"], skills).encode("utf-8"),
                capture_output=True,
                env=environment,
                timeout=timeout,
                check=False,
            )
            reply = completed.stdout.decode("utf-8", errors="replace")
            choices[case["id"]] = parse_choice(reply) if completed.returncode == 0 else None
        except subprocess.TimeoutExpired:
            choices[case["id"]] = None
    return score_triggers(cases, choices, name, threshold=threshold)


def evaluate_results(cases: list[dict[str, Any]], results: list[dict[str, Any]]) -> dict[str, Any]:
    result_by_id: dict[str, dict[str, Any]] = {}
    for index, result in enumerate(results):
        case_id = result.get("id")
        if not isinstance(case_id, str) or not case_id:
            raise ContractError(f"result[{index}].id must be a non-empty string")
        if case_id in result_by_id:
            raise ContractError(f"duplicate result id: {case_id}")
        if not isinstance(result.get("triggered"), bool):
            raise ContractError(f"result {case_id} triggered must be boolean")
        if not isinstance(result.get("mode"), str) or not result["mode"].strip():
            raise ContractError(f"result {case_id} mode must be a non-empty string")
        behaviors = result.get("behaviors")
        if not isinstance(behaviors, list) or not all(isinstance(item, str) for item in behaviors):
            raise ContractError(f"result {case_id} behaviors must be a list of strings")
        result_by_id[case_id] = result

    case_ids = {case["id"] for case in cases}
    unknown = sorted(set(result_by_id) - case_ids)
    missing = sorted(case_ids - set(result_by_id))
    if unknown or missing:
        raise ContractError(f"result ids mismatch; missing={missing}, unknown={unknown}")

    failures: list[dict[str, Any]] = []
    for case in cases:
        actual = result_by_id[case["id"]]
        observed = set(actual["behaviors"])
        reasons: list[str] = []
        if actual["triggered"] is not case["should_trigger"]:
            reasons.append(f"triggered={actual['triggered']} expected={case['should_trigger']}")
        if actual["mode"] != case["expected_mode"]:
            reasons.append(f"mode={actual['mode']!r} expected={case['expected_mode']!r}")
        absent = [item for item in case["required_behavior"] if item not in observed]
        present = [item for item in case["forbidden_behavior"] if item in observed]
        if absent:
            reasons.append(f"missing required behaviors: {absent}")
        if present:
            reasons.append(f"observed forbidden behaviors: {present}")
        if reasons:
            failures.append({"id": case["id"], "reasons": reasons})

    return {"ok": not failures, "passed": len(cases) - len(failures), "failed": len(failures), "failures": failures}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate, run or score tariff-sourcing evals")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--results", type=Path, help="Externally judged behavior results to score")
    parser.add_argument("--trigger", action="store_true", help="Run the trigger eval (needs --executor or --dry-run)")
    parser.add_argument("--executor", help="Shell command that reads a prompt on stdin and prints the model's reply")
    parser.add_argument("--dry-run", action="store_true", help="With --trigger: print the prompts, send nothing")
    parser.add_argument("--threshold", type=float, default=TRIGGER_THRESHOLD)
    parser.add_argument("--skill", type=Path, default=DEFAULT_SKILL, help="SKILL.md whose description is tested")
    parser.add_argument("--timeout", type=int, default=300, help="Seconds per executor call")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cases = load_cases(args.cases)
        report: dict[str, Any] = {"suite_valid": True, **validate_cases(cases)}
        if args.trigger and args.dry_run:
            name, description = read_skill(args.skill)
            skills = [(name, description), *DISTRACTORS]
            prompts = [{"id": case["id"], "prompt": trigger_prompt(case["prompt"], skills)} for case in cases]
            print(json.dumps(prompts, ensure_ascii=False, indent=2))
            return 0
        if args.trigger:
            if not args.executor:
                raise ContractError("--trigger needs --executor (or --dry-run to only print the prompts)")
            report["trigger"] = run_trigger_eval(
                cases, args.executor, skill_md=args.skill, threshold=args.threshold, timeout=args.timeout
            )
        if args.results:
            report["evaluation"] = evaluate_results(cases, _load_list(args.results, "results"))
    except ContractError as exc:
        print(json.dumps({"suite_valid": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    failed = (args.trigger and not report["trigger"]["ok"]) or (args.results and not report["evaluation"]["ok"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
