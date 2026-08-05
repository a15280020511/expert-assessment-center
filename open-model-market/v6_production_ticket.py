#!/usr/bin/env python3
"""Execute one governance-signed V6 expert roster without GPT/Claude planning."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence

from model_market import RunConfig, fetch_catalog
from v5_json_io import load_json_or_default, write_json
from v5_recovery_runtime import build_production_runtime
from v5_runtime import RuntimeConfig
from v5_task_constraints import compile_task_constraints
from v5_task_envelope import build_task_envelope
from v6_governed_roster import (
    RUNTIME_VERSION,
    materialize_execution_graph,
    resolve_exact_endpoints,
    validate_governed_ticket,
)
from v6_run_evidence import build_v6_evidence


def _canonical_user_task(root: Path, fallback: str) -> tuple[str, str]:
    packet = load_json_or_default(root / "ticket.json", {})
    task = packet.get("task") if isinstance(packet, Mapping) else None
    if not isinstance(task, Mapping) or not str(task.get("question") or "").strip():
        return str(fallback).strip(), "fallback-cli-task"
    sections = [str(task["question"]).strip()]
    requirements = task.get("requirements")
    if isinstance(requirements, list):
        rows = [str(value).strip() for value in requirements if str(value).strip()]
        if rows:
            sections.append("执行要求：\n" + "\n".join(f"- {row}" for row in rows))
    language = str(task.get("language") or "").strip()
    if language:
        sections.append(f"输出语言：{language}")
    return "\n\n".join(sections), "ticket.task"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--maximum-total-calls", type=int, required=True)
    parser.add_argument("--maximum-recovery-calls", type=int, required=True)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument("--require-live-catalog", action="store_true")
    return parser


def _run_config(task: str, root: Path) -> RunConfig:
    api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return RunConfig(
        api_key=api_key,
        task=task,
        output_dir=root,
        catalog_file=None,
        catalog_sorts=("intelligence-high-to-low", "pricing-low-to-high"),
        ranking_limit=150,
        min_context_length=8_192,
        max_completion_tokens=2_000,
        cost_anomaly_usd=None,
        catalog_timeout_seconds=int(os.getenv("CATALOG_TIMEOUT_SECONDS", "30")),
        model_timeout_seconds=int(os.getenv("MODEL_TIMEOUT_SECONDS", "240")),
        catalog_max_retries=int(os.getenv("CATALOG_MAX_RETRIES", "1")),
        model_max_retries=0,
        parallel_workers=max(1, int(os.getenv("PARALLEL_WORKERS", "4"))),
        http_referer=os.getenv("OPENROUTER_SITE_URL", ""),
        app_title=os.getenv(
            "OPENROUTER_APP_NAME",
            "self-managed-governed-expert-team-v6",
        ),
    )


def _serializable_validation(validation: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "v6-governed-ticket-validation-receipt-1",
        "status": "PASS",
        "runtime_version": RUNTIME_VERSION,
        "governance_roster_sha256": validation["governance_roster"]["roster_sha256"],
        "governance_commit_sha": validation["governance_roster"]["governance_commit_sha"],
        "team_plan_sha256": validation["governance_roster"]["team_plan_sha256"],
        "team_size": len(validation["primary_members"]),
        "recovery_size": len(validation["recovery_members"]),
        "approved_total_calls": validation["approved_total_calls"],
        "approved_recovery_calls": validation["approved_recovery_calls"],
        "final_work_id": validation["final_work_id"],
        "primary_members": list(validation["primary_members"]),
        "recovery_members": list(validation["recovery_members"]),
        "all_companies_unique": True,
        "claude_mechanism_enabled": False,
        "claude_calls": 0,
        "gpt_planning_calls": 0,
        "gpt_synthesis_calls": 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    if not 2 <= args.maximum_total_calls <= 12:
        raise ValueError("maximum-total-calls must be between 2 and 12")
    if not 0 <= args.maximum_recovery_calls < args.maximum_total_calls:
        raise ValueError("recovery reserve must leave at least one primary call")
    task, task_source = _canonical_user_task(root, args.task)
    if not task:
        raise ValueError("canonical task is empty")
    packet = load_json_or_default(root / "ticket.json", {})
    if not isinstance(packet, Mapping):
        raise ValueError("ticket.json is missing or invalid")

    try:
        validation = validate_governed_ticket(packet)
        if validation["approved_total_calls"] != args.maximum_total_calls:
            raise ValueError("CLI total calls do not match governance roster")
        if validation["approved_recovery_calls"] != args.maximum_recovery_calls:
            raise ValueError("CLI recovery calls do not match governance roster")
        validation_receipt = _serializable_validation(validation)
        write_json(root / "v6-roster-validation.json", validation_receipt)

        roster_profile = validation["governance_roster"]["task_cost_profile"]
        completion_tokens = int(
            roster_profile["expected_completion_tokens_per_call"]
        )
        task_envelope = build_task_envelope(
            task,
            minimum_context_length=8_192,
            maximum_completion_tokens=completion_tokens,
        )
        task_envelope.update(
            {
                "schema_version": "v6-governed-task-envelope-1",
                "decomposition_authority": "governance-signed-team-plan",
                "local_task_classification_used": False,
                "local_atomic_work_generation_used": False,
                "gpt_planning_calls": 0,
                "claude_calls": 0,
            }
        )
        write_json(root / "v6-task-envelope.json", task_envelope)
        constraints = compile_task_constraints(task)
        write_json(root / "task-constraints.json", constraints.to_dict())
        write_json(
            root / "planning-task.json",
            {
                "schema_version": "v6-governed-planning-task-1",
                "source": task_source,
                "sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
                "characters": len(task),
                "task_constraints": constraints.to_dict(),
                "decomposition_authority": "web-gpt-via-governance-ticket",
                "selection_authority": "decision-system-governance",
                "orchestration_authority": "networkx-deterministic-dag",
                "claude_mechanism_enabled": False,
                "claude_calls": 0,
                "gpt_planning_calls": 0,
                "gpt_synthesis_calls": 0,
                "local_scoring_used": False,
                "optimizer_used": False,
            },
        )

        run = _run_config(task, root)
        models, catalog_source = fetch_catalog(run)
        chosen, endpoint_catalog, endpoint_payloads = resolve_exact_endpoints(
            validation,
            models,
            run,
            task_envelope,
        )
        write_json(root / "v6-exact-endpoint-catalog.json", endpoint_catalog)
        graph, limits, materialization = materialize_execution_graph(
            packet,
            task,
            validation,
            chosen,
        )
        write_json(root / "v6-networkx-materialization.json", materialization)
        write_json(root / "v5-execution-graph.json", graph.to_dict())
        write_json(
            root / "v5-selection.json",
            {
                "schema_version": "v6-governed-selection-1",
                "status": "PASS",
                "runtime_version": RUNTIME_VERSION,
                "selection_authority": "decision-system-governance",
                "governance_roster_sha256": validation_receipt[
                    "governance_roster_sha256"
                ],
                "networkx_materialization": materialization,
                "optimizer_used": False,
                "local_scoring_used": False,
                "claude_calls": 0,
                "gpt_planning_calls": 0,
                "gpt_synthesis_calls": 0,
            },
        )

        runtime_config = RuntimeConfig(
            total_call_limit=args.maximum_total_calls,
            recovery_call_limit=args.maximum_recovery_calls,
            cost_anomaly_usd=args.cost_anomaly_usd,
            tools_allowed=False,
            live_catalog_required=bool(args.require_live_catalog),
            provider_lock_required=True,
        )
        runtime = build_production_runtime(runtime_config)
        write_json(
            root / "v5-runtime-config.json",
            {
                **runtime.describe(),
                "runtime_version": RUNTIME_VERSION,
                "claude_mechanism_enabled": False,
                "governance_model_calls": 0,
            },
        )
        selected_models = [
            models[model_id]
            for model_id in [
                *(row["model_id"] for row in validation["primary_members"]),
                *(row["model_id"] for row in validation["recovery_members"]),
            ]
        ]
        snapshot = runtime.build_catalog_snapshot(
            selected_models,
            endpoint_payloads,
            catalog_source=catalog_source,
            endpoint_source="openrouter-live-zdr-fixed-roster-models",
        )
        write_json(root / "catalog-snapshot.json", snapshot.to_dict())
        write_json(
            root / "production-runtime.json",
            {
                "runtime_version": RUNTIME_VERSION,
                "entrypoint": "v6_production_ticket.py",
                "architecture": (
                    "governance-signed-roster -> exact-zdr-endpoints -> "
                    "networkx-topological-generations -> expert-runtime"
                ),
                "maximum_total_calls": args.maximum_total_calls,
                "maximum_recovery_calls": args.maximum_recovery_calls,
                "governance_calls_reserved": 0,
                "claude_mechanism_enabled": False,
                "model_loop_allowed": False,
                "provider_fallback_allowed": False,
                "cross_task_history_used": False,
            },
        )

        execution = runtime.execute_graph(
            graph,
            run,
            task,
            output_dir=root,
            limits=limits,
        )
        result = build_v6_evidence(
            root,
            maximum_total_calls=args.maximum_total_calls,
            maximum_recovery_calls=args.maximum_recovery_calls,
            cost_anomaly_usd=args.cost_anomaly_usd,
            require_report=True,
        )
        if result.get("status") != "success":
            raise RuntimeError("V6 production result failed delivery gate")
        print(
            json.dumps(
                {
                    "runtime_version": RUNTIME_VERSION,
                    "status": result["status"],
                    "completion_mode": result["completion_mode"],
                    "actual_cost_usd": result["actual_cost_usd"],
                    "node_count": result["node_count"],
                    "expert_model_calls": result["expert_call_count"],
                    "governance_model_calls": 0,
                    "claude_calls": 0,
                    "governance_roster_sha256": result[
                        "governance_roster_sha256"
                    ],
                    "execution_status": execution.get("status"),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        normalization_error = None
        try:
            build_v6_evidence(
                root,
                maximum_total_calls=args.maximum_total_calls,
                maximum_recovery_calls=args.maximum_recovery_calls,
                cost_anomaly_usd=args.cost_anomaly_usd,
                require_report=False,
            )
        except Exception as normalize_exc:  # noqa: BLE001
            normalization_error = str(normalize_exc)
        write_json(
            root / "expert-team-error.json",
            {
                "version": 6,
                "runtime_version": RUNTIME_VERSION,
                "error_code": "V6_GOVERNED_ROSTER_EXECUTION_FAILED",
                "stage": "v6-governed-roster-networkx-runtime",
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "normalization_error": normalization_error,
                "fallback_used": False,
                "claude_mechanism_enabled": False,
                "claude_calls": 0,
                "gpt_planning_calls": 0,
                "gpt_synthesis_calls": 0,
                "model_loop_allowed": False,
                "cross_task_history_used": False,
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
