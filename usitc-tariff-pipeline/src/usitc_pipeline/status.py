"""What the project can and cannot do right now, computed from its files.

Pure Python (no pandas), so ``status`` runs in any interpreter.  The Skill
shows this output instead of keeping a "current limits" list that goes stale.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .config import ResearchConfig
from .plan import build_query_plan, load_project_study, plan_directory
from .scope import ScopeError, ScopeGateError, _parse_scope, verify_scope_manifest
from .study import StudyError


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _scope_versions(root: Path) -> list[dict[str, Any]]:
    result = []
    for directory in sorted(path for path in (root / "scopes").glob("*/v*") if path.is_dir()):
        entry: dict[str, Any] = {"scope": _relative(directory, root)}
        manifest = directory / "manifest.json"
        if manifest.is_file():
            try:
                scope = verify_scope_manifest(manifest)
                entry.update(approval="approved", continuity_verified=scope.continuity_verified)
            except ScopeGateError as exc:
                entry.update(approval="manifest rejected", reason=str(exc))
        else:
            statuses, candidates = {}, []
            for csv_path in sorted(directory.glob("*.csv")):
                if csv_path.name.startswith("candidates"):
                    candidates.append(csv_path.name)  # resolver output, not a scope file
                    continue
                try:
                    statuses[csv_path.name] = sorted({row["review_status"] for row in _parse_scope(csv_path.read_bytes())})
                except ScopeError as exc:
                    statuses[csv_path.name] = f"invalid: {exc}"
            entry.update(
                approval="not approved",
                review_status=statuses,
                approval_card=(directory / "approval-card.json").is_file(),
                **({"candidate_files": candidates} if candidates else {}),
            )
        result.append(entry)
    return result


def _studies(config: ResearchConfig) -> list[dict[str, Any]]:
    root = config.paths.root
    result = []
    for path in sorted((root / "config" / "studies").glob("*.toml")):
        entry: dict[str, Any] = {"study": _relative(path, root)}
        try:
            study = load_project_study(config, path)
            entry.update(study_id=study.study_id, scope_manifest=study.scope_manifest)
            scope = verify_scope_manifest(root / study.scope_manifest)
            run_dir = plan_directory(
                build_query_plan(scope, study, max_records=config.dataweb.max_records), outputs_root=config.paths.outputs
            )
        except (StudyError, ScopeError, ScopeGateError) as exc:
            entry.update(gate="closed", reason=str(exc))
            result.append(entry)
            continue
        acquisition = run_dir / "acquisition.json"
        entry.update(
            gate="open",
            continuity_verified=scope.continuity_verified,
            plan=_relative(run_dir / "query_plan.json", root) if (run_dir / "query_plan.json").is_file() else None,
            acquired=acquisition.is_file(),
            data_through=json.loads(acquisition.read_text(encoding="utf-8")).get("data_through")
            if acquisition.is_file()
            else None,
            analyses=[_relative(item, root) for item in sorted(run_dir.glob("analysis-*")) if item.is_dir()],
        )
        result.append(entry)
    return result


def _unhashed_snapshots(root: Path) -> int:
    count = 0
    for path in (root / "data" / "raw" / "dataweb").glob("*.meta.json"):
        try:
            count += json.loads(path.read_text(encoding="utf-8")).get("payload_sha256") is None
        except (OSError, json.JSONDecodeError):
            count += 1
    return count


def project_status(config: ResearchConfig) -> dict[str, Any]:
    root = config.paths.root
    token = bool(os.environ.get("DATAWEB_TOKEN"))
    scopes, studies = _scope_versions(root), _studies(config)
    replays = [_relative(path, root) for path in sorted((root / "config" / "replays").glob("*.toml"))]
    unhashed = _unhashed_snapshots(root)
    limits = [f"{item['study']}: cannot plan ({item['reason']})" for item in studies if item.get("gate") == "closed"]
    limits += [
        f"{item['study']}: approved with HTS continuity NOT verified; reports say so"
        for item in studies
        if item.get("gate") == "open" and not item.get("continuity_verified")
    ]
    if not token:
        limits.append("DATAWEB_TOKEN is not set: plan works, acquire --plan does not")
    if unhashed:
        limits.append(
            f"{unhashed} legacy snapshots in data/raw/dataweb have no payload hash: they can feed only a labeled "
            "replay (outputs/replays/), never a gated study"
        )
    return {
        "project_root": str(root),
        "dataweb_token_set": token,
        "scopes": scopes,
        "studies": studies,
        "replays": replays,
        "replay_outputs": [
            _relative(path, root) for path in sorted((config.paths.outputs / "replays").glob("*/replay-*/analysis-*"))
        ],
        "limits": limits,
    }
