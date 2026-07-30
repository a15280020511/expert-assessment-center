#!/usr/bin/env python3
"""Fail-closed R8 paid-run gate using the exact production runtime preflight.

The gate reads already collected catalog/endpoint metadata and deterministic task
artifacts. It regenerates each strict-economy graph, runs the same optimizer and
R8 runtime preflight used by paid execution, and makes zero completion calls.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_candidate_diversity
import v5_economy_zero_call_diagnostic as diagnostic
import v5_planner
import v5_production_hardening as production
import v5_r8_executor as runtime
import v5_value_optimizer
from execution_graph import ExecutionGraph, GraphLimits

DEFAULT_TASK_IDS = (
    "retail-expansion-unit-economics",
    "software-job-runner-security",
    "public-health-rumor-response",
)
EXPECTED_COST_POLICY = "reasoning-inclusive-p95-usage-not-max-allowance-r8"


class JointGateError(RuntimeError):
    """Raised when zero-call evidence cannot safely unlock paid inference."""


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise JointGateError(f"{path} must contain one JSON object")
    return value


def _strict_tier(task: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for row in task.get("tiers", []):
        if not isinstance(row, Mapping):
            continue
        tier = row.get("tier") if isinstance(row.get("tier"), Mapping) else {}
        if tier.get("name") == "strict-economy":
            return row
    return None


def _node_plan(tier: Mapping[str, Any], max_nodes: int) -> Mapping[str, Any] | None:
    for row in tier.get("node_limit_attempts", []):
        if isinstance(row, Mapping) and int(row.get("max_nodes", -1)) == max_nodes:
            return row
    return None


def _resource_bundle(task_root: Path) -> dict[str, Any]:
    task_semantics = _load_json(task_root / "task-interpretations.json")
    atomic_graphs = _load_json(task_root / "atomic-work-graph.json")
    matrices = _load_json(task_root / "task-resource-matrix.json")
    return {
        "version": 5,
        "phase_a_complete": True,
        "model_market_accessed": False,
        "task_semantics": task_semantics,
        "atomic_work_graphs": atomic_graphs,
        "resource_matrices": matrices,
    }


def _strict_price_tier() -> Mapping[str, Any]:
    for tier in diagnostic.PRICE_TIERS:
        if tier.get("name") == "strict-economy":
            return tier
    raise JointGateError("strict-economy price tier is not defined")


def _exact_runtime_preflight(
    task_root: Path,
    *,
    max_nodes: int,
    max_cost_usd: float,
) -> dict[str, Any]:
    resources = _resource_bundle(task_root)
    raw_market = _load_json(task_root / "raw-model-endpoint-market.json")
    filtered_market = diagnostic._filter_market(raw_market, _strict_price_tier())
    candidates = v5_planner.generate_candidate_graph(
        resources,
        filtered_market,
        maximum_per_group=12,
    )
    limits = GraphLimits(
        max_nodes=max_nodes,
        max_edges=64,
        max_stages=8,
        max_model_calls=max_nodes,
        max_retries=1,
        max_replacements=2,
        max_budget_usd=max_cost_usd,
        max_output_allowance_tokens=10_000,
    )
    optimized = v5_value_optimizer.optimize_execution_graph(
        candidates,
        limits=limits,
        solver_timeout_seconds=20.0,
    )
    graph = ExecutionGraph.from_mapping(optimized["execution_graph"])
    adjusted, preflight = runtime._preflight(graph, limits)
    return {
        "status": preflight.get("status"),
        "passed": not preflight.get("blockers"),
        "selected_candidate_ids": list(optimized.get("selected_candidate_ids") or []),
        "selected_node_count": len(adjusted.nodes),
        "selected_models": sorted({node.model for node in adjusted.nodes}),
        "selected_provider_endpoints": sorted({node.provider_endpoint for node in adjusted.nodes}),
        "estimated_total_cost_usd": adjusted.estimated_total_cost,
        "budget_preflight_parity": optimized.get("budget_preflight_parity"),
        "runtime_preflight": preflight,
        "model_inference_calls": 0,
        "actual_cost_usd": 0.0,
    }


def _verify_node_evidence(
    task_id: str,
    plan: Mapping[str, Any],
    runtime_report: Mapping[str, Any],
    *,
    max_nodes: int,
    max_cost_usd: float,
) -> tuple[list[str], dict[str, Any]]:
    blockers: list[str] = []
    nodes = [row for row in plan.get("selected_nodes", []) if isinstance(row, Mapping)]
    selected_count = int(plan.get("selected_node_count", len(nodes)) or 0)
    estimated_cost = float(plan.get("estimated_total_cost_usd", 0.0) or 0.0)

    if not bool(plan.get("feasible")):
        blockers.append(f"{task_id}:joint-plan-infeasible")
    if selected_count <= 0 or selected_count > max_nodes:
        blockers.append(f"{task_id}:selected-node-count={selected_count}>limit={max_nodes}")
    if len(nodes) != selected_count:
        blockers.append(
            f"{task_id}:selected-node-evidence-count={len(nodes)}!=declared={selected_count}"
        )
    if estimated_cost <= 0.0 or estimated_cost > max_cost_usd + 1e-12:
        blockers.append(
            f"{task_id}:estimated-cost={estimated_cost:.8f}>cap={max_cost_usd:.8f}"
        )

    models: set[str] = set()
    endpoints: set[str] = set()
    for node in nodes:
        node_id = str(node.get("node_id") or "unknown")
        model = str(node.get("model") or "")
        endpoint = str(node.get("provider_endpoint") or "")
        if model:
            models.add(model)
        if endpoint:
            endpoints.add(endpoint)
        allowance = int(node.get("recommended_output_allowance_tokens", 0) or 0)
        usage = int(node.get("estimated_completion_usage_tokens", 0) or 0)
        if allowance <= 0 or usage <= 0 or usage > allowance:
            blockers.append(
                f"{task_id}:{node_id}:invalid-allowance-usage={allowance}/{usage}"
            )
        if node.get("output_allowance_is_cost_assumption") is not False:
            blockers.append(f"{task_id}:{node_id}:allowance-still-used-as-cost-assumption")
        if node.get("cost_estimation_policy") != EXPECTED_COST_POLICY:
            blockers.append(f"{task_id}:{node_id}:unexpected-cost-policy")
        if float(node.get("estimated_cost_usd", 0.0) or 0.0) <= 0.0:
            blockers.append(f"{task_id}:{node_id}:missing-positive-cost-estimate")

    independence = plan.get("independence_policy")
    independence = independence if isinstance(independence, Mapping) else {}
    if independence.get("hard_model_diversity_scope") != "explicit-independence-groups-only":
        blockers.append(f"{task_id}:unexpected-model-independence-scope")
    for constraint in independence.get("constraints", []):
        if not isinstance(constraint, Mapping) or not bool(constraint.get("different_model_required")):
            continue
        work_id = str(constraint.get("work_id") or "")
        copies = int(constraint.get("copies", 0) or 0)
        work_models = {
            str(node.get("model") or "")
            for node in nodes
            if work_id in {str(value) for value in node.get("assigned_work", [])}
            and node.get("model")
        }
        if copies <= 1 or len(work_models) < copies:
            blockers.append(
                f"{task_id}:{work_id}:distinct-models={len(work_models)}<copies={copies}"
            )

    runtime_preflight = runtime_report.get("runtime_preflight")
    runtime_preflight = runtime_preflight if isinstance(runtime_preflight, Mapping) else {}
    runtime_blockers = [str(value) for value in runtime_preflight.get("blockers", [])]
    if not bool(runtime_report.get("passed")) or runtime_blockers:
        blockers.extend(f"{task_id}:runtime-preflight:{value}" for value in runtime_blockers)
        if not runtime_blockers:
            blockers.append(f"{task_id}:runtime-preflight-did-not-pass")
    runtime_count = int(runtime_report.get("selected_node_count", 0) or 0)
    if runtime_count != selected_count:
        blockers.append(
            f"{task_id}:runtime-selected-node-count={runtime_count}!=evidence={selected_count}"
        )
    planned_ids = {str(value) for value in plan.get("selected_candidate_ids", [])}
    runtime_ids = {str(value) for value in runtime_report.get("selected_candidate_ids", [])}
    if planned_ids and runtime_ids and planned_ids != runtime_ids:
        blockers.append(f"{task_id}:runtime-plan-does-not-match-joint-plan")

    return blockers, {
        "task_id": task_id,
        "passed": not blockers,
        "selected_node_count": selected_count,
        "estimated_total_cost_usd": round(estimated_cost, 8),
        "budget_slack_usd": round(max_cost_usd - estimated_cost, 8),
        "selected_model_count": len(models),
        "selected_provider_endpoint_count": len(endpoints),
        "allowance_usage_verified": bool(nodes) and all(
            int(node.get("estimated_completion_usage_tokens", 0) or 0)
            <= int(node.get("recommended_output_allowance_tokens", 0) or 0)
            for node in nodes
        ),
        "exact_runtime_preflight": dict(runtime_report),
        "blockers": blockers,
    }


def evaluate(
    root: str | Path,
    *,
    task_ids: Sequence[str] = DEFAULT_TASK_IDS,
    max_nodes: int = 9,
    max_cost_usd: float = 0.25,
) -> dict[str, Any]:
    production.install()
    v5_candidate_diversity.install()
    root_path = Path(root)
    blockers: list[str] = []
    task_reports: list[dict[str, Any]] = []
    for task_id in task_ids:
        task_root = root_path / "tasks" / task_id
        path = task_root / "zero-call-task-diagnostic.json"
        if not path.exists():
            blockers.append(f"{task_id}:missing-zero-call-task-diagnostic")
            continue
        task = _load_json(path)
        if int(task.get("model_inference_calls", -1) or 0) != 0:
            blockers.append(f"{task_id}:zero-call-contract-violated")
        if float(task.get("actual_cost_usd", -1.0) or 0.0) != 0.0:
            blockers.append(f"{task_id}:zero-call-cost-not-zero")
        tier = _strict_tier(task)
        if tier is None:
            blockers.append(f"{task_id}:strict-economy-tier-missing")
            continue
        plan = _node_plan(tier, max_nodes)
        if plan is None:
            blockers.append(f"{task_id}:max-nodes-{max_nodes}-attempt-missing")
            continue
        try:
            runtime_report = _exact_runtime_preflight(
                task_root,
                max_nodes=max_nodes,
                max_cost_usd=max_cost_usd,
            )
        except Exception as exc:  # noqa: BLE001
            blockers.append(f"{task_id}:runtime-preflight-reconstruction-failed:{exc}")
            continue
        task_blockers, report = _verify_node_evidence(
            task_id,
            plan,
            runtime_report,
            max_nodes=max_nodes,
            max_cost_usd=max_cost_usd,
        )
        blockers.extend(task_blockers)
        task_reports.append(report)

    if len(task_reports) != len(task_ids):
        blockers.append(
            f"task-evidence-count={len(task_reports)}!=required={len(task_ids)}"
        )
    blockers = sorted(set(blockers))
    return {
        "version": 2,
        "gate": "v5-r8-stage-d-exact-runtime-zero-call-preflight",
        "status": "passed" if not blockers else "blocked",
        "paid_inference_allowed": not blockers,
        "task_ids": list(task_ids),
        "max_nodes_per_v5_task": int(max_nodes),
        "max_estimated_v5_cost_per_task_usd": round(float(max_cost_usd), 8),
        "runtime_preflight_parity_verified": not blockers,
        "model_inference_calls": 0,
        "actual_model_cost_usd": 0.0,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
        "tasks": task_reports,
        "blockers": blockers,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate exact R8 runtime preflight evidence")
    parser.add_argument("--root", required=True)
    parser.add_argument("--max-nodes", type=int, default=9)
    parser.add_argument("--max-cost-usd", type=float, default=0.25)
    parser.add_argument("--output", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        report = evaluate(
            args.root,
            max_nodes=args.max_nodes,
            max_cost_usd=args.max_cost_usd,
        )
        Path(args.output).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["paid_inference_allowed"] else 3
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
