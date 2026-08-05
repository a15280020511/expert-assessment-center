#!/usr/bin/env python3
"""Create one post-upload V6 final attestation bound to run, SHA, and digest."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    return dict(value) if isinstance(value, Mapping) else {}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--primary-artifact-id", required=True)
    parser.add_argument("--primary-artifact-digest", required=True)
    parser.add_argument("--primary-artifact-url", default="")
    parser.add_argument("--audit-status", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    parser.add_argument("--final-status-file", required=True)
    parser.add_argument("--independent-revalidation-file", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.output_dir)
    final_status = _load(root / "final-status.json")
    independent_path = Path(args.independent_revalidation_file)
    independent = _load(independent_path)
    result = _load(root / "expert-team-result.json")
    status_text = Path(args.final_status_file).read_text("utf-8")
    checks = {
        "audit_status_pass": args.audit_status == "PASS",
        "final_status_pass": final_status.get("status") == "PASS",
        "independent_revalidation_pass": independent.get("status") == "PASS",
        "artifact_identity_present": bool(args.primary_artifact_id and args.primary_artifact_digest),
        "run_id_matches": str(independent.get("expected_run_id") or "") == str(args.run_id),
        "commit_sha_matches": str(independent.get("expected_execution_sha") or "") == args.commit_sha,
        "v6_runtime": result.get("runtime_version") == "v6-governed-roster-networkx-1",
        "claude_disabled": result.get("claude_mechanism_enabled") is False,
    }
    status = "PASS" if all(checks.values()) else "FAIL"
    payload = {
        "schema_version": "v6-final-attestation-1",
        "status": status,
        "runtime_version": "v6-governed-roster-networkx-1",
        "run_id": str(args.run_id),
        "commit_sha": args.commit_sha,
        "primary_artifact": {
            "id": args.primary_artifact_id,
            "digest": args.primary_artifact_digest,
            "url": args.primary_artifact_url or None,
        },
        "governance_roster_sha256": result.get("governance_roster_sha256"),
        "model_calls": result.get("call_count"),
        "governance_model_calls": 0,
        "claude_calls": 0,
        "actual_cost_usd": result.get("actual_cost_usd"),
        "checks": checks,
        "final_status_markdown_sha256": hashlib.sha256(status_text.encode("utf-8")).hexdigest(),
        "independent_artifact_revalidation": {
            **independent,
            "evidence_sha256": hashlib.sha256(independent_path.read_bytes()).hexdigest(),
        },
        "evidence_chain": (
            "governance-roster -> frozen-business-evidence -> primary-artifact -> "
            "independent-v6-revalidation -> final-status -> final-attestation"
        ),
    }
    Path(args.output).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
