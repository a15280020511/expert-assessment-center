#!/usr/bin/env python3
"""Create the post-upload attestation from the frozen V5 evidence bundle."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Mapping

from v5_evidence_bundle import build_final_attestation_record

_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def checked_out_commit_sha() -> str:
    """Return the exact source commit executed in the current checkout."""
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("cannot resolve checked-out execution commit") from exc
    commit_sha = completed.stdout.strip().casefold()
    if not _COMMIT_SHA_RE.fullmatch(commit_sha):
        raise RuntimeError("checked-out execution commit is not a full Git SHA")
    return commit_sha


def _authoritative_final_status(root: Path, workflow_status: str) -> str:
    path = root / "final-status.json"
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        raise RuntimeError("authoritative final-status.json is missing") from exc
    if not isinstance(value, Mapping):
        raise RuntimeError("authoritative final status is not an object")
    authoritative = str(value.get("status") or "FAIL").upper()
    if authoritative not in {"PASS", "DEGRADED", "FAIL"}:
        raise RuntimeError("authoritative final status is invalid")
    observed = str(workflow_status or "FAIL").upper()
    allowed_control = (
        {"PASS"}
        if authoritative == "PASS"
        else {"PASS", "DEGRADED"}
        if authoritative == "DEGRADED"
        else {"FAIL"}
    )
    if observed not in allowed_control:
        raise RuntimeError(
            "workflow control status conflicts with authoritative final status"
        )
    return authoritative


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
    parser.add_argument("--independent-revalidation-file", default="")
    parser.add_argument("--output", default="final-attestation.json")
    args = parser.parse_args()

    try:
        root = Path(args.output_dir)
        execution_commit_sha = checked_out_commit_sha()
        authoritative_status = _authoritative_final_status(
            root,
            args.audit_status,
        )
        attestation = build_final_attestation_record(
            root=root,
            primary_artifact_id=args.primary_artifact_id,
            primary_artifact_digest=args.primary_artifact_digest,
            primary_artifact_url=args.primary_artifact_url,
            audit_status=authoritative_status,
            run_id=args.run_id,
            commit_sha=execution_commit_sha,
            final_status_file=Path(args.final_status_file),
            independent_revalidation_file=(
                Path(args.independent_revalidation_file)
                if args.independent_revalidation_file
                else None
            ),
        )
        attestation["commit_sha_source"] = "checked-out-git-head"
        attestation["event_context_commit_sha"] = str(args.commit_sha).strip().casefold()
        attestation["event_context_commit_matched_checkout"] = (
            attestation["event_context_commit_sha"] == execution_commit_sha
        )
        attestation["workflow_control_status"] = str(args.audit_status).upper()
        attestation["authoritative_delivery_status"] = authoritative_status
        attestation["degraded_success_is_not_full_success"] = True
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
    Path(args.output).write_text(
        json.dumps(attestation, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(attestation, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
