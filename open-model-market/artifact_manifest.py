"""Create a deterministic SHA-256 manifest for execution artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_value(*args: str) -> str | None:
    try:
        return subprocess.check_output(
            ["git", *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def _execution_source() -> dict[str, object]:
    execution_sha = _git_value("rev-parse", "HEAD")
    event_sha = os.getenv("GITHUB_SHA") or None
    if os.getenv("GITHUB_ACTIONS") == "true" and not execution_sha:
        raise RuntimeError("cannot resolve authoritative checked-out execution SHA")
    dirty = bool(_git_value("status", "--porcelain"))
    return {
        "execution_sha": execution_sha,
        "event_sha": event_sha,
        "event_sha_matches_execution_sha": bool(
            execution_sha and event_sha and execution_sha == event_sha
        ),
        "execution_sha_policy": "checked-out-git-head-is-authoritative",
        "working_tree_dirty": dirty,
        "github_ref": os.getenv("GITHUB_REF"),
        "github_ref_name": os.getenv("GITHUB_REF_NAME"),
    }


def _architecture_hashes(root: Path) -> dict[str, str]:
    paths = {
        "config": "config.json",
        "governance_selection_validator": "v5_governance_selection.py",
        "governance_selected_pipeline": "v5_price_ranked_pipeline.py",
        "governance_selected_evidence": "v5_price_ranked_evidence.py",
        "proposal_validator": "v5_proposal_materializer.py",
        "execution_graph_validator": "execution_graph_validator.py",
        "execution_auditor": "v5_price_ranked_execution_auditor.py",
    }
    return {name: sha256_file(root / filename) for name, filename in paths.items()}


def _manifest_rows(output_dir: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in sorted(output_dir.rglob("*")):
        if path.is_file() and path.name != "artifact-manifest.json":
            rows.append(
                {
                    "path": str(path.relative_to(output_dir)),
                    "size": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    return rows


def write_manifest(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent
    provenance = {
        "schema_version": "v5-artifact-manifest-5",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **_execution_source(),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "github_repository": os.getenv("GITHUB_REPOSITORY"),
        "issue_number": os.getenv("ISSUE_NUMBER"),
        "architecture_sha256": _architecture_hashes(root),
        "active_selection_authority": "decision-system-governance",
        "selection_occurs_in_this_repository": False,
        "catalog_fetch_occurs_in_this_repository": False,
        "local_selection_fallback_allowed": False,
        "active_orchestration_library": "networkx",
        "claude_mechanism_enabled": False,
        "governance_model_calls": 0,
        "obsolete_local_selector_present": False,
        "files": _manifest_rows(output_dir),
    }
    (output_dir / "artifact-manifest.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
