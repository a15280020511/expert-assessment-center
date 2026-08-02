#!/usr/bin/env python3
"""GPT-led V5 pipeline with one Claude advisory red-team pass."""
from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market
from artifact_manifest import write_manifest
from v5_catalog_view import (
    catalog_sha256,
    compact_endpoint_catalog,
    eligible_models,
)
from v5_claude_red_team_policy import CLAUDE_RED_TEAM_GOVERNANCE_CALLS
from v5_endpoint_catalog import fetch_live_endpoint_payloads
from v5_governance_runtime import (
    run_single_pass_governance,
    write_governance_artifacts,
)
from v5_gpt_expert_selector import build_proposal_request
from v5_proposal_materializer import materialize_proposal
from v5_recovery_runtime import build_production_runtime
from v5_runtime import RuntimeConfig
from v5_task_envelope import build_task_envelope

RUNTIME_VERSION = "v5-gpt-claude-runtime-2"


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _load(path: str | Path) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="v5-artifacts")
    parser.add_argument(
        "--config",
        default=str(model_market.DEFAULT_CONFIG),
    )
    parser.add_argument("--catalog-file")
    parser.add_argument("--endpoint-file")
    parser.add_argument("--quality-tier", default="value")
    parser.add_argument("--ranking-limit", type=int, default=150)
    parser.add_argument("--maximum-total-calls", type=int, default=8)
    parser.add_argument("--maximum-recovery-calls", type=int, default=1)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument("--reasoning-effort")
    parser.add_argument("--require-live-catalog", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def _validate_budget(args: argparse.Namespace) -> tuple[int, int, int]:
    total = int(args.maximum_total_calls)
    recovery = int(args.maximum_recovery_calls)
    governance = CLAUDE_RED_TEAM_GOVERNANCE_CALLS
    if not 4 <= total <= 16:
        raise ValueError("maximum_total_calls must be between 4 and 16")
    if recovery < 0:
        raise ValueError("maximum_recovery_calls must be non-negative")
    expert_total = total - governance
    if recovery >= expert_total:
        raise ValueError(
            "budget must leave at least one expert initial call after "
            "three governance calls and recovery reserve"
        )
    if (
        args.cost_anomaly_usd is not None
        and float(args.cost_anomaly_usd) <= 0
    ):
        raise ValueError("cost_anomaly_usd must be positive")
    return total, recovery, expert_total


def _merge_request_audit(
    output: Path,
    governance_ledger: Mapping[str, Any],
    *,
    approved_total_calls: int,
) -> None:
    path = output / "v5-request-audit.json"
    try:
        expert = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        expert = {}
    expert_requests = (
        expert.get("requests", [])
        if isinstance(expert, Mapping)
        and isinstance(expert.get("requests"), list)
        else []
    )
    governance_calls = [
        row
        for row in governance_ledger.get("calls", [])
        if isinstance(row, Mapping)
    ]
    governance_requests = [
        dict(row.get("request") or {})
        for row in governance_calls
    ]
    requests = [*governance_requests, *expert_requests]
    forbidden = {
        "tools",
        "tool_choice",
        "plugins",
        "web_search",
        "web_search_options",
        "file_search",
        "browser",
        "code_interpreter",
        "models",
    }
    def forbidden_fields(row: Mapping[str, Any]) -> set[str]:
        direct = forbidden.intersection(row)
        recorded = row.get("request_fields")
        if isinstance(recorded, list):
            direct.update(forbidden.intersection(str(value) for value in recorded))
        return direct

    status = (
        "PASS"
        if len(requests) <= approved_total_calls
        and all(not forbidden_fields(row) for row in requests)
        else "FAIL"
    )
    _write(
        path,
        {
            "schema_version": "v5-complete-request-audit-2",
            "status": status,
            "request_count": len(requests),
            "approved_total_call_ceiling": approved_total_calls,
            "governance_request_count": len(governance_requests),
            "expert_request_count": len(expert_requests),
            "requests": requests,
            "external_tools_allowed": False,
            "provider_fallback_allowed": False,
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "claude_covers_internal_selection": True,
            "claude_covers_external_information": True,
            "second_claude_review_allowed": False,
            "model_loop_allowed": False,
        },
    )
    if status != "PASS":
        raise RuntimeError("complete request audit failed")


def main(
    argv: Sequence[str] | None = None,
    *,
    governance_call_fn: Any | None = None,
    expert_call_fn: Any | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    total_calls, recovery_calls, expert_total_calls = _validate_budget(args)
    run = model_market.build_run_config(args)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    task = str(args.task).strip()
    if not task:
        raise ValueError("task is empty")
    task_digest = sha256(task.encode("utf-8")).hexdigest()
    task_envelope = build_task_envelope(
        task,
        minimum_context_length=run.minimum_context_length,
        maximum_completion_tokens=run.max_completion_tokens,
    )
    _write(output / "v5-task-envelope.json", task_envelope)
    _write(
        output / "task-constraints.json",
        task_envelope["task_constraints"],
    )

    models, catalog_source = model_market.fetch_catalog(run)
    ranked = eligible_models(
        models,
        requested_context=int(task_envelope["required_context_tokens"]),
        maximum_models=int(args.ranking_limit),
    )
    if args.endpoint_file:
        endpoint_payloads = _load(args.endpoint_file)
        endpoint_source = f"fixture:{args.endpoint_file}"
        allow_synthetic = False
    elif run.dry_run and run.catalog_file:
        endpoint_payloads = {}
        endpoint_source = "synthetic-fixture-endpoints"
        allow_synthetic = True
    else:
        endpoint_payloads = fetch_live_endpoint_payloads(
            ranked,
            run,
            maximum_models=len(ranked),
        )
        endpoint_source = "openrouter-live-exact-endpoints"
        allow_synthetic = False
    catalog = compact_endpoint_catalog(
        ranked,
        endpoint_payloads,
        allow_synthetic_fixture=allow_synthetic,
    )
    snapshot_digest = catalog_sha256(catalog)
    snapshot = {
        "schema_version": "v5-gpt-catalog-snapshot-2",
        "catalog_snapshot_id": f"catalog-{snapshot_digest[:20]}",
        "catalog_sha256": snapshot_digest,
        "catalog_source": catalog_source,
        "endpoint_source": endpoint_source,
        "catalog": catalog,
        "local_task_classification_used": False,
        "local_atomic_work_generation_used": False,
        "local_resource_matrix_used": False,
        "local_scoring_used": False,
        "optimizer_used": False,
        "cross_task_history_used": False,
    }
    _write(output / "catalog-snapshot.json", snapshot)
    _write(output / "v5-gpt-catalog-view.json", catalog)
    _write(
        output / "v5-runtime-config.json",
        {
            "runtime_version": RUNTIME_VERSION,
            "approved_total_calls": total_calls,
            "governance_calls_reserved": CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
            "expert_total_call_limit": expert_total_calls,
            "expert_recovery_call_limit": recovery_calls,
            "expert_initial_call_limit": expert_total_calls - recovery_calls,
            "cost_anomaly_usd": args.cost_anomaly_usd,
            "selection_authority": "gpt-latest",
            "task_decomposition_authority": "gpt-latest",
            "red_team_role": "claude-opus-latest-advisory-once",
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "gpt_synthesis_calls": 1,
            "final_authority": "deterministic-constitutional-validator",
            "local_task_classification_used": False,
            "local_atomic_work_generation_used": False,
            "local_resource_matrix_used": False,
            "local_planner_present": False,
            "optimizer_present": False,
            "model_loop_allowed": False,
        },
    )

    if run.dry_run:
        proposal_request = build_proposal_request(
            task=task,
            task_envelope=task_envelope,
            catalog=catalog,
            approved_total_calls=total_calls,
            governance_calls_reserved=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
            approved_recovery_calls=recovery_calls,
            cost_anomaly_usd=args.cost_anomaly_usd,
        )
        _write(
            output / "v5-dry-run.json",
            {
                "schema_version": "v5-gpt-claude-advisory-dry-run-2",
                "status": "validated-not-executed",
                "model_calls": 0,
                "task_envelope": task_envelope,
                "proposal_request": proposal_request,
                "claude_model": "~anthropic/claude-opus-latest",
                "claude_calls_per_task": 1,
                "claude_is_advisory_only": True,
                "claude_gatekeeping_allowed": False,
                "gpt_synthesis_calls": 1,
                "second_claude_review_allowed": False,
                "local_task_classification_used": False,
                "local_atomic_work_generation_used": False,
                "local_resource_matrix_used": False,
                "local_scoring_used": False,
                "optimizer_used": False,
                "cp_sat_used": False,
                "pareto_pruning_used": False,
            },
        )
        write_manifest(output)
        return 0

    _, _, governance, governance_ledger = run_single_pass_governance(
        run=run,
        task=task,
        task_digest=task_digest,
        task_envelope=task_envelope,
        catalog=catalog,
        approved_total_calls=total_calls,
        governance_calls_reserved=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        approved_recovery_calls=recovery_calls,
        cost_anomaly_usd=args.cost_anomaly_usd,
        call_fn=governance_call_fn,
    )
    write_governance_artifacts(output, governance, governance_ledger)

    governance_cost = float(
        governance_ledger.get("actual_cost_usd") or 0.0
    )
    remaining_cost = (
        None
        if args.cost_anomaly_usd is None
        else float(args.cost_anomaly_usd) - governance_cost
    )
    if remaining_cost is not None and remaining_cost <= 0:
        raise RuntimeError("governance calls exhausted the approved cost guard")
    graph, graph_limits, materialization = materialize_proposal(
        governance["final_proposal"],
        task,
        task_envelope,
        catalog,
        approved_total_calls=total_calls,
        governance_calls_reserved=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        approved_recovery_calls=recovery_calls,
        cost_anomaly_usd=remaining_cost,
    )
    governance["materialization_after_governance_cost"] = materialization
    _write(output / "v5-governance-result.json", governance)
    _write(
        output / "v5-selection.json",
        {
            "schema_version": "v5-gpt-direct-selection-2",
            "status": "PASS",
            "proposal": governance["final_proposal"],
            "claude_advice": governance["claude_advice"],
            "materialization": materialization,
            "selection_authority": "gpt-latest",
            "task_decomposition_authority": "gpt-latest",
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "local_task_classification_used": False,
            "local_atomic_work_generation_used": False,
            "local_resource_matrix_used": False,
            "local_scoring_used": False,
            "optimizer_used": False,
        },
    )
    _write(output / "v5-execution-graph.json", graph.to_dict())

    expert_config = RuntimeConfig(
        total_call_limit=expert_total_calls,
        recovery_call_limit=recovery_calls,
        cost_anomaly_usd=remaining_cost,
        quality_tier="value",
        tools_allowed=False,
        live_catalog_required=bool(args.require_live_catalog),
        provider_lock_required=True,
    )
    runtime = build_production_runtime(expert_config)
    result = runtime.execute_graph(
        graph,
        run,
        task,
        call_fn=expert_call_fn,
        output_dir=output,
        limits=graph_limits,
    )
    expert_cost = float(result.get("actual_cost_usd") or 0.0)
    expert_calls = int(
        result.get("execution_budget", {}).get("calls_reserved", 0)
    )
    governance_calls = int(
        governance_ledger.get("actual_governance_calls") or 0
    )
    result["governance"] = {
        "actual_calls": governance_calls,
        "reserved_calls": CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        "actual_cost_usd": governance_cost,
        "claude_calls": 1,
        "gpt_synthesis_calls": 1,
        "claude_is_advisory_only": True,
        "claude_gatekeeping_allowed": False,
        "claude_covers_internal_selection": True,
        "claude_covers_external_information": True,
        "second_claude_review_allowed": False,
    }
    result["total_model_calls"] = governance_calls + expert_calls
    result["approved_total_calls"] = total_calls
    result["expert_actual_cost_usd"] = expert_cost
    result["actual_cost_usd"] = round(governance_cost + expert_cost, 8)
    if result["total_model_calls"] > total_calls:
        raise RuntimeError("overall model-call ceiling exceeded")
    if (
        args.cost_anomaly_usd is not None
        and result["actual_cost_usd"]
        > float(args.cost_anomaly_usd) + 1e-12
    ):
        raise RuntimeError("overall cost guard exceeded")

    _write(output / "v5-result.json", result)
    _write(
        output / "v5-execution-summary.json",
        {
            key: value
            for key, value in result.items()
            if key != "node_results"
        },
    )
    _merge_request_audit(
        output,
        governance_ledger,
        approved_total_calls=total_calls,
    )
    write_manifest(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
