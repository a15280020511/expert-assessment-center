#!/usr/bin/env python3
"""Independently revalidate an uploaded top-50 OR-Tools production artifact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from artifact_manifest import sha256_file
from v5_governance_model_plan import validate_governance_model_plan
from v5_price_ranked_execution_auditor import RUNTIME_VERSION, audit
from v5_price_ranked_support import load_mapping

SCHEMA_VERSION = "v5-independent-artifact-revalidation-4-open-provider"
POOL_AUTHORITY = "decision-system-governance"
ASSIGNMENT_AUTHORITY = "expert-assessment-center-ortools"


def _expected_digest(value: str) -> str:
    text = str(value or "").strip().lower()
    return text.split(":", 1)[-1] if text.startswith("sha256:") else text


def _record(failures: list[str], condition: bool, message: str) -> None:
    if not condition:
        failures.append(message)


def _validated_plan(
    ticket: Mapping[str, Any],
    plan_file: Mapping[str, Any],
    failures: list[str],
) -> dict[str, Any]:
    try:
        return validate_governance_model_plan(ticket, plan_file)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"governance model plan validation failed: {exc}")
        return {}


def _check_identity(
    runtime: Mapping[str, Any],
    ticket_status: Mapping[str, Any],
    plan: Mapping[str, Any],
    *,
    expected_sha: str,
    expected_run_id: str,
    maximum_calls: int,
    failures: list[str],
) -> None:
    top50 = plan.get("selected_from_top50_reasoning_pool_only") is True
    expected_authority = ASSIGNMENT_AUTHORITY if top50 else POOL_AUTHORITY
    checks = (
        (str(runtime.get("source_commit") or "") == str(expected_sha), "artifact source commit does not match authoritative production SHA"),
        (str(runtime.get("source_run_id") or "") == str(expected_run_id), "artifact source run id does not match current workflow run"),
        (int(ticket_status.get("calls") or 0) == int(maximum_calls), "artifact ticket call ceiling differs from admitted call ceiling"),
        (runtime.get("candidate_pool_authority") == POOL_AUTHORITY, "artifact runtime candidate-pool authority mismatch"),
        (runtime.get("selection_authority") == expected_authority, "artifact runtime selection authority mismatch"),
        (runtime.get("model_selection_performed_locally") is top50, "artifact runtime model-assignment evidence mismatch"),
        (runtime.get("governance_model_plan_sha256") == plan.get("plan_sha256"), "artifact runtime model plan digest mismatch"),
        (runtime.get("provider_routing_mode") == "unrestricted-openrouter", "artifact runtime provider routing is not unrestricted"),
        (runtime.get("provider_restrictions_applied") is False, "artifact runtime reports Provider restrictions"),
        (runtime.get("unrestricted_provider_fallback_allowed") is True, "artifact runtime does not allow unrestricted Provider fallback"),
        (runtime.get("model_substitution_allowed") is False, "artifact runtime permits model substitution"),
    )
    for condition, message in checks:
        _record(failures, condition, message)


def _ledger_state(
    ledger: Mapping[str, Any],
    maximum_calls: int,
    failures: list[str],
) -> tuple[dict[str, Any], float]:
    summary = ledger.get("summary")
    summary = dict(summary) if isinstance(summary, Mapping) else {}
    _record(failures, int(summary.get("call_count") or 0) <= int(maximum_calls), "artifact call count exceeds admitted ceiling")
    _record(failures, int(summary.get("governance_calls_in_expert_center") or 0) == 0, "artifact reports governance model calls in expert center")
    actual_cost = float(summary.get("provider_actual_cost_usd") or 0.0)
    _record(failures, actual_cost >= 0, "artifact actual cost is invalid")
    return summary, actual_cost


def revalidate(
    root: Path,
    *,
    expected_sha: str,
    expected_run_id: str,
    maximum_calls: int,
    cost_advisory_usd: float | None,
    archive: Path,
    expected_artifact_digest: str,
) -> dict[str, Any]:
    runtime = load_mapping(root, "production-runtime.json")
    ticket_status = load_mapping(root, "ticket-status.json")
    ticket = load_mapping(root, "ticket.json")
    plan_file = load_mapping(root, "governance-model-plan.json")
    ledger = load_mapping(root, "call-ledger.json")
    failures: list[str] = []

    observed_digest = sha256_file(archive)
    expected_digest = _expected_digest(expected_artifact_digest)
    _record(failures, not expected_digest or observed_digest == expected_digest, "downloaded artifact archive digest does not match GitHub digest")
    plan = _validated_plan(ticket, plan_file, failures)
    _check_identity(
        runtime,
        ticket_status,
        plan,
        expected_sha=expected_sha,
        expected_run_id=expected_run_id,
        maximum_calls=maximum_calls,
        failures=failures,
    )
    summary, actual_cost = _ledger_state(ledger, maximum_calls, failures)
    diagnosis = audit(
        root,
        execute_outcome="success",
        publish_outcome="success",
        require_manifest=True,
    )
    if diagnosis.get("status") != "PASS":
        failures.extend(str(row) for row in diagnosis.get("failures", []))
    failures = list(dict.fromkeys(failures))
    advisory = None if cost_advisory_usd is None else float(cost_advisory_usd)
    top50 = plan.get("selected_from_top50_reasoning_pool_only") is True
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "runtime_version": RUNTIME_VERSION,
        "recomputed_from_primitive_evidence": True,
        "paid_acceptance_verdict_used_as_source": False,
        "candidate_pool_authority": POOL_AUTHORITY,
        "selection_authority": ASSIGNMENT_AUTHORITY if top50 else POOL_AUTHORITY,
        "governance_model_plan_sha256": plan.get("plan_sha256"),
        "model_selection_performed_locally": top50,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "provider_routing_mode": "unrestricted-openrouter",
        "provider_restrictions_applied": False,
        "unrestricted_provider_fallback_allowed": True,
        "expected_sha": expected_sha,
        "observed_source_commit": runtime.get("source_commit"),
        "expected_run_id": str(expected_run_id),
        "observed_source_run_id": runtime.get("source_run_id"),
        "expected_artifact_digest": expected_digest,
        "observed_archive_sha256": observed_digest,
        "maximum_calls": int(maximum_calls),
        "observed_calls": int(summary.get("call_count") or 0),
        "actual_cost_usd": actual_cost,
        "cost_advisory_usd": advisory,
        "cost_advisory_exceeded": bool(advisory is not None and actual_cost > advisory + 1e-12),
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
    parser.add_argument("--cost-advisory-usd", type=float, default=None)
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
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
