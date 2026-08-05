#!/usr/bin/env python3
"""Production pipeline that executes an immutable governance model plan."""
from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market
from artifact_manifest import write_manifest
from v5_catalog_view import (
    MINIMUM_EXPERT_COMPLETION_TOKENS,
    catalog_sha256,
    compact_endpoint_catalog,
    eligible_models,
)
from v5_endpoint_catalog import fetch_live_endpoint_payloads
from v5_governance_model_plan import validate_governance_model_plan
from v5_governed_plan_orchestrator import build_governed_proposal
from v5_json_io import load_json_or_default, write_json
from v5_no_tools_policy import forbidden_request_fields
from v5_provider_lock import canonical_provider_lock
from v5_recovery_runtime import build_production_runtime
from v5_runtime import RuntimeConfig
from v5_soft_proposal_materializer import materialize_proposal
from v5_task_envelope import build_task_envelope

RUNTIME_VERSION = "v5-governance-plan-runtime-1"
GOVERNANCE_CALLS_RESERVED = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="v5-artifacts")
    parser.add_argument("--governance-plan-file")
    parser.add_argument("--config", default=str(model_market.DEFAULT_CONFIG))
    parser.add_argument("--catalog-file")
    parser.add_argument("--endpoint-file")
    parser.add_argument("--ranking-limit", type=int, default=150)
    parser.add_argument("--maximum-total-calls", type=int, default=8)
    parser.add_argument("--maximum-recovery-calls", type=int, default=1)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--require-live-catalog", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _load_mapping(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return dict(value)


def _validate_budget(args: argparse.Namespace) -> tuple[int, int]:
    total = int(args.maximum_total_calls)
    recovery = int(args.maximum_recovery_calls)
    if not 4 <= total <= 16:
        raise ValueError("maximum_total_calls must be between 4 and 16")
    if not 0 <= recovery < total:
        raise ValueError("maximum_recovery_calls must leave expert-call capacity")
    if total - recovery < 3:
        raise ValueError("budget must leave capacity for at least three experts")
    if args.cost_anomaly_usd is not None and args.cost_anomaly_usd <= 0:
        raise ValueError("cost_anomaly_usd must be positive")
    if args.max_completion_tokens is not None and args.max_completion_tokens <= 0:
        raise ValueError("max_completion_tokens must be positive")
    return total, recovery


