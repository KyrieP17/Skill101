"""Command-line entry point for the research pipeline.

Scope validation, approval, planning, gated acquisition and ``status`` are
pure Python: they import neither pandas nor matplotlib, so scopes and plans
can be reviewed in any Python 3.11+ interpreter.  Analysis stages import the
scientific stack only when they run.

A study of any HTS product::

    scope-validate --scope scopes/<id>/v<k>/reviewed.csv --approval-card scopes/<id>/v<k>/approval-card.json \
                   [--history-evidence scopes/<id>/v<k>/hts-history.json]
    scope-approve  ...                       # after the user approves the card, in chat or in a terminal
    plan    --study config/studies/<study_id>.toml
    acquire --plan outputs/runs/<study_id>/plan-<hash>/query_plan.json
    all     --study config/studies/<study_id>.toml   # analyze -> charts -> validate -> report
    brief-check --study config/studies/<study_id>.toml # labels, sources, traced numbers, wording, sections
    report-pdf --study config/studies/<study_id>.toml  # brief-check, then brief.md + appendix -> final_report.pdf
    registry-check [--study config/studies/<study_id>.toml]  # timeline rows against config/tariff_registry

The user approves the card either in chat (the agent then records their words) or in a terminal
(the command refuses a non-interactive shell unless --chat-confirmation is given)::

    scope-approve --scope <reviewed.csv> --approval-card <card.json> --reviewer "<name>" \
                  (--history-evidence <hts-history.json> | --continuity-unverified) \
                  --approved-scope scopes/<id>/v<k>/scope.csv --manifest scopes/<id>/v<k>/manifest.json \
                  [--chat-confirmation "<the user's exact words>" --confirm <first 8 of scope_sha256>]

A project can forbid chat approval with ``[approval] allow_chat_confirmation = false`` in research.toml.

A labeled replay of old snapshots (scope not approved, payloads unhashed)::

    all --replay config/replays/<replay_id>.toml

The original research.toml baseline, kept only to reproduce it (writes outputs/)::

    analyze | charts | validate | all --legacy-baseline;  acquire --legacy-baseline;  import-legacy
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from .config import ResearchConfig, load_config
from .dataweb import DataWebError
from .hts_history import HistoryEvidenceError, add_arguments as add_history_arguments, run as run_history
from .plan import acquire_plan, cached_jobs, codes_by_period, plan_study
from .scope import (
    ScopeError,
    ScopeGateError,
    add_arguments as add_scope_arguments,
    approve_scope,
    run as run_scope_validation,
)
from .study import StudyError

STAGES = ("analyze", "charts", "validate", "report", "all")
DOMAIN_ERRORS = (ScopeError, ScopeGateError, StudyError, HistoryEvidenceError, DataWebError, OSError)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="usitc-pipeline",
        description="Reproducible USITC DataWeb tariff and sourcing research pipeline",
    )
    parser.add_argument("--config", type=Path, default=None, help="Path to config/research.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_scope_arguments(
        subparsers.add_parser("scope-validate", help="Validate a scope CSV and optionally write its approval card")
    )
    add_history_arguments(
        subparsers.add_parser("hts-history", help="Build hash-backed historical HTS continuity evidence")
    )
    approve = subparsers.add_parser(
        "scope-approve",
        help="Freeze a scope once the user approves its card: in their own terminal, or in chat (--chat-confirmation)",
    )
    approve.add_argument("--scope", type=Path, required=True, help="Reviewed scope CSV")
    approve.add_argument("--approval-card", type=Path, required=True, help="Card written by scope-validate")
    approve.add_argument("--reviewer", required=True, help="Name recorded as the approving reviewer")
    continuity = approve.add_mutually_exclusive_group(required=True)
    continuity.add_argument("--history-evidence", type=Path, help="hts-history JSON proving HTS continuity")
    continuity.add_argument(
        "--continuity-unverified",
        action="store_true",
        help="Approve with HTS continuity explicitly marked as not verified (shown in every report)",
    )
    approve.add_argument("--approved-scope", type=Path, required=True, help="New immutable approved scope CSV")
    approve.add_argument("--manifest", type=Path, required=True, help="New immutable scope manifest JSON")
    approve.add_argument(
        "--chat-confirmation",
        help="The user's exact words approving the card in chat; the agent runs the command and they are recorded",
    )
    approve.add_argument("--confirm", help="With --chat-confirmation: the first 8 characters of scope_sha256 the card showed")

    plan_parser = subparsers.add_parser("plan", help="Build a DataWeb query plan from an approved scope and a study")
    plan_parser.add_argument("--study", type=Path, required=True, help="Study TOML, e.g. config/studies/<id>.toml")

    acquire = subparsers.add_parser("acquire", help="Download or reuse DataWeb snapshots for a query plan")
    source = acquire.add_mutually_exclusive_group(required=True)
    source.add_argument("--plan", type=Path, help="query_plan.json written by `plan`")
    source.add_argument("--legacy-baseline", action="store_true", help="Re-acquire the research.toml baseline")
    acquire.add_argument("--refresh", action="store_true", help="Fetch new immutable snapshots")

    import_parser = subparsers.add_parser("import-legacy", help="Copy reusable snapshots from the earlier project")
    import_parser.add_argument("--source", type=Path, required=True, help="Legacy Columbia 26 Fall project root")

    subparsers.add_parser("status", help="Show scopes, approvals, studies and runs as they are now")

    for command, help_text in (
        ("analyze", "Normalize, calculate and validate one study"),
        ("charts", "Render one chart per analysis, each with its chart-data CSV"),
        ("validate", "Re-read serialized outputs and verify every hash"),
        ("report", "Validate, then write report.md and key_points.md from findings.json"),
        ("all", "analyze -> charts -> validate -> report"),
    ):
        stage = subparsers.add_parser(command, help=help_text)
        target = stage.add_mutually_exclusive_group(required=True)
        target.add_argument("--study", type=Path, help="Study TOML (needs an approved scope and a completed acquire)")
        target.add_argument("--replay", type=Path, help="Replay TOML for labeled legacy snapshots")
        if command != "report":
            target.add_argument(
                "--legacy-baseline", action="store_true", help="Reproduce the original research.toml baseline"
            )

    check = subparsers.add_parser(
        "brief-check",
        help="Check brief.md: evidence labels, cited sources, every number traced to them, wording, section order",
    )
    check_target = check.add_mutually_exclusive_group(required=True)
    check_target.add_argument("--study", type=Path, help="Study TOML whose analysis directory holds brief.md")
    check_target.add_argument("--replay", type=Path, help="Replay TOML whose analysis directory holds brief.md")

    registry = subparsers.add_parser(
        "registry-check",
        help="Validate config/tariff_registry; with --study, compare the study's timeline rows with it (read-only)",
    )
    registry.add_argument("--study", type=Path, default=None, help="Study TOML whose timeline and scope codes to check")

    final = subparsers.add_parser(
        "report-pdf",
        help="Validate and brief-check, then render the agent-written brief.md plus an appendix into final_report.html and final_report.pdf",
    )
    final_target = final.add_mutually_exclusive_group(required=True)
    final_target.add_argument("--study", type=Path, help="Study TOML whose analysis directory holds brief.md")
    final_target.add_argument("--replay", type=Path, help="Replay TOML whose analysis directory holds brief.md")
    final.add_argument("--lang", choices=("zh", "en"), default=None, help="Appendix language (default: follow brief.md)")
    final.add_argument("--browser", type=Path, default=None, help="Edge or Chrome executable that prints the PDF")
    return parser


def _run_final_report(args: argparse.Namespace, config: ResearchConfig) -> int:
    from . import study_pipeline
    from .analysis import ValidationError
    from .final_report import FinalReportError

    try:
        result = study_pipeline.final_report_study(
            config, args.study, replay=args.replay, language=args.lang, browser=args.browser
        )
    except (*DOMAIN_ERRORS, ValidationError, FinalReportError) as exc:
        return _fail(str(exc))
    _emit(result)
    return 0


def _run_brief_check(args: argparse.Namespace, config: ResearchConfig) -> int:
    """Print the full check; exit 2 when the brief has errors, so report-pdf would refuse it."""

    from . import study_pipeline
    from .analysis import ValidationError
    from .brief_check import BriefCheckError

    try:
        result = study_pipeline.brief_check_study(config, args.study, replay=args.replay)
    except (*DOMAIN_ERRORS, ValidationError, BriefCheckError) as exc:
        return _fail(str(exc))
    _emit(result)
    return 0 if result["status"] == "pass" else 2


def _run_registry_check(args: argparse.Namespace, config: ResearchConfig) -> int:
    """Print the registry summary, or the study's timeline against it; never edits a file."""

    from .plan import load_project_study, timeline_records
    from .registry import RegistryError, check_timeline, load_registry, summary
    from .scope import verify_scope_manifest

    root = config.paths.root
    try:
        registry = load_registry(root / "config" / "tariff_registry")
        if args.study is None:
            _emit(summary(registry))
            return 0
        study = load_project_study(config, args.study)
        scope = verify_scope_manifest(root / study.scope_manifest)
        records = timeline_records(config, study)
        rows = [row for layer in ("product", "country") for row in records.get(layer, [])]
        years = [span.start for span in (study.annual, study.monthly) if span is not None]
        since = f"{min(years)}-01-01" if years else None
        result = check_timeline(registry, rows, sorted({line.hts_code for line in scope.included}), since=since)
    except (*DOMAIN_ERRORS, RegistryError) as exc:
        return _fail(str(exc))
    _emit({"study_id": study.study_id, **result})
    return 0


