#!/usr/bin/env python3
"""Execute one GPT-led production ticket and freeze combined evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_pipeline
from v5_claude_red_team_policy import CLAUDE_RED_TEAM_GOVERNANCE_CALLS
from v5_json_io import load_json_or_default, write_json
from v5_production_run_evidence import (
    ApprovedRun,
    EvidenceBundleBuilder,
    EvidenceInputs,
)
from v5_task_constraints import compile_task_constraints

RUNTIME_VERSION = "v5-gpt-claude-runtime-1"


def _canonical_user_task(root: Path, fallback: str) -> tuple[str, str]:
    packet = load_json_or_default(root / "ticket.json", {})
    task = packet.get("task") if isinstance(packet, Mapping) else None
    if (
        not isinstance(task, Mapping)
        or not str(task.get("question") or "").strip()
    ):
        return str(fallback).strip(), "fallback-cli-task"
    sections = [str(task["question"]).strip()]
    requirements = task.get("requirements")
    if isinstance(requirements, list):
        rows = [
            str(value).strip()
            for value in requirements
            if str(value).strip()
        ]
        if rows:
            sections.append(
                "执行要求：\n"
                + "\n".join(f"- {row}" for row in rows)
            )
    language = str(task.get("language") or "").strip()
    if language:
        sections.append(f"输出语言：{language}")
    return "\n\n".join(sections), "ticket.task"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument(
        "--maximum-total-calls",
        type=int,
        required=True,
    )
    parser.add_argument(
        "--maximum-recovery-calls",
        type=int,
        required=True,
    )
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument("--max-completion-tokens", type=int)
    parser.add_argument(
        "--governance-max-completion-tokens",
        type=int,
    )
    parser.add_argument("--require-live-catalog", action="store_true")
    return parser


def _pipeline_args(
    args: argparse.Namespace,
    root: Path,
    task: str,
) -> list[str]:
    values = [
        "--task",
        task,
        "--output-dir",
        str(root),
        "--ranking-limit",
        "150",
        "--maximum-total-calls",
        str(args.maximum_total_calls),
        "--maximum-recovery-calls",
        str(args.maximum_recovery_calls),
    ]
    if args.cost_anomaly_usd is not None:
        values.extend([
            "--cost-anomaly-usd",
            str(args.cost_anomaly_usd),
        ])
    if args.max_completion_tokens is not None:
        values.extend([
            "--max-completion-tokens",
            str(args.max_completion_tokens),
        ])
    if args.governance_max_completion_tokens is not None:
        values.extend([
            "--governance-max-completion-tokens",
            str(args.governance_max_completion_tokens),
        ])
    if args.require_live_catalog:
        values.append("--require-live-catalog")
    return values


def _normalize(
    root: Path,
    args: argparse.Namespace,
    require_report: bool,
) -> dict[str, Any]:
    return EvidenceBundleBuilder(
        EvidenceInputs.from_directory(root),
        ApprovedRun(
            total_calls=args.maximum_total_calls,
            recovery_calls=args.maximum_recovery_calls,
            cost_anomaly_usd=args.cost_anomaly_usd,
        ),
    ).write(root, require_report=require_report)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    expert_total = (
        args.maximum_total_calls
        - CLAUDE_RED_TEAM_GOVERNANCE_CALLS
    )
    if not 4 <= args.maximum_total_calls <= 16:
        raise ValueError(
            "maximum-total-calls must be between 4 and 16"
        )
    if not 0 <= args.maximum_recovery_calls < expert_total:
        raise ValueError(
            "recovery reserve must leave an expert call after "
            "three governance calls"
        )
    if (
        args.max_completion_tokens is not None
        and args.max_completion_tokens <= 0
    ):
        raise ValueError("max-completion-tokens must be positive")
    if (
        args.governance_max_completion_tokens is not None
        and args.governance_max_completion_tokens <= 0
    ):
        raise ValueError(
            "governance-max-completion-tokens must be positive"
        )
    task, source = _canonical_user_task(root, args.task)
    if not task:
        raise ValueError("canonical user task is empty")
    constraints = compile_task_constraints(task)
    write_json(
        root / "planning-task.json",
        {
            "schema_version": "v5-gpt-planning-task-2",
            "source": source,
            "sha256": hashlib.sha256(
                task.encode("utf-8")
            ).hexdigest(),
            "characters": len(task),
            "task_constraints": constraints.to_dict(),
            "selection_authority": "~openai/gpt-latest",
            "red_team_model": "~anthropic/claude-opus-latest",
            "claude_red_team_calls": 1,
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "gpt_synthesis_calls": 1,
            "local_scoring_used": False,
            "optimizer_used": False,
        },
    )
    write_json(
        root / "production-runtime.json",
        {
            "runtime_version": RUNTIME_VERSION,
            "entrypoint": "v5_production_ticket.py",
            "pipeline": "v5_pipeline.py",
            "architecture": (
                "gpt-latest-proposal -> "
                "claude-opus-latest-advice-once -> "
                "gpt-latest-synthesis-once -> "
                "deterministic-validator -> executor"
            ),
            "maximum_total_calls": args.maximum_total_calls,
            "governance_calls_reserved": (
                CLAUDE_RED_TEAM_GOVERNANCE_CALLS
            ),
            "maximum_expert_calls": expert_total,
            "maximum_recovery_calls": (
                args.maximum_recovery_calls
            ),
            "cost_anomaly_usd": args.cost_anomaly_usd,
            "max_completion_tokens": args.max_completion_tokens,
            "governance_max_completion_tokens": (
                args.governance_max_completion_tokens
            ),
            "claude_is_advisory_only": True,
            "claude_gatekeeping_allowed": False,
            "deterministic_validator_is_only_hard_gate": True,
            "local_planner_present": False,
            "optimizer_present": False,
            "cp_sat_present": False,
            "pareto_pruning_present": False,
            "model_loop_allowed": False,
            "fallback_policy": (
                "fail-closed-no-alternate-runtime"
            ),
            "cross_task_history_used": False,
        },
    )
    try:
        code = int(
            v5_pipeline.main(
                _pipeline_args(args, root, task)
            )
        )
        if code:
            raise RuntimeError(f"V5 pipeline returned {code}")
        result = _normalize(root, args, True)
        if result.get("status") != "success":
            raise RuntimeError(
                "production result failed delivery gate"
            )
        print(json.dumps({
            "runtime_version": RUNTIME_VERSION,
            "status": result["status"],
            "completion_mode": result["completion_mode"],
            "actual_cost_usd": result["actual_cost_usd"],
            "node_count": result["node_count"],
            "approved_total_calls": args.maximum_total_calls,
            "max_completion_tokens": args.max_completion_tokens,
            "governance_max_completion_tokens": (
                args.governance_max_completion_tokens
            ),
            "selection_authority": "gpt-latest",
            "claude_red_team_calls": 1,
            "claude_is_advisory_only": True,
            "gpt_synthesis_calls": 1,
            "local_optimizer_used": False,
            "evidence_input_sha256": (
                result["evidence_input_sha256"]
            ),
        }, ensure_ascii=False))
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
                "error_code": "V5_PRODUCTION_EXECUTION_FAILED",
                "stage": "v5-gpt-claude-production-runtime",
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "normalization_error": normalization_error,
                "fallback_used": False,
                "local_planner_present": False,
                "optimizer_present": False,
                "max_completion_tokens": args.max_completion_tokens,
                "governance_max_completion_tokens": (
                    args.governance_max_completion_tokens
                ),
                "claude_red_team_calls": 1,
                "claude_is_advisory_only": True,
                "claude_gatekeeping_allowed": False,
                "gpt_synthesis_calls": 1,
                "model_loop_allowed": False,
                "cross_task_history_used": False,
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
