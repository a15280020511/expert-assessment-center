#!/usr/bin/env python3
"""Create a post-upload attestation that closes the V5 evidence chain."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--primary-artifact-id", required=True)
    parser.add_argument("--primary-artifact-digest", required=True)
    parser.add_argument("--primary-artifact-url", default="")
    parser.add_argument("--audit-status", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--final-status-file", required=True)
    parser.add_argument("--output", default="final-attestation.json")
    args = parser.parse_args()

    root = Path(args.output_dir)
    report = root / "expert-team-report.md"
    manifest = root / "artifact-manifest.json"
    final_status = Path(args.final_status_file)
    if not report.is_file() or not manifest.is_file() or not final_status.is_file():
        raise SystemExit("report, manifest, and final status must exist before attestation")
    if not args.primary_artifact_id or not args.primary_artifact_digest:
        raise SystemExit("primary artifact identity is required")

    diagnosis = _load(root / "execution-diagnosis.json", {})
    attestation = {
        "version": 1,
        "runtime": "v5-r8-production",
        "run_id": int(args.run_id),
        "commit_sha": args.commit_sha,
        "primary_artifact": {
            "artifact_id": int(args.primary_artifact_id),
            "artifact_digest": args.primary_artifact_digest,
            "artifact_url": args.primary_artifact_url,
        },
        "audit_status": args.audit_status,
        "diagnosis_status": diagnosis.get("status") if isinstance(diagnosis, dict) else None,
        "report_sha256": _sha256(report),
        "manifest_sha256": _sha256(manifest),
        "final_status_sha256": _sha256(final_status),
        "external_tools_allowed": False,
        "alternate_runtime_fallback": False,
        "evidence_chain": "primary-artifact -> final-status -> final-attestation-artifact",
        "generator": "v5_final_attestation.py",
        "github_repository": os.getenv("GITHUB_REPOSITORY", ""),
    }
    Path(args.output).write_text(
        json.dumps(attestation, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(attestation, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
