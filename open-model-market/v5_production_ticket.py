#!/usr/bin/env python3
"""Run the hardened V5 R8 graph from a production execution ticket.

The adapter installs the consolidated V5 policies, delegates planning and
execution to the dynamic pipeline, and writes the production evidence bundle.
It fails closed and has no alternate runtime path.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_production_hardening

v5_production_hardening.install()
import v5_pipeline  # noqa: E402

RUNTIME_VERSION = "v5-r8"
ABSOLUTE_MAX_MODEL_CALLS = 16


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _provider_slug(endpoint: str) -> str:
    return endpoint.rsplit("@", 1)[-1] if "@" in endpoint else endpoint


def _request_provider(request: Mapping[str, Any]) -> str | None:
    provider = request.get("provider")
    if not isinstance(provider, Mapping):
        return None
    values = provider.get("only") or provider.get("order")
    if isinstance(values, list) and values:
        return str(values[0])
    return None


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


def _cost_from_attempts(attempts: Sequence[Mapping[str, Any]]) -> float:
    total = 0.0
    for attempt in attempts:
        usage = attempt.get("usage") if isinstance(attempt.get("usage"), Mapping) else {}
        for key in ("cost", "total_cost"):
            try:
                if usage.get(key) is not None:
                    total += max(0.0, float(usage[key]))
                    break
            except (TypeError, ValueError):
                continue
    return round(total, 8)


def _providers_from_evidence(
    attempts: Sequence[Mapping[str, Any]],
    requests: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    attempted = {
        provider
        for request in requests
        if isinstance(request, Mapping)
        for provider in [_request_provider(request)]
        if provider
    }
    substantive: set[str] = set()
    for attempt in attempts:
        provider = str(attempt.get("response_provider") or "").strip()
        endpoint = str(attempt.get("provider_endpoint") or "").strip()
        usage = attempt.get("usage") if isinstance(attempt.get("usage"), Mapping) else {}
        if provider:
            substantive.add(provider)
        elif usage and endpoint:
            substantive.add(_provider_slug(endpoint))
        if endpoint:
            attempted.add(_provider_slug(endpoint))
    return sorted(attempted), sorted(substantive)


def _write_runtime_evidence(
    output: Path,
    *,
    total_calls: int,
    recovery_calls: int,
    anomaly_budget: float | None,
) -> None:
    _write(output / "production-runtime.json", {
        "runtime_version": RUNTIME_VERSION,
        "entrypoint": "v5_production_ticket.py",
        "hardening": "v5_production_hardening.install",
        "maximum_model_calls": total_calls,
        "maximum_recovery_calls": recovery_calls,
        "maximum_initial_calls": total_calls - recovery_calls,
        "cost_anomaly_usd": anomaly_budget,
        "fallback_policy": "fail-closed-no-alternate-runtime",
        "legacy_runtime_present": False,
    })


def _normalize_evidence(
    output: Path,
    *,
    total_calls: int,
    recovery_calls: int,
    anomaly_budget: float | None,
    require_report: bool,
) -> dict[str, Any]:
    summary = _load(output / "v5-execution-summary.json", {})
    graph = _load(output / "v5-execution-graph.json", {})
    node_results = _load(output / "v5-node-results.json", [])
    request_audit = _load(output / "v5-request-audit.json", {})
    optimization = _load(output / "v5-optimization.json", {})
    ticket = _load(output / "ticket-status.json", {})

    summary = dict(summary) if isinstance(summary, Mapping) else {}
    graph = dict(graph) if isinstance(graph, Mapping) else {}
    request_audit = dict(request_audit) if isinstance(request_audit, Mapping) else {}
    requests = request_audit.get("requests") if isinstance(request_audit.get("requests"), list) else []
    requests = [request for request in requests if isinstance(request, Mapping)]
    attempts = _attempt_rows(node_results)
    request_count = int(request_audit.get("request_count") or len(requests))
    execution_budget = summary.get("execution_budget") if isinstance(summary.get("execution_budget"), Mapping) else {}
    calls_reserved = int(execution_budget.get("calls_reserved") or request_count)
    actual_cost = float(summary.get("actual_cost_usd") or execution_budget.get("actual_cost_usd") or _cost_from_attempts(attempts))
    if not math.isfinite(actual_cost) or actual_cost < 0:
        actual_cost = _cost_from_attempts(attempts)
    if calls_reserved > total_calls or request_count > total_calls:
        raise RuntimeError(
            f"V5 exceeded approved total paid-call ceiling: reserved={calls_reserved}, "
            f"captured={request_count}, approved={total_calls}"
        )

    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
    if len(nodes) > total_calls - recovery_calls:
        raise RuntimeError("V5 planned more initial nodes than the approved total leaves after recovery reserve")
    endpoints = sorted({
        str(row.get("provider_endpoint"))
        for row in nodes
        if isinstance(row, Mapping) and row.get("provider_endpoint")
    })
    models = sorted({
        str(row.get("model"))
        for row in nodes
        if isinstance(row, Mapping) and row.get("model")
    })
    attempted_providers, substantive_providers = _providers_from_evidence(attempts, requests)

    source_status = str(request_audit.get("status") or "missing")
    normalized_request_status = (
        "PASS"
        if source_status == "PASS" and request_count == len(requests) and request_count == calls_reserved
        else source_status
    )
    normalized_audit = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": normalized_request_status,
        "approved_total_call_ceiling": total_calls,
        "approved_recovery_call_ceiling": recovery_calls,
        "expected_request_count": calls_reserved,
        "captured_request_count": request_count,
        "requests": requests,
        "external_tools_allowed": False,
        "dynamic_output_allowance_sent": bool(request_audit.get("dynamic_output_allowance_sent")),
        "bounded_output_allowance_sent": bool(request_audit.get("bounded_output_allowance_sent")),
        "artificial_token_ceiling_sent": bool(request_audit.get("artificial_token_ceiling_sent", False)),
        "quality_integrity_status": request_audit.get("quality_integrity_status"),
        "source": "v5-request-audit.json",
    }
    _write(output / "request-audit.json", normalized_audit)

    ledger = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "summary": {
            "call_count": calls_reserved,
            "approved_total_call_ceiling": total_calls,
            "approved_recovery_call_ceiling": recovery_calls,
            "provider_actual_cost_usd": round(actual_cost, 8),
            "conservative_cost_usd": round(actual_cost, 8),
            "cost_evidence_status": (
                "provider_actual_or_runtime_reconciled"
                if actual_cost > 0
                else "request_attempt_recorded_no_provider_usage"
            ),
            "cost_anomaly_usd": anomaly_budget,
            "attempted_providers": attempted_providers,
            "attempted_provider_count": len(attempted_providers),
            "substantive_providers": substantive_providers,
            "substantive_provider_count": len(substantive_providers),
            "all_providers": sorted(set(attempted_providers) | set(substantive_providers)),
            "replacement_calls": int(execution_budget.get("replacements_reserved") or 0),
            "retry_calls": int(execution_budget.get("retries_reserved") or 0),
            "recovery_calls": int(execution_budget.get("recovery_calls_reserved") or 0),
        },
        "node_results": node_results if isinstance(node_results, list) else [],
    }
    _write(output / "call-ledger.json", ledger)

    selection = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "models": models,
        "provider_endpoints": endpoints,
        "node_count": len(nodes),
        "selected_interpretation": optimization.get("selected_interpretation") if isinstance(optimization, Mapping) else None,
    }
    _write(output / "model-selection.json", selection)
    _write(output / "task-routing.json", {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": "PASS" if graph else "FAIL",
        "mode": "dynamic-v5-dag",
        "call_consumed": False,
    })

    report_source = output / "v5-final-report.md"
    answer = str(summary.get("final_answer") or "").strip()
    if require_report:
        if not report_source.is_file() or not answer:
            raise RuntimeError("V5 did not produce a final report")
        shutil.copyfile(report_source, output / "expert-team-report.md")

    envelope = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": str(summary.get("status") or "failed"),
        "completion_mode": str(summary.get("completion_mode") or "none"),
        "quality_status": str(summary.get("quality_status") or "failed"),
        "quality_integrity": summary.get("quality_integrity"),
        "final_answer": answer,
        "actual_cost_usd": round(actual_cost, 8),
        "executor": summary.get("executor"),
        "work_coverage": summary.get("work_coverage"),
        "degradation": summary.get("degradation"),
        "execution_budget": dict(execution_budget),
        "approved_budget": {
            "maximum_total_calls": total_calls,
            "maximum_recovery_calls": recovery_calls,
            "maximum_initial_calls": total_calls - recovery_calls,
            "cost_anomaly_usd": anomaly_budget,
        },
        "node_count": len(nodes),
        "model_count": len(models),
        "provider_count": len(substantive_providers),
        "attempted_provider_count": len(attempted_providers),
        "production_entrypoint": True,
        "fallback_used": False,
        "legacy_runtime_present": False,
        "ticket_task_id": ticket.get("task_id") if isinstance(ticket, Mapping) else None,
    }
    _write(output / "expert-team-result.json", envelope)
    _write_runtime_evidence(
        output,
        total_calls=total_calls,
        recovery_calls=recovery_calls,
        anomaly_budget=anomaly_budget,
    )
    return envelope


def _normalize(output: Path, *, total_calls: int, recovery_calls: int, anomaly_budget: float | None) -> dict[str, Any]:
    return _normalize_evidence(
        output,
        total_calls=total_calls,
        recovery_calls=recovery_calls,
        anomaly_budget=anomaly_budget,
        require_report=True,
    )


def _retryable_provider_failure(output: Path) -> bool:
    rows = _load(output / "v5-node-results.json", [])
    attempts = _attempt_rows(rows)
    if not attempts:
        return False
    for attempt in attempts:
        text = " ".join([
            str(attempt.get("error") or ""),
            " ".join(str(value) for value in attempt.get("gate_reasons", []) if value),
        ]).casefold()
        usage = attempt.get("usage") if isinstance(attempt.get("usage"), Mapping) else {}
        answer = str(attempt.get("answer") or "").strip()
        response_id = str(attempt.get("response_id") or "").strip()
        if not answer and not response_id and not usage:
            continue
        if any(token in text for token in ("429", "rate limit", "timeout", "502", "503", "504", "upstream")):
            continue
        return False
    return True


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute the hardened V5 R8 production ticket runtime")
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--quality-tier", choices=["budget", "value", "quality"], default="value")
    parser.add_argument("--maximum-total-calls", type=int, required=True)
    parser.add_argument("--maximum-recovery-calls", type=int, required=True)
    parser.add_argument("--cost-anomaly-usd", type=float)
    parser.add_argument("--require-live-catalog", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    if not 4 <= args.maximum_total_calls <= ABSOLUTE_MAX_MODEL_CALLS:
        raise ValueError("maximum-total-calls must be between 4 and 16")
    if not 0 <= args.maximum_recovery_calls < args.maximum_total_calls:
        raise ValueError("maximum-recovery-calls must be non-negative and below total calls")
    os.environ["TOTAL_MODEL_CALLS"] = str(args.maximum_total_calls)
    command = [
        "--task", args.task,
        "--output-dir", str(output),
        "--quality-tier", args.quality_tier,
        "--maximum-total-calls", str(args.maximum_total_calls),
        "--maximum-recovery-calls", str(args.maximum_recovery_calls),
        "--maximum-candidates-per-work", "12",
        "--solver-timeout-seconds", "20",
    ]
    if args.cost_anomaly_usd is not None:
        command.extend(["--cost-anomaly-usd", str(args.cost_anomaly_usd)])
    if args.require_live_catalog:
        command.append("--require-live-catalog")
    try:
        code = int(v5_pipeline.main(command))
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
        })
        raise


if __name__ == "__main__":
    raise SystemExit(main())
