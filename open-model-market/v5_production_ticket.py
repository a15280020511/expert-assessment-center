#!/usr/bin/env python3
"""Run the hardened V5 R8 graph from a production execution ticket.

This adapter is intentionally independent from the preserved V3 runtime. It
installs the consolidated R8 policies, delegates planning/execution to the V5
pipeline, and writes a compact compatibility envelope for the production
publisher and evidence bundle. It never falls back to V3.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import sys
import traceback
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_production_hardening

v5_production_hardening.install()
import v5_pipeline  # noqa: E402  (must be imported after hardening installation)

RUNTIME_VERSION = "v5-r8"
MAX_MODEL_CALLS = 16


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def _write(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _provider_slug(endpoint: str) -> str:
    return endpoint.rsplit("@", 1)[-1] if "@" in endpoint else endpoint


def _normalize(output: Path) -> dict[str, Any]:
    summary = _load(output / "v5-execution-summary.json", {})
    graph = _load(output / "v5-execution-graph.json", {})
    node_results = _load(output / "v5-node-results.json", [])
    request_audit = _load(output / "v5-request-audit.json", {})
    optimization = _load(output / "v5-optimization.json", {})
    ticket = _load(output / "ticket-status.json", {})

    if not isinstance(summary, Mapping):
        raise RuntimeError("V5 execution summary is missing or invalid")
    if not isinstance(graph, Mapping):
        raise RuntimeError("V5 execution graph is missing or invalid")
    if not isinstance(request_audit, Mapping):
        raise RuntimeError("V5 request audit is missing or invalid")

    report_source = output / "v5-final-report.md"
    answer = str(summary.get("final_answer") or "").strip()
    if not report_source.is_file() or not answer:
        raise RuntimeError("V5 did not produce a final report")
    shutil.copyfile(report_source, output / "expert-team-report.md")

    requests = request_audit.get("requests") if isinstance(request_audit.get("requests"), list) else []
    request_count = int(request_audit.get("request_count") or len(requests))
    execution_budget = summary.get("execution_budget") if isinstance(summary.get("execution_budget"), Mapping) else {}
    calls_reserved = int(execution_budget.get("calls_reserved") or request_count)
    actual_cost = float(summary.get("actual_cost_usd") or execution_budget.get("actual_cost_usd") or 0.0)
    if not math.isfinite(actual_cost) or actual_cost < 0:
        raise RuntimeError("V5 actual cost is invalid")

    nodes = graph.get("nodes") if isinstance(graph.get("nodes"), list) else []
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
    providers = sorted({_provider_slug(value) for value in endpoints})

    normalized_audit = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": str(request_audit.get("status") or "FAIL"),
        "expected_request_count": calls_reserved,
        "captured_request_count": request_count,
        "requests": requests,
        "external_tools_allowed": False,
        "source": "v5-request-audit.json",
    }
    _write(output / "request-audit.json", normalized_audit)

    ledger = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "summary": {
            "call_count": calls_reserved,
            "provider_actual_cost_usd": round(actual_cost, 8),
            "conservative_cost_usd": round(actual_cost, 8),
            "cost_evidence_status": "provider_actual_or_runtime_reconciled",
            "substantive_providers": providers,
            "substantive_provider_count": len(providers),
            "all_providers": providers,
            "replacement_calls": int(execution_budget.get("replacements_reserved") or 0),
            "retry_calls": int(execution_budget.get("retries_reserved") or 0),
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
        "status": "PASS",
        "mode": "dynamic-v5-dag",
        "call_consumed": False,
    })

    envelope = {
        "version": 5,
        "runtime_version": RUNTIME_VERSION,
        "status": str(summary.get("status") or "failed"),
        "completion_mode": str(summary.get("completion_mode") or "none"),
        "quality_status": str(summary.get("quality_status") or "unknown"),
        "final_answer": answer,
        "actual_cost_usd": round(actual_cost, 8),
        "executor": summary.get("executor"),
        "work_coverage": summary.get("work_coverage"),
        "degradation": summary.get("degradation"),
        "execution_budget": dict(execution_budget),
        "node_count": len(nodes),
        "model_count": len(models),
        "provider_count": len(providers),
        "production_entrypoint": True,
        "v3_fallback_used": False,
        "v3_deleted": False,
        "ticket_task_id": ticket.get("task_id") if isinstance(ticket, Mapping) else None,
    }
    _write(output / "expert-team-result.json", envelope)
    _write(output / "production-runtime.json", {
        "runtime_version": RUNTIME_VERSION,
        "entrypoint": "v5_production_ticket.py",
        "hardening": "v5_production_hardening.install",
        "maximum_model_calls": MAX_MODEL_CALLS,
        "fallback_policy": "fail-closed-no-v3-fallback",
        "v3_preserved_for_manual_rollback": True,
    })
    return envelope


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Execute the hardened V5 R8 production ticket runtime")
    parser.add_argument("--task", required=True)
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--quality-tier", choices=["budget", "value", "quality"], default="value")
    parser.add_argument("--require-live-catalog", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("TOTAL_MODEL_CALLS", str(MAX_MODEL_CALLS))
    command = [
        "--task", args.task,
        "--output-dir", str(output),
        "--quality-tier", args.quality_tier,
        "--maximum-candidates-per-work", "12",
        "--solver-timeout-seconds", "20",
    ]
    if args.require_live_catalog:
        command.append("--require-live-catalog")
    try:
        code = int(v5_pipeline.main(command))
        if code != 0:
            raise RuntimeError(f"V5 pipeline returned {code}")
        envelope = _normalize(output)
        if envelope.get("status") != "success":
            raise RuntimeError("V5 production result did not pass the delivery gate")
        print(json.dumps({
            "runtime_version": RUNTIME_VERSION,
            "status": envelope["status"],
            "completion_mode": envelope["completion_mode"],
            "actual_cost_usd": envelope["actual_cost_usd"],
            "node_count": envelope["node_count"],
        }, ensure_ascii=False))
        return 0
    except Exception as exc:
        _write(output / "expert-team-error.json", {
            "version": 5,
            "runtime_version": RUNTIME_VERSION,
            "error_code": "V5_PRODUCTION_EXECUTION_FAILED",
            "stage": "v5-production-runtime",
            "message": str(exc),
            "traceback": traceback.format_exc(),
            "retryable": False,
            "v3_fallback_used": False,
        })
        raise


if __name__ == "__main__":
    raise SystemExit(main())
