#!/usr/bin/env python3
"""V5-only operational micro canary under a one-cent hard cost envelope.

The canary validates live catalog -> concrete Provider Endpoints -> calibrated
candidate graph -> CP-SAT selection -> real DAG execution. It deliberately does
not execute V3, does not compare quality, and can never authorize production
cutover. Canary-only capability neutralization treats missing catalog keywords as
unknown at the existing 0.48 evidence floor; this policy never enters production.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Sequence

import model_market as market
import v5_candidate_diversity
import v5_executor
import v5_live_benchmark as base
import v5_planner
import v5_value_optimizer
from artifact_manifest import write_manifest
from execution_graph import ExecutionGraph, GraphLimits

DEFAULT_TASK_ID = "software-job-runner-security"
MAX_COST_USD = 0.01
MAX_CALLS = 8
MAX_NODES = 8
OUTPUT_ALLOWANCE = 600
MAX_PROMPT_PPM = 0.40
MAX_COMPLETION_PPM = 1.00
MIN_RELIABILITY = 0.80
CANARY_CAPABILITY_FLOOR = 0.48


class CanaryError(RuntimeError):
    pass


def _write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={str(value).replace(chr(10), ' ').replace(chr(13), ' ')}\n")


def prepare(event_path: str | Path, output_dir: str | Path) -> int:
    event = base._load_json(event_path)
    issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
    body = str(issue.get("body") or "").strip()
    raw: Mapping[str, Any] = {}
    if body:
        parsed = json.loads(body)
        if not isinstance(parsed, Mapping):
            raise CanaryError("Issue body must be one JSON object")
        raw = parsed
    allowed = {"canary_id", "task_id"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise CanaryError(f"Unknown canary fields: {unknown}")

    suite = base._load_json(base.DEFAULT_SUITE)
    available = {
        str(row.get("task_id"))
        for row in suite.get("tasks", [])
        if isinstance(row, Mapping) and row.get("task_id")
    }
    task_id = str(raw.get("task_id") or DEFAULT_TASK_ID)
    if task_id not in available:
        raise CanaryError(f"Unknown task_id: {task_id}")

    config = {
        "version": 1,
        "mode": "v5-operational-micro-canary",
        "canary_id": str(raw.get("canary_id") or "v5-micro-canary-20260730"),
        "task_id": task_id,
        "max_actual_cost_usd": MAX_COST_USD,
        "max_calls": MAX_CALLS,
        "max_nodes": MAX_NODES,
        "output_allowance_tokens": OUTPUT_ALLOWANCE,
        "endpoint_caps": {
            "prompt_usd_per_million": MAX_PROMPT_PPM,
            "completion_usd_per_million": MAX_COMPLETION_PPM,
            "minimum_reliability": MIN_RELIABILITY,
        },
        "production_cutover_eligible": False,
        "production_entrypoint_changed": False,
        "v3_executed": False,
        "v3_deleted": False,
        "issue_number": int(issue.get("number") or 0),
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "v5-micro-canary-config.json", config)
    for key in ("canary_id", "task_id", "max_actual_cost_usd", "max_calls", "output_allowance_tokens"):
        _write_output(key, config[key])
    return 0


def _reasoning_effort(model: Any) -> str:
    reasoning = dict(getattr(model, "reasoning", {}) or {})
    if not bool(reasoning.get("mandatory")):
        return "none"
    supported = [str(value) for value in reasoning.get("supported_efforts", []) if str(value)]
    if "minimal" in supported or not supported:
        return "minimal"
    return supported[-1]


def _canary_models(ranked: Sequence[Any]) -> tuple[list[Any], dict[str, str]]:
    selected = list(ranked)
    efforts = {str(model.id): _reasoning_effort(model) for model in selected}
    return selected, efforts


def _filter_and_neutralize_market(raw: Mapping[str, Any]) -> dict[str, Any]:
    source = [row for row in raw.get("endpoints", []) if isinstance(row, Mapping)]
    kept: list[dict[str, Any]] = []
    rejected = list(raw.get("rejected", []) or [])
    for row in source:
        parameters = {str(value).casefold() for value in row.get("supported_parameters", [])}
        eligible = bool(
            float(row.get("prompt_price_per_million", math.inf)) <= MAX_PROMPT_PPM
            and float(row.get("completion_price_per_million", math.inf)) <= MAX_COMPLETION_PPM
            and float(row.get("reliability", 0.0)) >= MIN_RELIABILITY
            and not bool(row.get("synthetic_fixture_only"))
            and "max_tokens" in parameters
            and "reasoning" in parameters
        )
        if not eligible:
            rejected.append({
                "model": str(row.get("model_id") or ""),
                "provider": str(row.get("provider_slug") or ""),
                "endpoint_id": str(row.get("endpoint_id") or ""),
                "reason": "outside-micro-canary-price-reliability-or-parameter-cap",
            })
            continue
        normalized = dict(row)
        original = dict(row.get("capability_scores", {}) or {})
        normalized["capability_scores"] = {
            str(label): round(max(CANARY_CAPABILITY_FLOOR, float(score)), 6)
            for label, score in original.items()
        }
        normalized["canary_capability_neutralization"] = {
            "floor": CANARY_CAPABILITY_FLOOR,
            "production_policy_changed": False,
            "purpose": "operational-canary-only-missing-catalog-evidence-neutralization",
        }
        kept.append(normalized)
    if len({str(row.get("model_id")) for row in kept}) < 2:
        raise CanaryError("Fewer than two cheap real models satisfy micro-canary endpoint caps")
    result = dict(raw)
    result.update({
        "endpoints": kept,
        "endpoint_count": len(kept),
        "real_endpoint_count": len(kept),
        "synthetic_fixture_count": 0,
        "rejected": rejected,
        "micro_canary_market_policy": {
            "prompt_usd_per_million_max": MAX_PROMPT_PPM,
            "completion_usd_per_million_max": MAX_COMPLETION_PPM,
            "minimum_reliability": MIN_RELIABILITY,
            "required_parameters": ["max_tokens", "reasoning"],
            "capability_neutralization_floor": CANARY_CAPABILITY_FLOOR,
            "production_policy_changed": False,
        },
    })
    return result


def _bound_output_requirements(resources: Mapping[str, Any]) -> dict[str, Any]:
    bounded = json.loads(json.dumps(resources, ensure_ascii=False))
    for interpretation in bounded.get("task_semantics", {}).get("interpretations", []):
        for work in interpretation.get("atomic_work", []):
            context = work.get("context_requirements")
            if isinstance(context, dict):
                context["expected_output_tokens"] = min(
                    OUTPUT_ALLOWANCE,
                    int(context.get("expected_output_tokens", OUTPUT_ALLOWANCE) or OUTPUT_ALLOWANCE),
                )
    bounded["micro_canary_output_policy"] = {
        "maximum_completion_tokens": OUTPUT_ALLOWANCE,
        "reasoning_disabled_when_optional": True,
        "production_policy_changed": False,
    }
    return bounded


def _install_canary_payload(efforts: Mapping[str, str]):
    original = v5_executor.build_node_payload

    def canary_payload(node: Any, original_task: str, upstream: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        payload = original(node, original_task, upstream)
        payload["max_tokens"] = OUTPUT_ALLOWANCE
        payload["reasoning"] = {
            "effort": str(efforts.get(str(node.model), "none")),
            "exclude": True,
        }
        provider = payload.get("provider")
        if isinstance(provider, dict):
            provider["require_parameters"] = True
        return payload

    v5_executor.build_node_payload = canary_payload
    return original


def _summary(result: Mapping[str, Any]) -> str:
    return "\n".join([
        "# V5 Operational Micro Canary",
        "",
        f"- Status: `{result.get('status')}`",
        f"- Canary passed: `{str(bool(result.get('canary_passed'))).lower()}`",
        f"- Task: `{result.get('task_id')}`",
        f"- Calls: `{result.get('model_calls', 0)}` / `{MAX_CALLS}`",
        f"- Actual cost: `${float(result.get('actual_cost_usd', 0.0)):.6f}` / `${MAX_COST_USD:.2f}`",
        f"- Selected nodes: `{result.get('selected_node_count', 0)}`",
        f"- Final answer chars: `{result.get('final_answer_chars', 0)}`",
        "- V3 executed: `false`",
        "- Production cutover allowed: `false`",
        "- Production entrypoint changed: `false`",
        "- V3 deleted: `false`",
        "",
        "This canary is operational evidence only. It cannot authorize V5 production cutover.",
    ]).rstrip() + "\n"


def run(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise CanaryError("OPENROUTER_API_KEY is not set")
    config = base._load_json(config_path)
    suite = base._load_json(suite_path)
    task = next(
        (
            row for row in suite.get("tasks", [])
            if isinstance(row, Mapping) and str(row.get("task_id")) == str(config["task_id"])
        ),
        None,
    )
    if task is None:
        raise CanaryError("Configured canary task is absent from suite")

    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    task_root = root / "v5"
    task_root.mkdir(parents=True, exist_ok=True)
    run_config = market.build_run_config(
        base._namespace(base._task_text(task), task_root, ranking_limit=50)
    )
    run_config = replace(
        run_config,
        model_max_retries=0,
        maximum_replacements=0,
        parallel_workers=1,
    )
    profile = market.classify_task(run_config.task, run_config)
    models, catalog_source = market.fetch_catalog(run_config)
    ranked, efforts = _canary_models(base._rank_v5_models(models, profile, run_config))
    scoped = ranked[:24]
    payloads = v5_planner.fetch_live_endpoint_payloads(scoped, run_config, maximum_models=24)

    v5_candidate_diversity.install()
    resources = _bound_output_requirements(base.compile_v5_task_resources(profile, run_config))
    base.write_task_resource_artifacts(resources, task_root)
    raw_market = v5_planner.compile_model_endpoint_market(
        scoped,
        resources,
        endpoint_payloads=payloads,
        ranking_limit=24,
        allow_synthetic_fixture=False,
    )
    canary_market = _filter_and_neutralize_market(raw_market)
    candidates = v5_planner.generate_candidate_graph(
        resources,
        canary_market,
        maximum_per_group=12,
    )
    limits = GraphLimits(
        max_nodes=MAX_NODES,
        max_edges=32,
        max_stages=8,
        max_model_calls=MAX_CALLS,
        max_retries=0,
        max_replacements=0,
        max_budget_usd=MAX_COST_USD,
    )
    optimized = v5_value_optimizer.optimize_execution_graph(
        candidates,
        limits=limits,
        solver_timeout_seconds=20.0,
    )
    graph = ExecutionGraph.from_mapping(optimized["execution_graph"])
    if len(graph.nodes) > MAX_NODES or graph.estimated_total_cost > MAX_COST_USD + 1e-12:
        raise CanaryError("Selected canary graph exceeds hard node or estimated-cost ceiling")

    _write_json(task_root / "v5-model-endpoint-market.json", canary_market)
    _write_json(task_root / "v5-candidate-graph.json", candidates)
    _write_json(task_root / "v5-optimization.json", optimized)
    _write_json(task_root / "v5-execution-graph.json", graph.to_dict())
    _write_json(task_root / "v5-micro-canary-reasoning-policy.json", {
        "model_efforts": efforts,
        "optional_reasoning_effort": "none",
        "mandatory_reasoning_default": "minimal-or-lowest-supported",
        "output_allowance_tokens": OUTPUT_ALLOWANCE,
    })

    original_payload = _install_canary_payload(efforts)
    execution: Mapping[str, Any] = {}
    error: str | None = None
    try:
        execution = v5_executor.execute_v5_graph(
            graph,
            run_config,
            run_config.task,
            output_dir=task_root,
            limits=limits,
        )
    except Exception as exc:  # noqa: BLE001
        error = str(exc)
        summary_path = task_root / "v5-execution-summary.json"
        if summary_path.exists():
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            execution = loaded if isinstance(loaded, Mapping) else {}
    finally:
        v5_executor.build_node_payload = original_payload

    audit_path = task_root / "v5-request-audit.json"
    audit = json.loads(audit_path.read_text(encoding="utf-8")) if audit_path.exists() else {}
    actual_cost = float(execution.get("actual_cost_usd", 0.0) or 0.0)
    model_calls = int(audit.get("request_count", 0) or 0)
    final_answer = str(execution.get("final_answer") or "")
    passed = bool(
        execution.get("status") == "success"
        and actual_cost <= MAX_COST_USD + 1e-12
        and model_calls <= MAX_CALLS
        and len(final_answer) >= 320
        and audit.get("status") == "PASS"
    )
    result = {
        "version": 1,
        "mode": "v5-operational-micro-canary",
        "canary_id": config["canary_id"],
        "task_id": config["task_id"],
        "status": "passed" if passed else "failed",
        "canary_passed": passed,
        "error": error,
        "catalog_source": catalog_source,
        "selected_node_count": len(graph.nodes),
        "selected_models": sorted({node.model for node in graph.nodes}),
        "selected_provider_endpoints": sorted({node.provider_endpoint for node in graph.nodes}),
        "estimated_total_cost_usd": round(float(graph.estimated_total_cost), 8),
        "actual_cost_usd": round(actual_cost, 8),
        "model_calls": model_calls,
        "final_answer_chars": len(final_answer),
        "execution": dict(execution),
        "request_audit": audit,
        "v3_executed": False,
        "production_cutover_allowed": False,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
        "canary_only_capability_neutralization": True,
    }
    _write_json(root / "v5-micro-canary-result.json", result)
    (root / "v5-micro-canary-summary.md").write_text(_summary(result), encoding="utf-8")
    write_manifest(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if passed else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V5 operational micro canary")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--event-path", required=True)
    prep.add_argument("--output-dir", required=True)
    execute = sub.add_parser("run")
    execute.add_argument("--config", required=True)
    execute.add_argument("--suite", required=True)
    execute.add_argument("--output-dir", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv if argv is not None else sys.argv[1:]))
    try:
        if args.command == "prepare":
            return prepare(args.event_path, args.output_dir)
        return run(args.config, args.suite, args.output_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
