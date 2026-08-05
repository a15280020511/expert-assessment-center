#!/usr/bin/env python3
"""Independently revalidate an uploaded governance-plan production artifact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import sha256_file
from v5_governance_model_plan import validate_governance_model_plan
from v5_price_ranked_execution_auditor import RUNTIME_VERSION, audit
from v5_price_ranked_support import load_mapping

SCHEMA_VERSION = "v5-independent-artifact-revalidation-3"


def _expected_digest(value: str) -> str:
    text = str(value or "").strip().lower()
    return text.split(":", 1)[-1] if text.startswith("sha256:") else text


def revalidate(
    root: Path,
    *,
    expected_sha: str,
    expected_run_id: str,
    maximum_calls: int,
    cost_advisory_usd: float,
    archive: Path,
    expected_artifact_digest: str,
) -> dict[str, Any]:
    runtime = load_mapping(root, "production-runtime.json")
    ticket_status = load_mapping(root, "ticket-status.json")
    ticket = load_mapping(root, "ticket.json")
    plan_file = load_mapping(root, "governance-model-plan.json")
    ledger = load_mapping(root, "call-ledger.json")
    failures: list[str] = []

    observed_archive_digest = sha256_file(archive)
    expected_digest = _expected_digest(expected_artifact_digest)
    if expected_digest and observed_archive_digest != expected_digest:
        failures.append(
            "downloaded artifact archive digest does not match GitHub digest"
        )
    if str(runtime.get("source_commit") or "") != str(expected_sha):
        failures.append(
            "artifact source commit does not match authoritative production SHA"
        )
    if str(runtime.get("source_run_id") or "") != str(expected_run_id):
        failures.append("artifact source run id does not match current workflow run")
    if int(ticket_status.get("calls") or 0) != int(maximum_calls):
        failures.append(
            "artifact ticket call ceiling differs from admitted call ceiling"
        )
    try:
        plan = validate_governance_model_plan(ticket, plan_file)
    except Exception as exc:  # noqa: BLE001
        plan = {}
        failures.append(f"governance model plan validation failed: {exc}")
    if runtime.get("selection_authority") != "decision-system-governance":
        failures.append("artifact runtime selection authority is not governance")
    if runtime.get("model_selection_performed_locally") is not False:
        failures.append("artifact runtime reports local model selection")
    if runtime.get("governance_model_plan_sha256") != plan.get("plan_sha256"):
        failures.append("artifact runtime model plan digest mismatch")

    summary = ledger.get("summary")
    summary = dict(summary) if isinstance(summary, Mapping) else {}
    if int(summary.get("call_count") or 0) > int(maximum_calls):
        failures.append("artifact call count exceeds admitted ceiling")
    if int(summary.get("governance_calls_in_expert_center") or 0) != 0:
        failures.append("artifact reports governance model calls in expert center")
    actual_cost = float(summary.get("provider_actual_cost_usd") or 0.0)
    if actual_cost < 0:
        failures.append("artifact actual cost is invalid")
    advisory_exceeded = actual_cost > float(cost_advisory_usd) + 1e-12

    diagnosis = audit(
        root,
        execute_outcome="success",
        publish_outcome="success",
        require_manifest=True,
    )
    if diagnosis.get("status") != "PASS":
        failures.extend(str(row) for row in diagnosis.get("failures", []))
    failures = list(dict.fromkeys(failures))
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "runtime_version": RUNTIME_VERSION,
        "recomputed_from_primitive_evidence": True,
        "paid_acceptance_verdict_used_as_source": False,
        "selection_authority": "decision-system-governance",
        "governance_model_plan_sha256": plan.get("plan_sha256"),
        "model_selection_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "expected_sha": expected_sha,
        "observed_source_commit": runtime.get("source_commit"),
        "expected_run_id": str(expected_run_id),
        "observed_source_run_id": runtime.get("source_run_id"),
        "expected_artifact_digest": expected_digest,
        "observed_archive_sha256": observed_archive_digest,
        "maximum_calls": int(maximum_calls),
        "observed_calls": int(summary.get("call_count") or 0),
        "actual_cost_usd": actual_cost,
        "cost_advisory_usd": float(cost_advisory_usd),
        "cost_advisory_exceeded": advisory_exceeded,
        "cost_threshold_can_invalidate_result": False,
        "claude_mechanism_enabled": False,
        "governance_model_calls_in_expert_center": 0,
        "deterministic_audit": diagnosis,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-dir", required=True)
    parser.add_argument("--expected-sha", required=True)
    parser.add_argument("--expected-run-id", required=True)
    parser.add_argument("--maximum-calls", type=int, required=True)
    parser.add_argument("--cost-advisory-usd", type=float, required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--expected-artifact-digest", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = revalidate(
        Path(args.artifact_dir),
        expected_sha=args.expected_sha,
        expected_run_id=args.expected_run_id,
        maximum_calls=args.maximum_calls,
        cost_advisory_usd=args.cost_advisory_usd,
        archive=Path(args.archive),
        expected_artifact_digest=args.expected_artifact_digest,
    )
    Path(args.output).write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
