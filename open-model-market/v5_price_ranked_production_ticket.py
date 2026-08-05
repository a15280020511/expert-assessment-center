#!/usr/bin/env python3
"""Execute one production ticket using governance-owned model selection."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import traceback
from pathlib import Path
from typing import Any, Sequence

import v5_price_ranked_pipeline
from v5_governance_selection import SELECTION_AUTHORITY, load_and_validate
from v5_json_io import write_json
from v5_price_ranked_evidence import RUNTIME_VERSION, normalize_price_ranked_evidence
from v5_price_ranked_support import canonical_ticket_task
from v5_task_constraints import compile_task_constraints


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--governance-selection-file")
    parser.add_argument("--maximum-total-calls", type=int, required=True)
    parser.add_argument("--maximum-recovery-calls", type=int, required=True)
    parser.add_argument("--expert-count", type=int)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument("--require-live-catalog", action="store_true")
    return parser


def _pipeline_args(
    args: argparse.Namespace,
    root: Path,
    task: str,
    selection_path: Path,
) -> list[str]:
    values = [
        "--task",
        task,
        "--output-dir",
        str(root),
        "--governance-selection-file",
        str(selection_path),
        "--maximum-total-calls",
        str(args.maximum_total_calls),
        "--maximum-recovery-calls",
        str(args.maximum_recovery_calls),
    ]
    if args.cost_anomaly_usd is not None:
        values.extend(["--cost-anomaly-usd", str(args.cost_anomaly_usd)])
    if args.max_completion_tokens is not None:
        values.extend(["--max-completion-tokens", str(args.max_completion_tokens)])
    if args.require_live_catalog:
        values.append("--require-live-catalog")
    return values


def _normalize(
    root: Path,
    args: argparse.Namespace,
    require_report: bool,
) -> dict[str, Any]:
    return normalize_price_ranked_evidence(
        root,
        approved_total_calls=args.maximum_total_calls,
        approved_recovery_calls=args.maximum_recovery_calls,
        cost_anomaly_usd=args.cost_anomaly_usd,
        require_report=require_report,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    total = int(args.maximum_total_calls)
    recovery = int(args.maximum_recovery_calls)
    if not 4 <= total <= 16:
        raise ValueError("maximum-total-calls must be between 4 and 16")
    if not 0 <= recovery < total or total - recovery < 3:
        raise ValueError("recovery reserve must leave three initial expert calls")
    if args.expert_count is not None:
        raise ValueError(
            "expert-count is governance-owned and cannot be set in the expert center"
        )
    if args.max_completion_tokens is not None and args.max_completion_tokens <= 0:
        raise ValueError("max-completion-tokens must be positive")

    selection_path = (
        Path(args.governance_selection_file)
        if args.governance_selection_file
        else root / "governance-selection.json"
    )
    plan, validation = load_and_validate(
        selection_path,
        approved_total_calls=total,
        approved_recovery_calls=recovery,
    )
    task, source = canonical_ticket_task(root, args.task)
    task = task.strip()
    if not task:
        raise ValueError("canonical user task is empty")
    if task != str(plan["task_text"]).strip():
        raise ValueError("canonical task differs from governance-bound task")
    constraints = compile_task_constraints(task)
    source_commit = str(
        os.getenv("AUTHORITATIVE_EXECUTION_SHA")
        or os.getenv("GITHUB_SHA")
        or ""
    )
    source_run_id = str(os.getenv("GITHUB_RUN_ID") or "")
    write_json(root / "governance-selection.json", plan)
    write_json(root / "governance-selection-validation.json", validation)
    write_json(
        root / "planning-task.json",
        {
            "schema_version": "v5-governance-selected-planning-task-1",
            "source": source,
            "sha256": hashlib.sha256(task.encode("utf-8")).hexdigest(),
            "characters": len(task),
            "task_constraints": constraints.to_dict(),
            "selection_authority": SELECTION_AUTHORITY,
            "selection_source_repository": plan["source_repository"],
            "selection_source_commit": plan.get("source_commit", ""),
            "selection_plan_sha256": plan["plan_sha256"],
            "decomposition_authority": SELECTION_AUTHORITY,
            "expert_center_selection_performed": False,
            "expert_center_catalog_fetch_performed": False,
            "local_fallback_used": False,
            "claude_mechanism_enabled": False,
            "claude_calls": 0,
            "governance_model_calls": 0,
            "networkx_orchestration": True,
            "optimizer_used": False,
        },
    )
    write_json(
        root / "production-runtime.json",
        {
            "runtime_version": RUNTIME_VERSION,
            "entrypoint": "v5_price_ranked_production_ticket.py",
            "pipeline": "v5_price_ranked_pipeline.py",
            "architecture": (
                "governance-bound selection plan -> integrity/budget validation -> "
                "NetworkX DAG materialization -> expert execution"
            ),
            "source_commit": source_commit,
            "source_run_id": source_run_id,
            "maximum_total_calls": total,
            "governance_calls_reserved": 0,
            "maximum_expert_calls": total,
            "maximum_recovery_calls": recovery,
            "maximum_initial_calls": total - recovery,
            "cost_anomaly_usd": args.cost_anomaly_usd,
            "max_completion_tokens": args.max_completion_tokens,
            "selection_authority": SELECTION_AUTHORITY,
            "selection_source_repository": plan["source_repository"],
            "selection_source_commit": plan.get("source_commit", ""),
            "selection_plan_sha256": plan["plan_sha256"],
            "orchestration_library": "networkx",
            "expert_center_selection_performed": False,
            "expert_center_catalog_fetch_performed": False,
            "local_selection_fallback_allowed": False,
            "claude_mechanism_enabled": False,
            "claude_calls": 0,
            "governance_model_calls": 0,
            "external_tools_allowed": False,
            "provider_fallback_allowed": False,
            "fallback_policy": "fail-closed-no-local-selection-runtime",
            "legacy_runtime_present": False,
            "model_loop_allowed": False,
            "cross_task_history_used": False,
        },
    )
    try:
        code = int(
            v5_price_ranked_pipeline.main(
                _pipeline_args(args, root, task, selection_path)
            )
        )
        if code:
            raise RuntimeError(f"governance-selected pipeline returned {code}")
        result = _normalize(root, args, True)
        if result.get("status") != "success":
            raise RuntimeError("production result failed delivery gate")
        print(
            json.dumps(
                {
                    "runtime_version": RUNTIME_VERSION,
                    "status": result["status"],
                    "completion_mode": result["completion_mode"],
                    "actual_cost_usd": result["actual_cost_usd"],
                    "node_count": result["node_count"],
                    "approved_total_calls": total,
                    "selection_authority": SELECTION_AUTHORITY,
                    "selection_plan_sha256": plan["plan_sha256"],
                    "expert_center_selection_performed": False,
                    "expert_center_catalog_fetch_performed": False,
                    "local_fallback_used": False,
                    "claude_calls": 0,
                    "governance_model_calls": 0,
                    "networkx_orchestration": True,
                    "evidence_input_sha256": result["evidence_input_sha256"],
                },
                ensure_ascii=False,
            )
        )
        return 0
    except Exception as exc:
        normalization_error = None
        try:
            _normalize(root, args, False)
        except Exception as normalize_exc:  # noqa: BLE001
            normalization_error = str(normalize_exc)
        write_json(
            root / "expert-team-error.json",
            {
                "version": 5,
                "runtime_version": RUNTIME_VERSION,
                "error_code": "GOVERNANCE_SELECTED_PRODUCTION_EXECUTION_FAILED",
                "stage": "governance-selected-production-runtime",
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "normalization_error": normalization_error,
                "selection_authority": SELECTION_AUTHORITY,
                "selection_plan_sha256": plan.get("plan_sha256", ""),
                "expert_center_selection_performed": False,
                "expert_center_catalog_fetch_performed": False,
                "fallback_used": False,
                "legacy_runtime_present": False,
                "claude_mechanism_enabled": False,
                "claude_calls": 0,
                "governance_model_calls": 0,
                "model_loop_allowed": False,
                "cross_task_history_used": False,
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
