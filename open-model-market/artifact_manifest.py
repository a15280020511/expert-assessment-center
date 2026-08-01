"""Create a deterministic SHA-256 manifest for execution artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checked_out_commit_sha() -> tuple[str | None, str]:
    """Resolve the executed checkout, never treating event SHA as authoritative."""
    explicit = os.getenv("V5_EXECUTION_COMMIT_SHA", "").strip().casefold()
    if _COMMIT_RE.fullmatch(explicit):
        return explicit, "V5_EXECUTION_COMMIT_SHA"
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
        value = completed.stdout.strip().casefold()
        if _COMMIT_RE.fullmatch(value):
            return value, "checked-out-git-head"
    except (OSError, subprocess.CalledProcessError):
        pass
    fallback = os.getenv("GITHUB_SHA", "").strip().casefold()
    if _COMMIT_RE.fullmatch(fallback):
        return fallback, "event-context-fallback-nonproduction"
    return None, "unavailable"


def build_manifest(output_dir: Path) -> dict[str, Any]:
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
    execution_sha, source = checked_out_commit_sha()
    event_sha = os.getenv("GITHUB_SHA", "").strip().casefold() or None
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "github_sha": execution_sha,
        "execution_commit_sha": execution_sha,
        "execution_commit_sha_source": source,
        "event_context_commit_sha": event_sha,
        "event_context_commit_matched_checkout": bool(
            execution_sha and event_sha and execution_sha == event_sha
        ),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "github_repository": os.getenv("GITHUB_REPOSITORY"),
        "issue_number": os.getenv("ISSUE_NUMBER"),
        "config_sha256": sha256_file(root / "config.json"),
        "policy_sha256": sha256_file(root / "team_policy.json"),
        "files": files,
    }


def write_manifest(output_dir: Path) -> dict[str, Any]:
    provenance = build_manifest(output_dir)
    (output_dir / "artifact-manifest.json").write_text(
        json.dumps(provenance, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return provenance
