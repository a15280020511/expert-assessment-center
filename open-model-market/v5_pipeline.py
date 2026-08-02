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
    MINIMUM_EXPERT_COMPLETION_TOKENS,
    catalog_sha256,
    compact_endpoint_catalog,
    eligible_models,
)
from v5_claude_red_team_policy import CLAUDE_RED_TEAM_GOVERNANCE_CALLS
from v5_endpoint_catalog import fetch_live_endpoint_payloads
from v5_governance_catalog import (
    governance_candidate_models,
    resolve_live_governance_models,
    synthetic_governance_models,
)
from v5_governance_runtime import (
    run_single_pass_governance,
    write_governance_artifacts,
)
from v5_gpt_expert_selector import build_proposal_request
from v5_json_io import write_json
from v5_proposal_materializer import materialize_proposal
from v5_recovery_runtime import build_production_runtime
from v5_runtime import RuntimeConfig
from v5_task_envelope import build_task_envelope

RUNTIME_VERSION = "v5-gpt-claude-runtime-3"


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
            direct.update(
                forbidden.intersection(str(value) for value in recorded)
            )
        return direct

    status = (
        "PASS"
        if len(requests) <= approved_total_calls
        and all(not forbidden_fields(row) for row in requests)
        else "FAIL"
    )
    write_json(
        path,
        {
            "schema_version": "v5-complete-request-audit-3",
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


def _resolve_governance_models(
    *,
    models: Mapping[str, Any],
    run: Any,
    required_context_tokens: int,
    governance_call_fn: Any | None,
) -> tuple[dict[str, Any], str]:
    synthetic_allowed = bool(
        run.catalog_file
        and (run.dry_run or governance_call_fn is not None)
    )
    if synthetic_allowed:
        return synthetic_governance_models(), "synthetic-no-call-fixture"
    candidates = governance_candidate_models(models)
    payloads = fetch_live_endpoint_payloads(
        candidates,
        run,
        maximum_models=len(candidates),
    )
    resolved = resolve_live_governance_models(
        models,
        payloads,
        required_context_tokens=required_context_tokens,
    )
    return resolved, "openrouter-live-exact-direct-endpoints"


def _prepare_task(
    args: argparse.Namespace,
    run: Any,
    output: Path,
) -> tuple[str, str, Mapping[str, Any]]:
    task = str(args.task).strip()
    if not task:
        raise ValueError("task is empty")
    digest = sha256(task.encode("utf-8")).hexdigest()
    envelope = build_task_envelope(
        task,
        minimum_context_length=run.minimum_context_length,
        maximum_completion_tokens=run.max_completion_tokens,
    )
    write_json(output / "v5-task-envelope.json", envelope)
    write_json(output / "task-constraints.json", envelope["task_constraints"])
    return task, digest, envelope


def _endpoint_payload_source(
    args: argparse.Namespace,
    run: Any,
    ranked: Sequence[Mapping[str, Any]],
) -> tuple[Mapping[str, Any], str, bool]:
    if args.endpoint_file:
        return _load(args.endpoint_file), f"fixture:{args.endpoint_file}", False
    if run.dry_run and run.catalog_file:
        return {}, "synthetic-fixture-endpoints", True
    return (
        fetch_live_endpoint_payloads(ranked, run, maximum_models=len(ranked)),
        "openrouter-live-exact-endpoints",
        False,
    )


def _build_catalog_state(
    args: argparse.Namespace,
    run: Any,
    output: Path,
    task_envelope: Mapping[str, Any],
    governance_call_fn: Any | None,
) -> tuple[Mapping[str, Any], Mapping[str, Any], str, str]:
    required_context = int(task_envelope["required_context_tokens"])
    models, catalog_source = model_market.fetch_catalog(run)
    governance_models, governance_endpoint_source = _resolve_governance_models(
        models=models,
        run=run,
        required_context_tokens=required_context,
        governance_call_fn=governance_call_fn,
    )
    write_json(
        output / "v5-governance-models.json",
        {
            **governance_models,
            "catalog_source": catalog_source,
            "endpoint_source": governance_endpoint_source,
        },
    )
    ranked = eligible_models(
        models,
        requested_context=required_context,
        maximum_models=int(args.ranking_limit),
    )
    payloads, endpoint_source, synthetic = _endpoint_payload_source(args, run, ranked)
    catalog = compact_endpoint_catalog(
        ranked,
        payloads,
        allow_synthetic_fixture=synthetic,
        required_context_tokens=required_context,
        minimum_completion_tokens=MINIMUM_EXPERT_COMPLETION_TOKENS,
    )
    return catalog, governance_models, catalog_source, endpoint_source


def _catalog_snapshot(
    catalog: Mapping[str, Any],
    catalog_source: str,
    endpoint_source: str,
) -> dict[str, Any]:
    digest = catalog_sha256(catalog)
    return {
        "schema_version": "v5-gpt-catalog-snapshot-3",
        "catalog_snapshot_id": f"catalog-{digest[:20]}",
        "catalog_sha256": digest,
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


def _runtime_config_payload(
    args: argparse.Namespace,
    governance_models: Mapping[str, Any],
    total_calls: int,
    recovery_calls: int,
    expert_total_calls: int,
) -> dict[str, Any]:
    return {
        "runtime_version": RUNTIME_VERSION,
        "approved_total_calls": total_calls,
        "governance_calls_reserved": CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        "expert_total_call_limit": expert_total_calls,
        "expert_recovery_call_limit": recovery_calls,
        "expert_initial_call_limit": expert_total_calls - recovery_calls,
        "cost_anomaly_usd": args.cost_anomaly_usd,
        "selection_authority": "gpt-latest",
        "selection_model": governance_models["gpt"]["resolved_model"],
        "selection_provider": governance_models["gpt"]["provider"],
        "task_decomposition_authority": "gpt-latest",
        "red_team_role": "claude-opus-latest-advisory-once",
        "red_team_model": governance_models["claude"]["resolved_model"],
        "red_team_provider": governance_models["claude"]["provider"],
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
    }


def _write_catalog_artifacts(
    output: Path,
    catalog: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    runtime_config: Mapping[str, Any],
) -> None:
    write_json(output / "catalog-snapshot.json", snapshot)
    write_json(output / "v5-gpt-catalog-view.json", catalog)
    write_json(output / "v5-runtime-config.json", runtime_config)


def _write_dry_run(
    output: Path,
    *,
    task: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    governance_models: Mapping[str, Any],
    total_calls: int,
    recovery_calls: int,
    cost_anomaly_usd: float | None,
) -> None:
    proposal_request = build_proposal_request(
        task=task,
        task_envelope=task_envelope,
        catalog=catalog,
        approved_total_calls=total_calls,
        governance_calls_reserved=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        approved_recovery_calls=recovery_calls,
        cost_anomaly_usd=cost_anomaly_usd,
    )
    write_json(
        output / "v5-dry-run.json",
        {
            "schema_version": "v5-gpt-claude-advisory-dry-run-3",
            "status": "validated-not-executed",
            "model_calls": 0,
            "task_envelope": task_envelope,
            "proposal_request": proposal_request,
            "governance_model_resolution": governance_models,
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


def _remaining_cost(
    approved: float | None,
    governance_ledger: Mapping[str, Any],
) -> tuple[float, float | None]:
    governance_cost = float(governance_ledger.get("actual_cost_usd") or 0.0)
    remaining = None if approved is None else float(approved) - governance_cost
    if remaining is not None and remaining <= 0:
        raise RuntimeError("governance calls exhausted the approved cost guard")
    return governance_cost, remaining


def _selection_payload(
    governance: Mapping[str, Any],
    governance_models: Mapping[str, Any],
    materialization: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "v5-gpt-direct-selection-3",
        "status": "PASS",
        "proposal": governance["final_proposal"],
        "claude_advice": governance["claude_advice"],
        "governance_model_resolution": governance_models,
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
    }


def _run_governance_and_materialize(
    *,
    args: argparse.Namespace,
    run: Any,
    output: Path,
    task: str,
    task_digest: str,
    task_envelope: Mapping[str, Any],
    catalog: Mapping[str, Any],
    governance_models: Mapping[str, Any],
    total_calls: int,
    recovery_calls: int,
    governance_call_fn: Any | None,
) -> tuple[dict[str, Any], Mapping[str, Any], Any, Any, float, float | None]:
    _, _, governance, ledger = run_single_pass_governance(
        run=run,
        task=task,
        task_digest=task_digest,
        task_envelope=task_envelope,
        catalog=catalog,
        approved_total_calls=total_calls,
        governance_calls_reserved=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        approved_recovery_calls=recovery_calls,
        cost_anomaly_usd=args.cost_anomaly_usd,
        governance_models=governance_models,
        call_fn=governance_call_fn,
    )
    write_governance_artifacts(output, governance, ledger)
    governance_cost, remaining = _remaining_cost(args.cost_anomaly_usd, ledger)
    graph, limits, materialization = materialize_proposal(
        governance["final_proposal"],
        task,
        task_envelope,
        catalog,
        approved_total_calls=total_calls,
        governance_calls_reserved=CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        approved_recovery_calls=recovery_calls,
        cost_anomaly_usd=remaining,
    )
    governance["materialization_after_governance_cost"] = materialization
    write_json(output / "v5-governance-result.json", governance)
    write_json(output / "v5-selection.json", _selection_payload(governance, governance_models, materialization))
    write_json(output / "v5-execution-graph.json", graph.to_dict())
    return governance, ledger, graph, limits, governance_cost, remaining


def _governance_result_payload(
    ledger: Mapping[str, Any],
    governance_models: Mapping[str, Any],
    governance_cost: float,
) -> dict[str, Any]:
    return {
        "actual_calls": int(ledger.get("actual_governance_calls") or 0),
        "reserved_calls": CLAUDE_RED_TEAM_GOVERNANCE_CALLS,
        "actual_cost_usd": governance_cost,
        "gpt_model": governance_models["gpt"]["resolved_model"],
        "gpt_provider": governance_models["gpt"]["provider"],
        "claude_model": governance_models["claude"]["resolved_model"],
        "claude_provider": governance_models["claude"]["provider"],
        "claude_calls": 1,
        "gpt_synthesis_calls": 1,
        "claude_is_advisory_only": True,
        "claude_gatekeeping_allowed": False,
        "claude_covers_internal_selection": True,
        "claude_covers_external_information": True,
        "second_claude_review_allowed": False,
    }


def _finalize_result(
    result: dict[str, Any],
    *,
    args: argparse.Namespace,
    total_calls: int,
    governance_models: Mapping[str, Any],
    governance_ledger: Mapping[str, Any],
    governance_cost: float,
) -> None:
    expert_cost = float(result.get("actual_cost_usd") or 0.0)
    expert_calls = int(result.get("execution_budget", {}).get("calls_reserved", 0))
    governance = _governance_result_payload(
        governance_ledger,
        governance_models,
        governance_cost,
    )
    result["governance"] = governance
    result["total_model_calls"] = governance["actual_calls"] + expert_calls
    result["approved_total_calls"] = total_calls
    result["expert_actual_cost_usd"] = expert_cost
    result["actual_cost_usd"] = round(governance_cost + expert_cost, 8)
    if result["total_model_calls"] > total_calls:
        raise RuntimeError("overall model-call ceiling exceeded")
    if args.cost_anomaly_usd is not None and result["actual_cost_usd"] > float(args.cost_anomaly_usd) + 1e-12:
        raise RuntimeError("overall cost guard exceeded")


def _execute_experts(
    *,
    args: argparse.Namespace,
    run: Any,
    output: Path,
    task: str,
    graph: Any,
    graph_limits: Any,
    expert_total_calls: int,
    recovery_calls: int,
    remaining_cost: float | None,
    expert_call_fn: Any | None,
) -> dict[str, Any]:
    config = RuntimeConfig(
        total_call_limit=expert_total_calls,
        recovery_call_limit=recovery_calls,
        cost_anomaly_usd=remaining_cost,
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
        limits=graph_limits,
    )


def _write_final_artifacts(
    output: Path,
    result: Mapping[str, Any],
    governance_ledger: Mapping[str, Any],
    total_calls: int,
) -> None:
    write_json(output / "v5-result.json", result)
    write_json(
        output / "v5-execution-summary.json",
        {key: value for key, value in result.items() if key != "node_results"},
    )
    _merge_request_audit(output, governance_ledger, approved_total_calls=total_calls)
    write_manifest(output)


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
    task, task_digest, task_envelope = _prepare_task(args, run, output)
    catalog, governance_models, catalog_source, endpoint_source = _build_catalog_state(
        args, run, output, task_envelope, governance_call_fn
    )
    _write_catalog_artifacts(
        output,
        catalog,
        _catalog_snapshot(catalog, catalog_source, endpoint_source),
        _runtime_config_payload(
            args,
            governance_models,
            total_calls,
            recovery_calls,
            expert_total_calls,
        ),
    )
    if run.dry_run:
        _write_dry_run(
            output,
            task=task,
            task_envelope=task_envelope,
            catalog=catalog,
            governance_models=governance_models,
            total_calls=total_calls,
            recovery_calls=recovery_calls,
            cost_anomaly_usd=args.cost_anomaly_usd,
        )
        return 0
    _, ledger, graph, limits, governance_cost, remaining = _run_governance_and_materialize(
        args=args,
        run=run,
        output=output,
        task=task,
        task_digest=task_digest,
        task_envelope=task_envelope,
        catalog=catalog,
        governance_models=governance_models,
        total_calls=total_calls,
        recovery_calls=recovery_calls,
        governance_call_fn=governance_call_fn,
    )
    result = _execute_experts(
        args=args,
        run=run,
        output=output,
        task=task,
        graph=graph,
        graph_limits=limits,
        expert_total_calls=expert_total_calls,
        recovery_calls=recovery_calls,
        remaining_cost=remaining,
        expert_call_fn=expert_call_fn,
    )
    _finalize_result(
        result,
        args=args,
        total_calls=total_calls,
        governance_models=governance_models,
        governance_ledger=ledger,
        governance_cost=governance_cost,
    )
    _write_final_artifacts(output, result, ledger, total_calls)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