def chat_approval_allowed(config_path: Path | None) -> bool:
    """False only when research.toml says ``[approval] allow_chat_confirmation = false``."""

    import tomllib

    path = Path(config_path) if config_path else Path(__file__).resolve().parents[2] / "config" / "research.toml"
    if not path.is_file():
        return True
    with path.open("rb") as handle:
        value = (tomllib.load(handle).get("approval") or {}).get("allow_chat_confirmation", True)
    if not isinstance(value, bool):
        raise ScopeError("[approval] allow_chat_confirmation must be true or false")
    return value


def _emit(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 2


def _plan(config: ResearchConfig, study_path: Path) -> int:
    plan, path = plan_study(config, study_path)
    cached = cached_jobs(config, plan)
    try:
        shown_path = path.relative_to(config.paths.root).as_posix()
    except ValueError:
        shown_path = str(path)
    _emit(
        {
            "plan_path": str(path),
            "plan_sha256": plan["plan_sha256"],
            "scope": {
                key: plan["scope"][key]
                for key in ("scope_id", "scope_version", "scope_sha256", "hts_level", "continuity_verified")
            },
            "study_id": plan["study"]["study_id"],
            "codes_by_period": codes_by_period(plan),
            "jobs": len(plan["jobs"]),
            "jobs_already_cached": len(cached),
            "needs_dataweb_token": len(cached) < len(plan["jobs"]),
            "next_step": f"python run.py acquire --plan {shown_path}",
        }
    )
    return 0


def _run_study(command: str, config: ResearchConfig, study: Path | None, replay: Path | None) -> int:
    from . import study_pipeline
    from .analysis import ValidationError

    try:
        if command in {"analyze", "all"}:
            result = study_pipeline.analyze_study(config, study, replay=replay)
        if command in {"charts", "all"}:
            charts = study_pipeline.render_study_charts(config, study, replay=replay)
            result = {**result, "charts": charts} if command == "all" else {"charts": charts}
        if command == "validate":
            result = study_pipeline.validate_study_outputs(config, study, replay=replay)
        if command in {"report", "all"}:
            report = study_pipeline.report_study(config, study, replay=replay)
            result = {**result, **report} if command == "all" else report
    except (*DOMAIN_ERRORS, ValidationError) as exc:
        return _fail(str(exc))
    _emit(result)
    return 0


def _run_baseline(args: argparse.Namespace, config: ResearchConfig) -> int:
    """Reproduce the original research.toml workflow; needs pandas and matplotlib."""

    from . import pipeline

    if args.command == "acquire":
        pipeline._print_result(pipeline.acquire(config, refresh=args.refresh, legacy_baseline=True))
    elif args.command == "import-legacy":
        pipeline._print_result(pipeline.import_legacy(config, args.source))
    elif args.command == "analyze":
        pipeline._print_result(pipeline.analyze(config))
    elif args.command == "charts":
        pipeline._print_result({"charts": pipeline.render_charts(config)})
    elif args.command == "validate":
        pipeline._print_result(pipeline.validate_outputs(config))
    elif args.command == "all":
        # Like every other ``all``, this never downloads, token or not; the
        # baseline is refreshed only by the explicit ``acquire --legacy-baseline``.
        if not pipeline._current_raw_json(config):
            return _fail("No baseline snapshots found; run `acquire --legacy-baseline` (needs DATAWEB_TOKEN) first")
        result = pipeline.analyze(config)
        result["charts"] = pipeline.render_charts(config)
        result["final_validation"] = pipeline.validate_outputs(config)
        pipeline._print_result(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "scope-validate":
        return run_scope_validation(
            args.scope,
            require_approved=args.require_approved,
            manifest=args.manifest,
            approval_card=args.approval_card,
            history_evidence=args.history_evidence,
        )
    if args.command == "hts-history":
        return run_history(args.scope, args.catalog, args.output)
    if args.command == "scope-approve":
        try:
            if args.chat_confirmation is not None and not chat_approval_allowed(args.config):
                return _fail(
                    "This project requires terminal approval ([approval] allow_chat_confirmation = false in "
                    "config/research.toml): the reviewer runs scope-approve in their own terminal"
                )
            _emit(
                approve_scope(
                    args.scope,
                    approval_card=args.approval_card,
                    approved_scope=args.approved_scope,
                    manifest_path=args.manifest,
                    reviewer=args.reviewer,
                    history_evidence=args.history_evidence,
                    continuity_unverified=args.continuity_unverified,
                    out=lambda text: print(text, file=sys.stderr),
                    chat_confirmation=args.chat_confirmation,
                    confirm_prefix=args.confirm,
                )
            )
            return 0
        except DOMAIN_ERRORS as exc:
            return _fail(str(exc))

    try:
        config = load_config(args.config)
    except (OSError, ValueError) as exc:
        return _fail(f"cannot load project config: {exc}")
    if args.command == "status":
        from .status import project_status

        _emit(project_status(config))
        return 0
    if args.command == "report-pdf":
        return _run_final_report(args, config)
    if args.command == "brief-check":
        return _run_brief_check(args, config)
    if args.command == "registry-check":
        return _run_registry_check(args, config)
    if args.command in STAGES and not getattr(args, "legacy_baseline", False):
        return _run_study(args.command, config, args.study, args.replay)
    try:
        if args.command == "plan":
            return _plan(config, args.study)
        if args.command == "acquire" and args.plan is not None:
            _emit(acquire_plan(config, args.plan, refresh=args.refresh))
            return 0
    except DOMAIN_ERRORS as exc:
        return _fail(str(exc))
    return _run_baseline(args, config)


if __name__ == "__main__":
    raise SystemExit(main())
