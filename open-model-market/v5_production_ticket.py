#!/usr/bin/env python3
"""Execute one GPT-led expert-team ticket and freeze audited evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_pipeline
from v5_claude_red_team_policy import CLAUDE_RED_TEAM_GOVERNANCE_CALLS
from v5_evidence_bundle import ApprovedRun, EvidenceBundleBuilder, EvidenceInputs
from v5_task_constraints import compile_task_constraints

RUNTIME_VERSION = "v5-gpt-claude-runtime-1"
ABSOLUTE_MAX_MODEL_CALLS = 16


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _canonical_user_task(output: Path, fallback: str) -> tuple[str, str]:
    packet = _load(output / "ticket.json", {})
    task = packet.get("task") if isinstance(packet, Mapping) else None
    if not isinstance(task, Mapping):
        return str(fallback or "").strip(), "fallback-cli-task"
    question = str(task.get("question") or "").strip()
    if not question:
        return str(fallback or "").strip(), "fallback-cli-task"
    sections = [question]
    requirements = task.get("requirements")
    if isinstance(requirements, list):
        cleaned = [
            str(item).strip()
            for item in requirements
            if str(item).strip()
        ]
        if cleaned:
            sections.append(
                "执行要求：\n"
                + "\n".join(f"- {item}" for item in cleaned)
            )
    language = str(task.get("language") or "").strip()
    if language:
        sections.append(f"输出语言：{language}")
    return "\n\n".join(sections), "ticket.task"


def _normalize_evidence(
    output: Path,
    *,
    total_calls: int,
    recovery_calls: int,
    anomaly_budget: float | None,
    require_report: bool,
) -> dict[str, Any]:
    builder = EvidenceBundleBuilder(
        EvidenceInputs.from_directory(output),
        ApprovedRun(
            total_calls=total_calls,
            recovery_calls=recovery_calls,
            cost_anomaly_usd=anomaly_budget,
        ),
    )
    return builder.write(output, require_report=require_report)


def _retryable_provider_failure(output: Path) -> bool:
    nodes = _load(output / "v5-node-results.json", [])
    attempts = [
        attempt
        for node in nodes
        if isinstance(node, Mapping)
        for attempt in node.get("attempts", [])
        if isinstance(attempt, Mapping)
    ] if isinstance(nodes, list) else []
    if not attempts:
        return False
    saw_failure = False
    for attempt in attempts:
        failure = attempt.get("failure")
        failure = failure if isinstance(failure, Mapping) else None
        if failure is None:
            if str(attempt.get("status") or "") == "passed":
                return False
            continue
        saw_failure = True
        if not bool(failure.get("retryable")):
            return False
    return saw_failure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument(
        "--quality-tier",
        choices=["budget", "value", "quality"],
        default="value",
    )
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
    parser.add_argument("--require-live-catalog", action="store_true")
    return parser


def _pipeline_command(
    args: argparse.Namespace,
    output: Path,
    task: str,
) -> list[str]:
    command = [
        "--task",
        task,
        "--output-dir",
        str(output),
        "--quality-tier",
        args.quality_tier,
        "--ranking-limit",
        "150",
        "--maximum-total-calls",
        str(args.maximum_total_calls),
        "--maximum-recovery-calls",
        str(args.maximum_recovery_calls),
    ]
    if args.cost_anomaly_usd is not None:
        command.extend([
            "--cost-anomaly-usd",
            str(args.cost_anomaly_usd),
        ])
    if args.require_live_catalog:
        command.append("--require-live-catalog")
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if not 4 <= args.maximum_total_calls <= ABSOLUTE_MAX_MODEL_CALLS:
        raise ValueError(
            "maximum-total-calls must be between 4 and 16"
        )
    expert_total = (
        args.maximum_total_calls
        - CLAUDE_RED_TEAM_GOVERNANCE_CALLS
    )
    if not 0 <= args.maximum_recovery_calls < expert_total:
        raise ValueError(
            "recovery calls must leave one expert initial call after "
            "the three-call governance reserve"
        )

    canonical_task, task_source = _canonical_user_task(
        output,
        args.task,
    )
    if not canonical_task:
        raise ValueError("canonical user task is empty")
    constraints = compile_task_constraints(canonical_task)
    _write(
        output / "planning-task.json",
        {
            "schema_version": "v5-gpt-planning-task-1",
            "source": task_source,
            "sha256": hashlib.sha256(
                canonical_task.encode("utf-8")
            ).hexdigest(),
            "characters": len(canonical_task),
            "task_constraints": constraints.to_dict(),
            "selection_authority": "~openai/gpt-latest",
            "red_team_authority": (
                "~anthropic/claude-opus-latest"
            ),
            "claude_red_team_calls": 1,
            "gpt_synthesis_calls_max": 1,
            "local_scoring_used": False,
            "optimizer_used": False,
        },
    )
    _write(
        output / "production-runtime.json",
        {
            "runtime_version": RUNTIME_VERSION,
            "entrypoint": "v5_production_ticket.py",
            "pipeline": "v5_pipeline.py",
            "architecture": (
                "gpt-latest-proposal -> "
                "claude-opus-latest-red-team-once -> "
                "optional-gpt-synthesis-once -> "
                "deterministic-validator -> execution-only-runtime"
            ),
            "maximum_total_calls": args.maximum_total_calls,
            "governance_calls_reserved": (
                CLAUDE_RED_TEAM_GOVERNANCE_CALLS
            ),
            "maximum_recovery_calls": (
                args.maximum_recovery_calls
            ),
            "maximum_expert_calls": expert_total,
            "cost_anomaly_usd": args.cost_anomaly_usd,
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
                _pipeline_command(
                    args,
                    output,
                    canonical_task,
                )
            )
        )
        if code != 0:
            raise RuntimeError(f"V5 pipeline returned {code}")
        envelope = _normalize_evidence(
            output,
            total_calls=args.maximum_total_calls,
            recovery_calls=args.maximum_recovery_calls,
            anomaly_budget=args.cost_anomaly_usd,
            require_report=True,
        )
        if envelope.get("status") != "success":
            raise RuntimeError(
                "production result failed delivery gate"
            )
        print(json.dumps({
            "runtime_version": RUNTIME_VERSION,
            "status": envelope["status"],
            "completion_mode": envelope["completion_mode"],
            "actual_cost_usd": envelope["actual_cost_usd"],
            "node_count": envelope["node_count"],
            "approved_total_calls": args.maximum_total_calls,
            "selection_authority": "gpt-latest",
            "claude_red_team_calls": 1,
            "local_optimizer_used": False,
            "evidence_input_sha256": (
                envelope["evidence_input_sha256"]
            ),
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        normalization_error = None
        try:
            _normalize_evidence(
                output,
                total_calls=args.maximum_total_calls,
                recovery_calls=args.maximum_recovery_calls,
                anomaly_budget=args.cost_anomaly_usd,
                require_report=False,
            )
        except Exception as normalize_exc:  # noqa: BLE001
            normalization_error = str(normalize_exc)
        retryable = _retryable_provider_failure(output)
        _write(
            output / "expert-team-error.json",
            {
                "version": 5,
                "runtime_version": RUNTIME_VERSION,
                "error_code": (
                    "V5_RETRYABLE_PROVIDER_RESPONSE_FAILURE"
                    if retryable
                    else "V5_PRODUCTION_EXECUTION_FAILED"
                ),
                "stage": (
                    "v5-gpt-claude-production-runtime"
                ),
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "normalization_error": normalization_error,
                "retryable": retryable,
                "fallback_used": False,
                "local_planner_present": False,
                "optimizer_present": False,
                "claude_red_team_calls_max": 1,
                "model_loop_allowed": False,
                "cross_task_history_used": False,
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
