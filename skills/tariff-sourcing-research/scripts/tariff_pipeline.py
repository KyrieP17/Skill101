#!/usr/bin/env python3
"""Thin wrapper around the tariff research pipeline's own CLI.

It finds the project and its interpreter, runs ``run.py <command>`` inside the
project root, and keeps ``DATAWEB_TOKEN`` away from every command except
``acquire``.  It holds no research rules; the pipeline enforces them::

    python scripts/tariff_pipeline.py status
    python scripts/tariff_pipeline.py --dry-run plan --study config/studies/<study_id>.toml

Project: ``--project-root``, else ``TARIFF_PIPELINE_ROOT``, else ``project_root``
in the skill's ``config.json``.  Interpreter: ``--python``, else ``python`` in
``config.json`` (relative to the project), else the project's ``.venv``, else
this interpreter.  With no project configured it stops and says what to ask.

``scope-approve`` is passed through unchanged.  The agent runs it only with
``--chat-confirmation "<the user's words>" --confirm <prefix>`` after the user
approved the card in chat; without those flags the pipeline refuses a stdin
that is a pipe, a file or NUL, which is how an agent's shell runs it.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from typing import Any


SKILL_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = SKILL_ROOT / "config.json"
PIPELINE_COMMANDS = (
    "status",
    "scope-validate",
    "hts-history",
    "scope-approve",
    "plan",
    "acquire",
    "analyze",
    "charts",
    "validate",
    "report",
    "all",
    "brief-check",
    "report-pdf",
    "registry-check",
)
TOKEN_COMMANDS = {"acquire"}


class SetupError(RuntimeError):
    """The skill does not know where the project or its interpreter is."""


def _ask_user(reason: str) -> SetupError:
    return SetupError(
        f"{reason}\nAsk the user for the pipeline project folder (the one with run.py) and, if it has no .venv, "
        f"the Python interpreter to use. Then write {CONFIG_PATH} as "
        '{"project_root": "<folder>", "python": "<interpreter, optional, may be relative to the folder>"}.'
    )


def load_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    """Read the skill's per-machine settings; a missing file means none."""

    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise _ask_user(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise _ask_user(f"{path} must hold a JSON object")
    return data


def find_project_root(explicit: str | None = None, config: dict[str, Any] | None = None) -> Path:
    """Return the first configured project; a wrong path is an error, not a fallback."""

    config = load_config() if config is None else config
    for source, value in (
        ("--project-root", explicit),
        ("TARIFF_PIPELINE_ROOT", os.environ.get("TARIFF_PIPELINE_ROOT")),
        (f"project_root in {CONFIG_PATH.name}", config.get("project_root")),
    ):
        if not value:
            continue
        root = Path(str(value)).expanduser().resolve()
        if (root / "run.py").is_file() and (root / "src" / "usitc_pipeline").is_dir():
            return root
        raise _ask_user(f"{source} points to {root}, which has no run.py and src/usitc_pipeline.")
    raise _ask_user("No pipeline project is configured.")


def project_python(project: Path, explicit: str | None = None, config: dict[str, Any] | None = None) -> Path:
    """Return the interpreter for the project CLI."""

    # absolute(), not resolve(): resolving a POSIX venv's python symlink would
    # silently leave the virtual environment.
    if explicit:
        return Path(explicit).expanduser().absolute()
    config = load_config() if config is None else config
    configured = config.get("python")
    if configured:
        path = Path(str(configured)).expanduser()
        path = path if path.is_absolute() else project / path
        if not path.is_file():
            raise _ask_user(f"python in {CONFIG_PATH.name} points to {path}, which does not exist.")
        return path.absolute()
    for candidate in (project / ".venv" / "Scripts" / "python.exe", project / ".venv" / "bin" / "python"):
        if candidate.is_file():
            return candidate.absolute()
    return Path(sys.executable).absolute()


def load_pipeline_module(name: str, project_root: str | None = None) -> ModuleType:
    """Import ``usitc_pipeline.<name>`` from the configured project's ``src``."""

    source = (find_project_root(project_root) / "src").resolve()
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    module = importlib.import_module(f"usitc_pipeline.{name}")
    location = Path(module.__file__ or "").resolve()
    if source not in location.parents:
        raise ImportError(f"usitc_pipeline.{name} was imported from {location}, not from {source}")
    return module


def run(args: argparse.Namespace) -> int:
    config = load_config()
    project = find_project_root(args.project_root, config)
    python = project_python(project, args.python, config)
    if args.command == "test":
        command = [str(python), "-m", "unittest", "discover", "-s", str(project / "tests")]
    else:
        command = [str(python), str(project / "run.py"), args.command, *args.pipeline_args]
    token_forwarded = args.command in TOKEN_COMMANDS and bool(os.environ.get("DATAWEB_TOKEN"))
    if args.dry_run:
        preview = {"project_root": str(project), "python": str(python), "command": command}
        print(json.dumps({**preview, "dataweb_token_forwarded": token_forwarded}, ensure_ascii=False, indent=2))
        return 0
    environment = os.environ.copy()
    if args.command not in TOKEN_COMMANDS:
        environment.pop("DATAWEB_TOKEN", None)
    return int(subprocess.run(command, cwd=project, env=environment, check=False).returncode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the tariff research pipeline's CLI from the skill")
    parser.add_argument("--project-root", help="Pipeline project folder (default: TARIFF_PIPELINE_ROOT, then config.json)")
    parser.add_argument(
        "--python", help="Interpreter for the project (default: config.json, then the project .venv, then this one)"
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the resolved command without running it")
    parser.add_argument("command", choices=[*PIPELINE_COMMANDS, "test"], help="Project CLI command, or test")
    parser.add_argument("pipeline_args", nargs=argparse.REMAINDER, help="Passed unchanged to the project CLI")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        return run(build_parser().parse_args(argv))
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
