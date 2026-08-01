#!/usr/bin/env python3
"""Explain deterministic V5 fixture infeasibility without model calls."""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market as market  # noqa: E402
import v5_pipeline  # noqa: E402
from resource_matrix import compile_v5_task_resources  # noqa: E402
from v5_general_task_planning import (  # noqa: E402
    classify_task,
    compile_task_semantics,
)
from v5_planning_diagnostics import build_infeasibility_report  # noqa: E402


def _serializable_limits(limits: Any) -> dict[str, Any]:
    """Serialize GraphLimits without assuming a repository-specific method."""
    if is_dataclass(limits):
        return asdict(limits)
    values = getattr(limits, "__dict__", None)
    if isinstance(values, Mapping):
        return dict(values)
    raise TypeError("GraphLimits is not serializable by the diagnostic tool")


def _work_candidate_summary(
    bundle: Mapping[str, Any],
) -> list[dict[str, Any]]:
    candidates = [
        row
        for row in bundle.get("candidates", [])
        if isinstance(row, Mapping)
    ]
    interpretations = bundle.get("interpretations", {})
    interpretations = (
        interpretations if isinstance(interpretations, Mapping) else {}
    )
    calibration_root = bundle.get("hard_capability_calibration", {})
    calibration_root = (
        calibration_root if isinstance(calibration_root, Mapping) else {}
    )
    calibration_interpretations = calibration_root.get(
        "interpretations",
        {},
    )
    calibration_interpretations = (
        calibration_interpretations
        if isinstance(calibration_interpretations, Mapping)
        else {}
    )

    rows: list[dict[str, Any]] = []
    for interpretation_id, metadata in interpretations.items():
        if not isinstance(metadata, Mapping):
            continue
        interpretation_calibration = calibration_interpretations.get(
            interpretation_id,
            {},
        )
        interpretation_calibration = (
            interpretation_calibration
            if isinstance(interpretation_calibration, Mapping)
            else {}
        )
        calibrations = interpretation_calibration.get(
            "work_calibrations",
            [],
        )
        calibration_by_work = {
            str(row.get("work_id") or ""): row
            for row in calibrations
            if isinstance(row, Mapping)
        }
        for work_id, copies_raw in dict(
            metadata.get("copies_by_work", {})
        ).items():
            companies: set[str] = set()
            models: set[str] = set()
            coverage_counts: dict[str, int] = {}
            for copy_index in range(int(copies_raw)):
                key = f"{work_id}#{copy_index}"
                matching = [
                    row
                    for row in candidates
                    if str(row.get("interpretation_id") or "")
                    == str(interpretation_id)
                    and key in row.get("coverage_keys", [])
                ]
                coverage_counts[key] = len(matching)
                for candidate in matching:
                    model_id = str(candidate.get("model") or "")
                    models.add(model_id)
                    companies.add(model_id.split("/", 1)[0])
            rows.append(
                {
                    "interpretation_id": str(interpretation_id),
                    "work_id": str(work_id),
                    "copies": int(copies_raw),
                    "coverage_candidate_counts": coverage_counts,
                    "candidate_model_count": len(models),
                    "candidate_company_count": len(companies),
                    "candidate_companies": sorted(companies),
                    "calibration": calibration_by_work.get(
                        str(work_id),
                        {},
                    ),
                }
            )
    return rows


