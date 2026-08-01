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


def write_manifest(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "artifact-manifest.json":
            continue
        files.append(
            {
                "path": str(path.relative_to(output_dir)),
                "size": path.stat().st_size,
                "sha256": sha256_file(path),
            }
        )
    root = Path(__file__).resolve().parent
    provenance = {
        "schema_version": "v5-artifact-manifest-2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        **_execution_source(),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "github_repository": os.getenv("GITHUB_REPOSITORY"),
        "issue_number": os.getenv("ISSUE_NUMBER"),
        "config_sha256": sha256_file(root / "config.json"),
        "policy_sha256": sha256_file(root / "team_policy.json"),
        "files": files,
    }
    (output_dir / "artifact-manifest.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