def _ticket_and_plan(args: argparse.Namespace, output: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    path = (
        Path(args.governance_plan_file)
        if args.governance_plan_file
        else output / "ticket.json"
    )
    ticket = _load_mapping(path)
    plan = validate_governance_model_plan(ticket)
    write_json(output / "governance-model-plan.json", plan)
    return ticket, plan


def _task_state(
    args: argparse.Namespace,
    run: Any,
    output: Path,
    plan: Mapping[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    task = str(args.task).strip()
    if not task:
        raise ValueError("task is empty")
    digest = sha256(task.encode("utf-8")).hexdigest()
    envelope = dict(
        build_task_envelope(
            task,
            minimum_context_length=run.minimum_context_length,
            maximum_completion_tokens=run.max_completion_tokens,
        )
    )
    envelope.update(
        {
            "decomposition_authority": "decision-system-governance",
            "selection_authority": "decision-system-governance",
            "governance_model_plan_sha256": plan["plan_sha256"],
            "expert_center_model_selection_allowed": False,
            "expert_center_model_reranking_allowed": False,
            "model_substitution_allowed": False,
            "provider_resolution_only": True,
            "claude_mechanism_enabled": False,
            "governance_model_calls_in_expert_center": 0,
            "local_task_classification_used": False,
            "cross_task_history_used": False,
        }
    )
    write_json(output / "v5-task-envelope.json", envelope)
    constraints = envelope.get("task_constraints")
    if isinstance(constraints, Mapping):
        write_json(output / "task-constraints.json", constraints)
    return task, digest, envelope


def _catalog_state(
    args: argparse.Namespace,
    run: Any,
    task_envelope: Mapping[str, Any],
) -> tuple[Mapping[str, Any], str, str]:
    required_context = int(task_envelope["required_context_tokens"])
    models, catalog_source = model_market.fetch_catalog(run)
    ranked = eligible_models(
        models,
        requested_context=required_context,
        maximum_models=int(args.ranking_limit),
    )
    if args.endpoint_file:
        payloads = _load_mapping(Path(args.endpoint_file))
        endpoint_source = f"fixture:{args.endpoint_file}"
        synthetic = False
    elif run.dry_run and run.catalog_file:
        payloads = {}
        endpoint_source = "synthetic-fixture-endpoints"
        synthetic = True
    else:
        payloads = fetch_live_endpoint_payloads(
            ranked,
            run,
            maximum_models=len(ranked),
        )
        endpoint_source = "openrouter-live-exact-endpoints"
        synthetic = False
    catalog = dict(
        compact_endpoint_catalog(
            ranked,
            payloads,
            allow_synthetic_fixture=synthetic,
            required_context_tokens=required_context,
            minimum_completion_tokens=MINIMUM_EXPERT_COMPLETION_TOKENS,
        )
    )
    catalog.update(
        {
            "selection_authority": "decision-system-governance",
            "model_selection_performed_locally": False,
            "model_reranking_performed_locally": False,
            "model_substitution_allowed": False,
            "provider_resolution_only": True,
        }
    )
    return catalog, catalog_source, endpoint_source


def _catalog_snapshot(
    catalog: Mapping[str, Any],
    catalog_source: str,
    endpoint_source: str,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    digest = catalog_sha256(catalog)
    return {
        "schema_version": "v5-governance-plan-catalog-snapshot-1",
        "catalog_snapshot_id": f"catalog-{digest[:20]}",
        "catalog_sha256": digest,
        "catalog_source": catalog_source,
        "endpoint_source": endpoint_source,
        "catalog": catalog,
        "selection_authority": "decision-system-governance",
        "governance_model_plan_sha256": plan["plan_sha256"],
        "model_selection_performed_locally": False,
        "model_reranking_performed_locally": False,
        "provider_resolution_performed_locally": True,
        "optimizer_used": False,
        "cross_task_history_used": False,
    }


def _runtime_config(
    args: argparse.Namespace,
    *,
    total_calls: int,
    recovery_calls: int,
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "runtime_version": RUNTIME_VERSION,
        "approved_total_calls": total_calls,
        "governance_calls_reserved": 0,
        "expert_total_call_limit": total_calls,
        "expert_recovery_call_limit": recovery_calls,
        "expert_initial_call_limit": total_calls - recovery_calls,
        "selected_expert_count": plan["expert_count"],
        "selection_authority": "decision-system-governance",
        "governance_model_plan_sha256": plan["plan_sha256"],
        "model_selection_performed_locally": False,
        "model_reranking_performed_locally": False,
        "model_substitution_allowed": False,
        "provider_resolution_authority": "expert-runtime-exact-endpoint-only",
        "team_topology": (
            "parallel-independent-analysis -> cross-review -> final-synthesis"
        ),
        "orchestration_library": "networkx",
        "claude_mechanism_enabled": False,
        "claude_calls": 0,
        "gpt_selection_calls": 0,
        "governance_model_calls": 0,
        "external_tools_allowed": False,
        "provider_fallback_allowed": False,
        "cost_advisory_usd": args.cost_anomaly_usd,
        "completion_capacity_advisory_tokens": args.max_completion_tokens,
        "local_token_ceiling_enforced": False,
        "local_task_classification_used": False,
        "optimizer_present": False,
        "agent_framework_present": False,
        "model_loop_allowed": False,
        "cross_task_history_used": False,
    }


def _zero_local_governance_artifacts(
    output: Path,
    plan: Mapping[str, Any],
    materialization_audit: Mapping[str, Any],
) -> dict[str, Any]:
    ledger = {
        "schema_version": "v5-external-governance-selection-ledger-1",
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "actual_governance_calls": 0,
        "claude_red_team_calls": 0,
        "gpt_proposal_calls": 0,
        "gpt_synthesis_calls": 0,
        "actual_cost_usd": 0.0,
        "calls": [],
        "selection_authority": "decision-system-governance",
        "selection_performed_in_expert_center": False,
        "governance_model_plan_sha256": plan["plan_sha256"],
        "claude_mechanism_enabled": False,
    }
    result = {
        "schema_version": "v5-external-governance-selection-result-1",
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "actual_calls": 0,
        "reserved_calls": 0,
        "actual_cost_usd": 0.0,
        "selection_authority": "decision-system-governance",
        "governance_model_plan_sha256": plan["plan_sha256"],
        "selection_performed_in_expert_center": False,
        "provider_materialization": dict(materialization_audit),
        "claude_mechanism_enabled": False,
        "claude_calls": 0,
        "gpt_selection_calls": 0,
    }
    write_json(output / "v5-governance-calls.json", ledger)
    write_json(output / "v5-governance-result.json", result)
    return ledger


def _request_audit(output: Path, *, approved_total_calls: int) -> None:
    path = output / "v5-request-audit.json"
    value = load_json_or_default(path, {})
    document = dict(value) if isinstance(value, Mapping) else {}
    requests = document.get("requests")
    requests = requests if isinstance(requests, list) else []
    invalid = [
        row
        for row in requests
        if not isinstance(row, Mapping)
        or forbidden_request_fields(row)
        or not canonical_provider_lock(row)
    ]
    document.update(
        {
            "schema_version": "v5-complete-request-audit-5",
            "status": (
                "PASS"
                if not invalid and len(requests) <= approved_total_calls
                else "FAIL"
            ),
            "request_count": len(requests),
            "approved_total_call_ceiling": approved_total_calls,
            "governance_request_count": 0,
            "expert_request_count": len(requests),
            "requests": requests,
            "provider_locks_valid": not invalid,
            "external_tools_allowed": False,
            "provider_fallback_allowed": False,
            "selection_authority": "decision-system-governance",
            "model_selection_performed_locally": False,
            "claude_mechanism_enabled": False,
        }
    )
    write_json(path, document)
    if document["status"] != "PASS":
        raise RuntimeError("complete request audit failed")


def _execute(
    *,
    args: argparse.Namespace,
    run: Any,
    output: Path,
    task: str,
    graph: Any,
    limits: Any,
    total_calls: int,
    recovery_calls: int,
    expert_call_fn: Any | None,
) -> dict[str, Any]:
    config = RuntimeConfig(
        total_call_limit=total_calls,
        recovery_call_limit=recovery_calls,
        cost_anomaly_usd=args.cost_anomaly_usd,
        tools_allowed=False,
        live_catalog_required=bool(args.require_live_catalog),
        provider_lock_required=True,
    )
    runtime = build_production_runtime(config)
    return runtime.execute_graph(
        graph,
        run,
        task,
        call_fn=expert_call_fn,
        output_dir=output,
        limits=limits,
    )


def _finalize_result(
    result: dict[str, Any],
    *,
    total_calls: int,
    plan: Mapping[str, Any],
    selection_audit: Mapping[str, Any],
) -> None:
    budget = result.get("execution_budget")
    expert_calls = (
        int(budget.get("calls_reserved") or 0)
        if isinstance(budget, Mapping)
        else 0
    )
    result.update(
        {
            "runtime_version": RUNTIME_VERSION,
            "total_model_calls": expert_calls,
            "approved_total_calls": total_calls,
            "governance": {
                "actual_calls_in_expert_center": 0,
                "reserved_calls": 0,
                "selection_authority": "decision-system-governance",
                "governance_model_plan_sha256": plan["plan_sha256"],
                "claude_mechanism_enabled": False,
            },
            "selection_authority": "decision-system-governance",
            "model_selection_performed_locally": False,
            "model_reranking_performed_locally": False,
            "selected_expert_count": plan["expert_count"],
            "selection_audit": dict(selection_audit),
        }
    )
    if expert_calls > total_calls:
        raise RuntimeError("overall model-call ceiling exceeded")


def _write_dry_run(
    output: Path,
    *,
    proposal: Mapping[str, Any],
    selection_audit: Mapping[str, Any],
    runtime_config: Mapping[str, Any],
) -> None:
    write_json(
        output / "v5-dry-run.json",
        {
            "schema_version": "v5-governance-plan-dry-run-1",
            "status": "validated-not-executed",
            "model_calls": 0,
            "proposal": proposal,
            "selection": selection_audit,
            "runtime": runtime_config,
        },
    )
    write_manifest(output)


def main(
    argv: Sequence[str] | None = None,
    *,
    expert_call_fn: Any | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    total_calls, recovery_calls = _validate_budget(args)
    run = model_market.build_run_config(args)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    ticket, plan = _ticket_and_plan(args, output)
    if int(plan["recovery_count"]) != recovery_calls:
        raise ValueError("governance recovery plan disagrees with admitted reserve")
    if int(plan["expert_count"]) > total_calls - recovery_calls:
        raise ValueError("governance expert plan exceeds initial call capacity")

    task, _, task_envelope = _task_state(args, run, output, plan)
    catalog, catalog_source, endpoint_source = _catalog_state(
        args, run, task_envelope
    )
    snapshot = _catalog_snapshot(
        catalog, catalog_source, endpoint_source, plan
    )
    runtime_config = _runtime_config(
        args,
        total_calls=total_calls,
        recovery_calls=recovery_calls,
        plan=plan,
    )
    write_json(output / "catalog-snapshot.json", snapshot)
    write_json(output / "v5-gpt-catalog-view.json", catalog)
    write_json(output / "v5-runtime-config.json", runtime_config)

    proposal, selection_audit = build_governed_proposal(
        ticket=ticket,
        catalog=catalog,
        task_envelope=task_envelope,
    )
    write_json(output / "v5-price-ranked-selection.json", selection_audit)

    graph, limits, materialization = materialize_proposal(
        proposal,
        task,
        task_envelope,
        catalog,
        approved_total_calls=total_calls,
        governance_calls_reserved=0,
        approved_recovery_calls=recovery_calls,
        cost_anomaly_usd=args.cost_anomaly_usd,
    )
    _zero_local_governance_artifacts(output, plan, selection_audit)
    write_json(
        output / "v5-selection.json",
        {
            "schema_version": "v5-governance-plan-selection-1",
            "status": "PASS",
            "proposal": proposal,
            "materialization": materialization,
            "selection_authority": "decision-system-governance",
            "governance_model_plan_sha256": plan["plan_sha256"],
            "model_selection_performed_locally": False,
            "model_reranking_performed_locally": False,
            "model_substitution_allowed": False,
            "provider_resolution_only": True,
            "claude_mechanism_enabled": False,
        },
    )
    write_json(output / "v5-execution-graph.json", graph.to_dict())

    if run.dry_run:
        _write_dry_run(
            output,
            proposal=proposal,
            selection_audit=selection_audit,
            runtime_config=runtime_config,
        )
        return 0

    result = _execute(
        args=args,
        run=run,
        output=output,
        task=task,
        graph=graph,
        limits=limits,
        total_calls=total_calls,
        recovery_calls=recovery_calls,
        expert_call_fn=expert_call_fn,
    )
    _request_audit(output, approved_total_calls=total_calls)
    _finalize_result(
        result,
        total_calls=total_calls,
        plan=plan,
        selection_audit=selection_audit,
    )
    write_json(output / "v5-result.json", result)
    write_json(
        output / "v5-execution-summary.json",
        {key: value for key, value in result.items() if key != "node_results"},
    )
    write_manifest(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