def _write_result(output: Path, result: Mapping[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "fixture-planning-diagnostics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def diagnose(task: str, output: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "task": task,
        "status": "failed",
        "failed_stage": "argument-construction",
        "stage_error": None,
        "traceback": None,
        "profile": None,
        "resource_shape": None,
        "limits": None,
        "ranked_model_count": 0,
        "market_company_count": 0,
        "candidate_count": 0,
        "work_candidate_summary": [],
        "optimizer_error": None,
        "optimizer_status": None,
        "infeasibility_report": None,
        "model_calls_performed": 0,
        "model_cost_usd": 0,
        "cross_task_history_used": False,
    }
    args = None
    run = None
    runtime = None
    profile = None
    resources = None
    shape = None
    limits = None
    ranked = []
    compiled_market: Mapping[str, Any] = {}
    candidate_graph: Mapping[str, Any] = {}
    optimization: Mapping[str, Any] | None = None

    try:
        args = v5_pipeline.build_parser().parse_args(
            [
                "--task",
                task,
                "--catalog-file",
                "tests/fixtures/models.json",
                "--endpoint-file",
                "tests/fixtures/endpoints.json",
                "--dry-run",
                "--maximum-total-calls",
                "16",
                "--maximum-recovery-calls",
                "2",
                "--cost-anomaly-usd",
                "0.25",
                "--quality-tier",
                "value",
                "--output-dir",
                str(output),
            ]
        )

        result["failed_stage"] = "runtime-construction"
        run = market.build_run_config(args)
        runtime = v5_pipeline._runtime_from_args(args, run)

        result["failed_stage"] = "task-classification"
        profile = classify_task(run.task, run)
        result["profile"] = {
            "complexity": profile.complexity,
            "complexity_score": profile.complexity_score,
            "high_stakes": profile.high_stakes,
            "long_context": profile.long_context,
            "requested_context": profile.requested_context,
        }

        result["failed_stage"] = "catalog-ranking"
        models, catalog_source = market.fetch_catalog(run)
        ranked = v5_pipeline._rank_v5_models(models, profile, run)
        result["ranked_model_count"] = len(ranked)

        result["failed_stage"] = "resource-compilation"
        resources = compile_v5_task_resources(
            profile,
            run,
            semantic_compiler=compile_task_semantics,
        )
        shape = v5_pipeline._resource_shape(resources)
        result["resource_shape"] = shape

        result["failed_stage"] = "limit-compilation"
        limits = v5_pipeline._planning_limits(
            total_calls=runtime.config.total_call_limit,
            recovery_calls=runtime.config.recovery_call_limit,
            planning_nodes=runtime.config.initial_call_limit,
            anomaly_budget=runtime.config.cost_anomaly_usd,
            runtime=runtime,
            task=run.task,
            profile=profile,
            resource_shape=shape,
        )
        result["limits"] = _serializable_limits(limits)

        result["failed_stage"] = "endpoint-snapshot"
        endpoint_payloads = v5_pipeline._load_json(args.endpoint_file)
        snapshot = runtime.build_catalog_snapshot(
            ranked,
            endpoint_payloads,
            catalog_source=catalog_source,
            endpoint_source=f"fixture:{args.endpoint_file}",
        )

        result["failed_stage"] = "market-compilation"
        compiled_market = runtime.planner_policy.compile_market(
            ranked,
            resources,
            endpoint_payloads=snapshot.endpoint_payloads,
            ranking_limit=len(ranked),
            allow_synthetic_fixture=False,
        )
        result["market_company_count"] = compiled_market.get(
            "model_company_count",
            0,
        )

        result["failed_stage"] = "candidate-generation"
        candidate_graph = runtime.planner_policy.generate_candidate_graph(
            resources,
            compiled_market,
            maximum_per_group=runtime.config.maximum_candidates_per_work,
        )
        result["candidate_count"] = len(
            candidate_graph.get("candidates", [])
        )
        result["work_candidate_summary"] = _work_candidate_summary(
            candidate_graph
        )

        result["failed_stage"] = "joint-optimization"
        optimization = runtime.planner_policy.optimize_execution_graph(
            candidate_graph,
            limits=limits,
            quality_tolerance_pct=2.0,
            solver_timeout_seconds=runtime.config.solver_timeout_seconds,
        )
        result["optimizer_status"] = optimization.get("solver_status")
        result["status"] = "feasible"
        result["failed_stage"] = None
    except Exception as exc:  # noqa: BLE001 - diagnostic must persist all failures
        result["stage_error"] = str(exc)
        result["optimizer_error"] = (
            str(exc)
            if result.get("failed_stage") == "joint-optimization"
            else None
        )
        result["traceback"] = traceback.format_exc()
        if candidate_graph and limits is not None:
            try:
                result["infeasibility_report"] = build_infeasibility_report(
                    candidate_graph,
                    limits,
                    message=str(exc),
                )
            except Exception as report_exc:  # noqa: BLE001
                result["infeasibility_report"] = {
                    "status": "diagnostic-report-failed",
                    "message": str(report_exc),
                }
    finally:
        if candidate_graph:
            result["candidate_count"] = len(
                candidate_graph.get("candidates", [])
            )
            result["work_candidate_summary"] = _work_candidate_summary(
                candidate_graph
            )
        if compiled_market:
            result["market_company_count"] = compiled_market.get(
                "model_company_count",
                result["market_company_count"],
            )
        if ranked:
            result["ranked_model_count"] = len(ranked)
        _write_result(output, result)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = diagnose(args.task, Path(args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["status"] == "feasible" else 1


if __name__ == "__main__":
    raise SystemExit(main())
