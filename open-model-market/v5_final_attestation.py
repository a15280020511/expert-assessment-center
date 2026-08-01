#!/usr/bin/env python3
"""Create the post-upload attestation from the frozen V5 evidence bundle."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

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
    parser.add_argument("--publication-outcome", required=True)
    parser.add_argument("--output", default="final-attestation.json")
    args = parser.parse_args()

    try:
        execution_commit_sha = checked_out_commit_sha()
        attestation = build_final_attestation_record(
            root=Path(args.output_dir),
            primary_artifact_id=args.primary_artifact_id,
            primary_artifact_digest=args.primary_artifact_digest,
            primary_artifact_url=args.primary_artifact_url,
            audit_status=args.audit_status,
            run_id=args.run_id,
            commit_sha=execution_commit_sha,
            final_status_file=Path(args.final_status_file),
        )
        event_commit_sha = str(args.commit_sha).strip().casefold()
        if not _COMMIT_SHA_RE.fullmatch(event_commit_sha):
            raise RuntimeError("event context commit is not a full Git SHA")
        if event_commit_sha != execution_commit_sha:
            raise RuntimeError(
                "event control-plane commit differs from checked-out production commit"
            )
        if str(args.publication_outcome) != "success":
            raise RuntimeError("audited report publication did not succeed")
        attestation["commit_sha_source"] = "checked-out-git-head"
        attestation["event_context_commit_sha"] = event_commit_sha
        attestation["event_context_commit_matched_checkout"] = True
        attestation["audited_publication_outcome"] = "success"
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
