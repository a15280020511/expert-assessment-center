#!/usr/bin/env python3
"""Execute a governance-owned expert model plan without local model selection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market
from artifact_manifest import write_manifest
from v5_governance_selection import SELECTION_AUTHORITY, load_and_validate
from v5_json_io import load_json_or_default, write_json
from v5_no_tools_policy import forbidden_request_fields
from v5_provider_lock import canonical_provider_lock
from v5_recovery_runtime import build_production_runtime
from v5_runtime import RuntimeConfig
from v5_soft_proposal_materializer import materialize_proposal

RUNTIME_VERSION = "v5-price-ranked-runtime-1"
GOVERNANCE_CALLS_RESERVED = 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="v5-artifacts")
    parser.add_argument("--governance-selection-file")
    parser.add_argument("--config", default=str(model_market.DEFAULT_CONFIG))
    parser.add_argument("--catalog-file")
    parser.add_argument("--endpoint-file")
    parser.add_argument("--ranking-limit", type=int, default=150)
    parser.add_argument("--maximum-total-calls", type=int, default=8)
    parser.add_argument("--maximum-recovery-calls", type=int, default=1)
    parser.add_argument("--expert-count", type=int)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--require-live-catalog", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _validate_budget(args: argparse.Namespace) -> tuple[int, int]:
    total = int(args.maximum_total_calls)
    recovery = int(args.maximum_recovery_calls)
    if not 4 <= total <= 16:
        raise ValueError("maximum_total_calls must be between 4 and 16")
    if not 0 <= recovery < total:
        raise ValueError("maximum_recovery_calls must leave initial-call capacity")
    if total - recovery < 3:
        raise ValueError("expert team requires at least three initial calls")
    if args.expert_count is not None:
        raise ValueError(
            "expert-count is governance-owned and cannot be supplied to the expert center"
        )
    if args.catalog_file or args.endpoint_file:
        raise ValueError(
            "expert-center catalog and endpoint inputs are removed; governance plan is required"
        )
    if args.cost_anomaly_usd is not None and args.cost_anomaly_usd <= 0:
        raise ValueError("cost_anomaly_usd must be positive")
    if args.max_completion_tokens is not None and args.max_completion_tokens <= 0:
        raise ValueError("max_completion_tokens must be positive")
    return total, recovery


def _selection_path(args: argparse.Namespace, output: Path) -> Path:
    path = (
        Path(args.governance_selection_file)
        if args.governance_selection_file
        else output / "governance-selection.json"
    )
    if not path.is_file():
        raise FileNotFoundError(
            "governance selection plan is missing; local model selection is removed"
        )
    return path


def _selected_catalog_rows(
    plan: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    catalog = plan["catalog"]
    index = {
        (str(row["model"]), str(row["provider"])): row
        for row in catalog["endpoints"]
    }
    selected: list[dict[str, Any]] = []
    recovery: list[dict[str, Any]] = []
    for node in plan["proposal"]["nodes"]:
        route = index[(str(node["model"]), str(node["provider"]))]
        selected.append(
            {
                **dict(route),
                "node_id": str(node["node_id"]),
                "role": str(node.get("role") or ""),
            }
        )
        for candidate in node.get("recovery", []):
            recovery.append(
                dict(index[(str(candidate["model"]), str(candidate["provider"]))])
            )
    return selected, recovery


def _selection_audit(
    plan: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> dict[str, Any]:
    selected, recovery = _selected_catalog_rows(plan)
    return {
        "schema_version": "v5-governance-owned-selection-1",
        "status": "PASS",
        "selection_authority": SELECTION_AUTHORITY,
        "selection_source_repository": plan["source_repository"],
        "selection_source_commit": plan.get("source_commit", ""),
        "selection_plan_sha256": plan["plan_sha256"],
        "catalog_sha256": plan["catalog_sha256"],
        "proposal_sha256": plan["proposal_sha256"],
        "task_sha256": plan["task_sha256"],
        "selected_expert_count": plan["selected_expert_count"],
        "selected_endpoints": selected,
        "recovery_endpoints": recovery,
        "validation": dict(validation),
        "networkx_used_for_dag_validation": True,
        "expert_center_selection_performed": False,
        "expert_center_catalog_fetch_performed": False,
        "price_sorting_performed_in_expert_center": False,
        "local_fallback_used": False,
        "optimizer_used": False,
        "claude_calls": 0,
        "gpt_selection_calls": 0,
        "governance_model_calls": 0,
        "cross_task_history_used": False,
    }


def _catalog_snapshot(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v5-governance-catalog-snapshot-1",
        "catalog_snapshot_id": f"governance-{str(plan['catalog_sha256'])[:20]}",
        "catalog_sha256": plan["catalog_sha256"],
        "catalog_source": "decision-system-governance-bound-plan",
        "endpoint_source": "decision-system-governance-exact-provider-resolution",
        "catalog": plan["catalog"],
        "selection_authority": SELECTION_AUTHORITY,
        "expert_center_catalog_fetch_performed": False,
        "expert_center_selection_performed": False,
        "local_fallback_used": False,
    }


def _runtime_config(
    args: argparse.Namespace,
    plan: Mapping[str, Any],
    total: int,
    recovery: int,
) -> dict[str, Any]:
    return {
        "runtime_version": RUNTIME_VERSION,
        "approved_total_calls": total,
        "governance_calls_reserved": 0,
        "expert_total_call_limit": total,
        "expert_recovery_call_limit": recovery,
        "expert_initial_call_limit": total - recovery,
        "selected_expert_count": plan["selected_expert_count"],
        "selection_authority": SELECTION_AUTHORITY,
        "selection_plan_sha256": plan["plan_sha256"],
        "task_decomposition_authority": SELECTION_AUTHORITY,
        "expert_center_selection_present": False,
        "expert_center_catalog_fetch_present": False,
        "local_selection_fallback_present": False,
        "team_topology": "governance-declared-networkx-dag",
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
        "optimizer_present": False,
        "agent_framework_present": False,
        "model_loop_allowed": False,
        "cross_task_history_used": False,
    }


def _zero_governance_ledger(output: Path, plan: Mapping[str, Any]) -> dict[str, Any]:
    ledger = {
        "schema_version": "v5-zero-model-governance-ledger-2",
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS",
        "actual_governance_calls": 0,
        "claude_red_team_calls": 0,
        "gpt_proposal_calls": 0,
        "gpt_synthesis_calls": 0,
        "actual_cost_usd": 0.0,
        "calls": [],
        "selection_authority": SELECTION_AUTHORITY,
        "selection_plan_sha256": plan["plan_sha256"],
        "selection_occurred_in_governance_workflow": True,
        "selection_inference_calls": 0,
        "expert_center_selection_performed": False,
        "claude_mechanism_enabled": False,
    }
    write_json(output / "v5-governance-calls.json", ledger)
    write_json(
        output / "v5-governance-result.json",
        {
            "schema_version": "v5-governance-owned-selection-result-1",
            "runtime_version": RUNTIME_VERSION,
            "status": "PASS",
            "actual_calls": 0,
            "actual_cost_usd": 0.0,
            "selection_authority": SELECTION_AUTHORITY,
            "selection_plan_sha256": plan["plan_sha256"],
            "expert_center_selection_performed": False,
            "claude_mechanism_enabled": False,
        },
    )
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
            "selection_authority": SELECTION_AUTHORITY,
            "expert_center_selection_performed": False,
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
    total: int,
    recovery: int,
    expert_call_fn: Any | None,
) -> dict[str, Any]:
    config = RuntimeConfig(
        total_call_limit=total,
        recovery_call_limit=recovery,
        cost_anomaly_usd=args.cost_anomaly_usd,
        tools_allowed=False,
        live_catalog_required=False,
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


def main(
    argv: Sequence[str] | None = None,
    *,
    expert_call_fn: Any | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    total, recovery = _validate_budget(args)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    plan, validation = load_and_validate(
        _selection_path(args, output),
        approved_total_calls=total,
        approved_recovery_calls=recovery,
    )
    task = str(args.task).strip()
    if task != str(plan["task_text"]).strip():
        raise ValueError("runtime task differs from governance-bound task")
    if args.require_live_catalog:
        # Compatibility flag: the governance-bound exact catalog is authoritative.
        pass
    run = model_market.build_run_config(args)
    write_json(output / "governance-selection.json", plan)
    write_json(output / "governance-selection-validation.json", validation)
    write_json(output / "v5-task-envelope.json", plan["task_envelope"])
    constraints = plan["task_envelope"].get("task_constraints")
    if isinstance(constraints, Mapping):
        write_json(output / "task-constraints.json", constraints)
    snapshot = _catalog_snapshot(plan)
    write_json(output / "catalog-snapshot.json", snapshot)
    write_json(output / "v5-gpt-catalog-view.json", plan["catalog"])
    runtime_config = _runtime_config(args, plan, total, recovery)
    write_json(output / "v5-runtime-config.json", runtime_config)
    selection = _selection_audit(plan, validation)
    write_json(output / "v5-price-ranked-selection.json", selection)
    write_json(
        output / "v5-selection.json",
        {
            **selection,
            "proposal": plan["proposal"],
        },
    )
    _zero_governance_ledger(output, plan)

    graph, limits, materialization = materialize_proposal(
        plan["proposal"],
        task,
        plan["task_envelope"],
        plan["catalog"],
        approved_total_calls=total,
        governance_calls_reserved=0,
        approved_recovery_calls=recovery,
        cost_anomaly_usd=args.cost_anomaly_usd,
    )
    write_json(output / "v5-materialization.json", materialization)
    graph_document = graph.to_dict()
    metadata = graph_document.get("metadata")
    if isinstance(metadata, dict):
        metadata.update(
            {
                "selection_authority": SELECTION_AUTHORITY,
                "selection_plan_sha256": plan["plan_sha256"],
                "expert_center_selection_performed": False,
                "expert_center_catalog_fetch_performed": False,
                "local_fallback_used": False,
            }
        )
    write_json(output / "v5-execution-graph.json", graph_document)

    if run.dry_run:
        write_json(
            output / "v5-dry-run.json",
            {
                "schema_version": "v5-governance-owned-dry-run-1",
                "status": "validated-not-executed",
                "model_calls": 0,
                "selection_authority": SELECTION_AUTHORITY,
                "selection_plan_sha256": plan["plan_sha256"],
                "expert_center_selection_performed": False,
                "expert_center_catalog_fetch_performed": False,
                "local_fallback_used": False,
                "proposal": plan["proposal"],
                "materialization": materialization,
                "runtime": runtime_config,
            },
        )
        write_manifest(output)
        return 0

    result = _execute(
        args=args,
        run=run,
        output=output,
        task=task,
        graph=graph,
        limits=limits,
        total=total,
        recovery=recovery,
        expert_call_fn=expert_call_fn,
    )
    _request_audit(output, approved_total_calls=total)
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
            "approved_total_calls": total,
            "governance": {
                "actual_calls": 0,
                "reserved_calls": 0,
                "actual_cost_usd": 0.0,
                "selection_authority": SELECTION_AUTHORITY,
                "selection_plan_sha256": plan["plan_sha256"],
                "claude_mechanism_enabled": False,
            },
            "selection_authority": SELECTION_AUTHORITY,
            "selection_plan_sha256": plan["plan_sha256"],
            "selected_expert_count": plan["selected_expert_count"],
            "expert_center_selection_performed": False,
            "expert_center_catalog_fetch_performed": False,
            "local_fallback_used": False,
        }
    )
    if expert_calls > total:
        raise RuntimeError("overall model-call ceiling exceeded")
    write_json(output / "v5-result.json", result)
    write_json(
        output / "v5-execution-summary.json",
        {key: value for key, value in result.items() if key != "node_results"},
    )
    write_manifest(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
