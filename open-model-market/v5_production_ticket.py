#!/usr/bin/env python3
"""Run one explicit V5 production runtime and freeze one evidence bundle.

This adapter does not install patches or mutate environment-derived policy.
Planning, execution and evidence normalization receive the same immutable
RuntimeConfig. Failures close the run without invoking an alternate runtime.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_pipeline
from v5_evidence_bundle import ApprovedRun, EvidenceBundleBuilder, EvidenceInputs
from v5_model_company import (
    DEFAULT_INTELLIGENCE_RANKING_LIMIT,
    MINIMUM_CANDIDATES_PER_WORK,
)
from v5_recovery_runtime import build_production_runtime
from v5_runtime import ProductionRuntime, RuntimeConfig

RUNTIME_VERSION = "v5-native-runtime-1"
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
    """Return only user-authored task fields from the immutable ticket.

    ``task.txt`` intentionally contains execution/delegation boundaries. Those
    boundaries must not participate in domain, complexity, long-context or
    operation classification because words such as “evidence” and “report” are
    runtime governance, not user task semantics.
    """
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
        cleaned = [str(item).strip() for item in requirements if str(item).strip()]
        if cleaned:
            sections.append("执行要求：\n" + "\n".join(f"- {item}" for item in cleaned))
    language = str(task.get("language") or "").strip()
    if language:
        sections.append(f"输出语言：{language}")
    return "\n\n".join(sections), "ticket.task"


def _attempt_rows(node_results: Any) -> list[Mapping[str, Any]]:
    rows: list[Mapping[str, Any]] = []
    if not isinstance(node_results, list):
        return rows
    for node in node_results:
        if not isinstance(node, Mapping):
            continue
        attempts = node.get("attempts", [])
        if isinstance(attempts, list):
            rows.extend(attempt for attempt in attempts if isinstance(attempt, Mapping))
    return rows


def _write_runtime_evidence(
    output: Path,
    *,
    total_calls: int,
    recovery_calls: int,
    anomaly_budget: float | None,
) -> None:
    """Write minimal fail-closed runtime evidence if normalization cannot run."""
    _write(output / "production-runtime.json", {
        "runtime_version": RUNTIME_VERSION,
        "entrypoint": "v5_production_ticket.py",
        "runtime_constructor": "v5_runtime.ProductionRuntime",
        "recovery_policy": "v5_recovery_runtime.cross-endpoint-company-safe-cost-performance",
        "model_company_policy": "task-global-all-different",
        "intelligence_ranking_limit": DEFAULT_INTELLIGENCE_RANKING_LIMIT,
        "maximum_candidates_per_work": MINIMUM_CANDIDATES_PER_WORK,
        "global_monkey_patching": False,
        "maximum_model_calls": total_calls,
        "maximum_total_calls": total_calls,
        "maximum_recovery_calls": recovery_calls,
        "maximum_initial_calls": total_calls - recovery_calls,
        "cost_anomaly_usd": anomaly_budget,
        "fallback_policy": "fail-closed-no-alternate-runtime",
        "legacy_runtime_present": False,
        "cross_task_history_used": False,
    })


def _normalize_evidence(
    output: Path,
    *,
    total_calls: int,
    recovery_calls: int,
    anomaly_budget: float | None,
    require_report: bool,
) -> dict[str, Any]:
    """Build every normalized document from one immutable input snapshot."""
    inputs = EvidenceInputs.from_directory(output)
    builder = EvidenceBundleBuilder(
        inputs,
        ApprovedRun(
            total_calls=total_calls,
            recovery_calls=recovery_calls,
            cost_anomaly_usd=anomaly_budget,
        ),
    )
    return builder.write(output, require_report=require_report)


def _normalize(
    output: Path,
    *,
    total_calls: int,
    recovery_calls: int,
    anomaly_budget: float | None,
) -> dict[str, Any]:
    return _normalize_evidence(
        output,
        total_calls=total_calls,
        recovery_calls=recovery_calls,
        anomaly_budget=anomaly_budget,
        require_report=True,
    )


def _retryable_provider_failure(output: Path) -> bool:
    """Classify retryability only from structured failure fields."""
    rows = _load(output / "v5-node-results.json", [])
    attempts = _attempt_rows(rows)
    if not attempts:
        return False
    saw_failure = False
    for attempt in attempts:
        failure = (
            attempt.get("failure")
            if isinstance(attempt.get("failure"), Mapping)
            else None
        )
        if failure is None:
            if str(attempt.get("status") or "") == "passed":
                return False
            continue
        saw_failure = True
        if not bool(failure.get("retryable")):
            return False
    return saw_failure


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Execute the explicit V5 native production ticket runtime"
    )
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument(
        "--quality-tier",
        choices=["budget", "value", "quality"],
        default="value",
    )
    parser.add_argument("--maximum-total-calls", type=int, required=True)
    parser.add_argument("--maximum-recovery-calls", type=int, required=True)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument("--require-live-catalog", action="store_true")
    return parser


def _runtime(args: argparse.Namespace) -> ProductionRuntime:
    config = RuntimeConfig(
        total_call_limit=args.maximum_total_calls,
        recovery_call_limit=args.maximum_recovery_calls,
        cost_anomaly_usd=args.cost_anomaly_usd,
        quality_tier=args.quality_tier,
        tools_allowed=False,
        live_catalog_required=args.require_live_catalog,
        provider_lock_required=True,
        maximum_candidates_per_work=MINIMUM_CANDIDATES_PER_WORK,
    )
    return build_production_runtime(config)


def _pipeline_command(args: argparse.Namespace, output: Path, task: str) -> list[str]:
    command = [
        "--task", task,
        "--output-dir", str(output),
        "--quality-tier", args.quality_tier,
        "--ranking-limit", str(DEFAULT_INTELLIGENCE_RANKING_LIMIT),
        "--maximum-total-calls", str(args.maximum_total_calls),
        "--maximum-recovery-calls", str(args.maximum_recovery_calls),
        "--maximum-candidates-per-work", str(MINIMUM_CANDIDATES_PER_WORK),
        "--solver-timeout-seconds", "20",
    ]
    if args.cost_anomaly_usd is not None:
        command.extend(["--cost-anomaly-usd", str(args.cost_anomaly_usd)])
    if args.require_live_catalog:
        command.append("--require-live-catalog")
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if not 4 <= args.maximum_total_calls <= ABSOLUTE_MAX_MODEL_CALLS:
        raise ValueError("maximum-total-calls must be between 4 and 16")
    if not 0 <= args.maximum_recovery_calls < args.maximum_total_calls:
        raise ValueError(
            "maximum-recovery-calls must be non-negative and below total calls"
        )
    runtime = _runtime(args)
    canonical_task, task_source = _canonical_user_task(output, args.task)
    if not canonical_task:
        raise ValueError("canonical user task is empty")
    _write(output / "planning-task.json", {
        "schema_version": "v5-planning-task-1",
        "source": task_source,
        "sha256": hashlib.sha256(canonical_task.encode("utf-8")).hexdigest(),
        "characters": len(canonical_task),
        "delegation_notice_included": False,
        "execution_constraints_supplied_by_runtime": True,
        "cross_endpoint_empty_response_recovery": True,
        "task_global_model_company_uniqueness": True,
        "intelligence_ranking_limit": DEFAULT_INTELLIGENCE_RANKING_LIMIT,
        "maximum_candidates_per_work": MINIMUM_CANDIDATES_PER_WORK,
    })
    try:
        code = int(v5_pipeline.main(
            _pipeline_command(args, output, canonical_task),
            runtime=runtime,
        ))
        if code != 0:
            raise RuntimeError(f"V5 pipeline returned {code}")
        envelope = _normalize(
            output,
            total_calls=args.maximum_total_calls,
            recovery_calls=args.maximum_recovery_calls,
            anomaly_budget=args.cost_anomaly_usd,
        )
        if envelope.get("status") != "success":
            raise RuntimeError("V5 production result did not pass the delivery gate")
        print(json.dumps({
            "runtime_version": RUNTIME_VERSION,
            "status": envelope["status"],
            "completion_mode": envelope["completion_mode"],
            "actual_cost_usd": envelope["actual_cost_usd"],
            "node_count": envelope["node_count"],
            "approved_total_calls": args.maximum_total_calls,
            "model_company_policy": "task-global-all-different",
            "intelligence_ranking_limit": DEFAULT_INTELLIGENCE_RANKING_LIMIT,
            "maximum_candidates_per_work": MINIMUM_CANDIDATES_PER_WORK,
            "evidence_input_sha256": envelope["evidence_input_sha256"],
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
        except Exception as normalize_exc:  # noqa: BLE001 - preserve primary failure
            normalization_error = str(normalize_exc)
            _write_runtime_evidence(
                output,
                total_calls=args.maximum_total_calls,
                recovery_calls=args.maximum_recovery_calls,
                anomaly_budget=args.cost_anomaly_usd,
            )
        retryable = _retryable_provider_failure(output)
        _write(output / "expert-team-error.json", {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "error_code": (
                "V5_RETRYABLE_PROVIDER_RESPONSE_FAILURE"
                if retryable
                else "V5_PRODUCTION_EXECUTION_FAILED"
            ),
            "stage": "v5-production-runtime",
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "normalization_error": normalization_error,
            "retryable": retryable,
            "fallback_used": False,
            "legacy_runtime_present": False,
            "global_monkey_patching": False,
            "task_global_model_company_uniqueness": True,
            "intelligence_ranking_limit": DEFAULT_INTELLIGENCE_RANKING_LIMIT,
            "maximum_candidates_per_work": MINIMUM_CANDIDATES_PER_WORK,
            "cross_task_history_used": False,
        })
        raise


if __name__ == "__main__":
    raise SystemExit(main())
