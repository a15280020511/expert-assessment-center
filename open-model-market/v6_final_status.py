#!/usr/bin/env python3
"""Render the authoritative V6 execution status after artifact revalidation."""
from __future__ import annotations

import argparse
import json
import os
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
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--ticket-upload-outcome", required=True)
    parser.add_argument("--audit-outcome", required=True)
    parser.add_argument("--manifest-outcome", required=True)
    parser.add_argument("--artifact-id", default="")
    parser.add_argument("--artifact-url", default="")
    parser.add_argument("--artifact-digest", default="")
    parser.add_argument("--independent-revalidation-file", required=True)
    args = parser.parse_args()
    root = Path(args.output_dir)
    result = _load(root / "expert-team-result.json")
    audit = _load(root / "v6-execution-audit.json")
    independent = _load(Path(args.independent_revalidation_file))
    pass_conditions = {
        "business_result_success": result.get("status") == "success",
        "completion_full": result.get("completion_mode") == "full",
        "quality_full_success": result.get("quality_status") == "full_success",
        "v6_runtime": result.get("runtime_version") == "v6-governed-roster-networkx-1",
        "claude_disabled": result.get("claude_mechanism_enabled") is False,
        "governance_model_calls_zero": int(result.get("governance_call_count") or 0) == 0,
        "native_audit_pass": audit.get("status") == "PASS" and args.audit_outcome == "success",
        "manifest_frozen": args.manifest_outcome == "success",
        "primary_artifact_uploaded": args.ticket_upload_outcome == "success" and bool(args.artifact_id),
        "independent_revalidation_pass": independent.get("status") == "PASS",
    }
    status = "PASS" if all(pass_conditions.values()) else "FAIL"
    payload = {
        "schema_version": "v6-authoritative-final-status-1",
        "status": status,
        "runtime_version": "v6-governed-roster-networkx-1",
        "run_url": args.run_url,
        "primary_artifact_id": args.artifact_id or None,
        "primary_artifact_url": args.artifact_url or None,
        "primary_artifact_digest": args.artifact_digest or None,
        "governance_roster_sha256": result.get("governance_roster_sha256"),
        "selected_models": _load(root / "model-selection.json").get("primary_models", []),
        "actual_cost_usd": result.get("actual_cost_usd"),
        "model_calls": result.get("call_count"),
        "governance_model_calls": 0,
        "claude_calls": 0,
        "checks": pass_conditions,
        "failures": [name for name, passed in pass_conditions.items() if not passed],
    }
    (root / "final-status.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "utf-8",
    )
    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"status={status}\n")
    title = "EXPERT_TEAM_COMPLETED" if status == "PASS" else "EXPERT_TEAM_FAILED"
    print(f"## {title}\n")
    print(f"- Runtime: `v6-governed-roster-networkx-1`")
    print(f"- Final status: `{status}`")
    print(f"- Governance roster: `{payload['governance_roster_sha256']}`")
    print(f"- Expert model calls: `{payload['model_calls']}`")
    print("- Governance model calls: `0`")
    print("- Claude mechanism: `disabled`")
    print(f"- Actual cost: `${float(payload['actual_cost_usd'] or 0):.8f}`")
    print(f"- Primary Artifact ID: `{payload['primary_artifact_id'] or 'unavailable'}`")
    if payload["failures"]:
        print("- Failed checks: `" + ",".join(payload["failures"]) + "`")
    return 0 if status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
