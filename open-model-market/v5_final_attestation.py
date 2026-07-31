#!/usr/bin/env python3
"""Create the post-upload attestation from the frozen V5 evidence bundle."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from v5_evidence_bundle import build_final_attestation_record


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

    try:
        attestation = build_final_attestation_record(
            root=Path(args.output_dir),
            primary_artifact_id=args.primary_artifact_id,
            primary_artifact_digest=args.primary_artifact_digest,
            primary_artifact_url=args.primary_artifact_url,
            audit_status=args.audit_status,
            run_id=args.run_id,
            commit_sha=args.commit_sha,
            final_status_file=Path(args.final_status_file),
        )
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
