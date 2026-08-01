#!/usr/bin/env python3
"""Explain deterministic V5 fixture infeasibility without model calls."""
from __future__ import annotations

import argparse
import json
import sys
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
    rows: list[dict[str, Any]] = []
    for interpretation_id, metadata in interpretations.items():
        if not isinstance(metadata, Mapping):
            continue
        calibrations = (
            bundle.get("hard_capability_calibration", {})
            .get(interpretation_id, {})
            .get("work_calibrations", [])
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
                    "calibration": calibration_by_work.get(
                        str(work_id),
                        {},
                    ),
                }
            )
    return rows


def diagnose(task: str, output: Path) -> dict[str, Any]:
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
    run = market.build_run_config(args)
    runtime = v5_pipeline._runtime_from_args(args, run)
    profile = classify_task(run.task, run)
    models, catalog_source = market.fetch_catalog(run)
    ranked = v5_pipeline._rank_v5_models(models, profile, run)
    resources = compile_v5_task_resources(
        profile,
        run,
        semantic_compiler=compile_task_semantics,
    )
    shape = v5_pipeline._resource_shape(resources)
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
    endpoint_payloads = v5_pipeline._load_json(args.endpoint_file)
    snapshot = runtime.build_catalog_snapshot(
        ranked,
        endpoint_payloads,
        catalog_source=catalog_source,
        endpoint_source=f"fixture:{args.endpoint_file}",
    )
    compiled_market = runtime.planner_policy.compile_market(
        ranked,
        resources,
        endpoint_payloads=snapshot.endpoint_payloads,
        ranking_limit=len(ranked),
        allow_synthetic_fixture=False,
    )
    candidate_graph = runtime.planner_policy.generate_candidate_graph(
        resources,
        compiled_market,
        maximum_per_group=runtime.config.maximum_candidates_per_work,
    )
    optimizer_error = None
    optimization = None
    try:
        optimization = runtime.planner_policy.optimize_execution_graph(
            candidate_graph,
            limits=limits,
            quality_tolerance_pct=2.0,
            solver_timeout_seconds=runtime.config.solver_timeout_seconds,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostic output
        optimizer_error = str(exc)

    report = build_infeasibility_report(
        candidate_graph,
        limits,
        message=optimizer_error or "optimizer returned a feasible graph",
    )
    result = {
        "task": task,
        "profile": {
            "complexity": profile.complexity,
            "complexity_score": profile.complexity_score,
            "high_stakes": profile.high_stakes,
            "long_context": profile.long_context,
            "requested_context": profile.requested_context,
        },
        "resource_shape": shape,
        "limits": limits.to_dict(),
        "ranked_model_count": len(ranked),
        "market_company_count": compiled_market.get("model_company_count"),
        "candidate_count": len(candidate_graph.get("candidates", [])),
        "work_candidate_summary": _work_candidate_summary(candidate_graph),
        "optimizer_error": optimizer_error,
        "optimizer_status": (
            optimization.get("solver_status")
            if isinstance(optimization, Mapping)
            else None
        ),
        "infeasibility_report": report,
        "model_calls_performed": 0,
        "model_cost_usd": 0,
    }
    output.mkdir(parents=True, exist_ok=True)
    (output / "fixture-planning-diagnostics.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    result = diagnose(args.task, Path(args.output_dir))
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["optimizer_error"] is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
