"""Create a deterministic SHA-256 manifest for execution artifacts."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    files = []
    for path in sorted(output_dir.rglob("*")):
        if not path.is_file() or path.name == "artifact-manifest.json":
            continue
        files.append({"path": str(path.relative_to(output_dir)), "size": path.stat().st_size, "sha256": sha256_file(path)})
    root = Path(__file__).resolve().parent
    provenance = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "github_sha": os.getenv("GITHUB_SHA"),
        "github_run_id": os.getenv("GITHUB_RUN_ID"),
        "github_run_attempt": os.getenv("GITHUB_RUN_ATTEMPT"),
        "github_repository": os.getenv("GITHUB_REPOSITORY"),
        "issue_number": os.getenv("ISSUE_NUMBER"),
        "config_sha256": sha256_file(root / "config.json"),
        "policy_sha256": sha256_file(root / "team_policy.json"),
        "files": files,
    }
    (output_dir / "artifact-manifest.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2), encoding="utf-8")
