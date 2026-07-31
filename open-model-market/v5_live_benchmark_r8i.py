#!/usr/bin/env python3
"""R8I Stage-D benchmark with truthful external accounting and bounded V3."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_live_benchmark as base
import v5_live_benchmark_r8 as r8

MAX_GLOBAL_CALLS = 57
MAX_STRATEGY_COST_USD = 0.25
OUTPUT_ALLOWANCE_TOKENS = 10_000
V3_MAX_PAID_CALLS = 7
BLIND_JUDGE_MIN_CALLS = 2
_INSTALLED = False


def _truthful_add_external(
    ledger: base.GlobalLedger,
    *,
    task_id: str,
    strategy: str,
    calls: int,
    cost_usd: float,
) -> None:
    """Account paid subprocess usage before enforcing global ceilings."""
    call_value = max(0, int(calls))
    cost_value = max(0.0, float(cost_usd))
    ledger.calls += call_value
    ledger.actual_cost_usd += cost_value
    call_exceeded = ledger.calls > ledger.max_calls
    cost_exceeded = ledger.actual_cost_usd > ledger.max_cost_usd + 1e-12
    ledger.events.append({
        "kind": "external_strategy_accounted_truthfully",
        "task_id": task_id,
        "strategy": strategy,
        "calls": call_value,
        "cost_usd": round(cost_value, 8),
        "cumulative_calls": ledger.calls,
        "cumulative_cost_usd": round(ledger.actual_cost_usd, 8),
        "call_ceiling_exceeded": call_exceeded,
        "cost_ceiling_exceeded": cost_exceeded,
    })
    if call_exceeded:
        raise base.BenchmarkLimitExceeded(
            f"external strategy caused global calls {ledger.calls} to exceed ceiling {ledger.max_calls}"
        )
    if cost_exceeded:
        raise base.BenchmarkLimitExceeded(
            f"external strategy caused total cost {ledger.actual_cost_usd:.6f} USD to exceed ceiling {ledger.max_cost_usd:.6f} USD"
        )


def _load(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, Mapping) else {}


def _v3_models(root: Path) -> list[str]:
    selection = _load(root / "model-selection.json")
    values: list[str] = []
    for row in selection.get("experts", []) if isinstance(selection.get("experts"), list) else []:
        if isinstance(row, Mapping):
            model = row.get("model_id") or row.get("model")
            if model:
                values.append(str(model))
    judge = selection.get("judge") if isinstance(selection.get("judge"), Mapping) else {}
    model = judge.get("model_id") or judge.get("model")
    if model:
        values.append(str(model))
    return sorted(set(values))


def _bounded_v3_strategy(
    task: Mapping[str, Any],
    root: Path,
    ledger: base.GlobalLedger,
    strategy_cap: float,
) -> base.StrategyOutcome:
    task_id = str(task["task_id"])
    root.mkdir(parents=True, exist_ok=True)

    # Reserve enough global call capacity for the complete V3 subprocess and
    # the minimum two independent blind judges before any V3 request is sent.
    required = V3_MAX_PAID_CALLS + BLIND_JUDGE_MIN_CALLS
    if ledger.calls + required > ledger.max_calls:
        raise base.BenchmarkLimitExceeded(
            f"V3 preflight requires {required} remaining calls; only {ledger.max_calls - ledger.calls} remain"
        )
    if ledger.remaining_cost() <= 0:
        raise base.BenchmarkLimitExceeded("no global monetary capacity remains for V3")

    task_text = base._task_text(task)
    (root / "task.txt").write_text(task_text, encoding="utf-8")
    command = [
        sys.executable,
        str(base.HERE / "v3_stage_d_bounded.py"),
        "--task",
        task_text,
        "--quality-tier",
        "value",
        "--require-live-catalog",
        "--max-estimated-cost-usd",
        str(min(float(strategy_cap), MAX_STRATEGY_COST_USD)),
        "--max-completion-tokens",
        str(OUTPUT_ALLOWANCE_TOKENS),
        "--output-dir",
        str(root),
    ]
    env = os.environ.copy()
    env["TOTAL_MODEL_CALLS"] = "6"
    env["EXPERT_MAX_REPLACEMENTS"] = "0"
    env["STAGE_D_V3_HARD_COST_USD"] = str(min(float(strategy_cap), MAX_STRATEGY_COST_USD))
    started = time.monotonic()
    completed = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=2400,
        check=False,
    )
    (root / "benchmark-subprocess.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (root / "benchmark-subprocess.stderr.log").write_text(completed.stderr, encoding="utf-8")

    cost = float(base._parse_v3_cost(root))
    calls = int(base._parse_v3_calls(root))
    ledger.add_external(task_id=task_id, strategy="v3", calls=calls, cost_usd=cost)

    result = _load(root / "expert-team-result.json")
    audit = _load(root / "request-audit.json")
    call_ledger = _load(root / "call-ledger.json")
    summary = call_ledger.get("summary") if isinstance(call_ledger.get("summary"), Mapping) else {}
    conservative = float(summary.get("conservative_cost_usd") or cost)
    provider_actual = float(summary.get("provider_actual_cost_usd") or cost)
    providers = [str(value) for value in summary.get("substantive_providers", [])] if isinstance(summary.get("substantive_providers"), list) else []
    answer = str(result.get("final_answer") or "").strip()

    cap = min(float(strategy_cap), MAX_STRATEGY_COST_USD)
    cap_ok = provider_actual <= cap + 1e-12 and conservative <= cap + 1e-12
    success = completed.returncode == 0 and len(answer) >= 160 and cap_ok
    if not cap_ok:
        error = (
            f"V3 Stage-D cost acceptance failed: provider_actual=${provider_actual:.8f}, "
            f"conservative=${conservative:.8f}, cap=${cap:.8f}"
        )
    elif completed.returncode != 0:
        error = completed.stderr[-2000:] or "V3 bounded execution failed"
    elif len(answer) < 160:
        error = "V3 bounded execution produced no usable final answer"
    else:
        error = None

    return base.StrategyOutcome(
        task_id=task_id,
        strategy="v3",
        status="success" if success else "failed",
        answer=answer or None,
        actual_cost_usd=round(provider_actual, 8),
        latency_seconds=round(time.monotonic() - started, 6),
        call_count=calls,
        models=_v3_models(root),
        providers=providers,
        safety_failure=bool(audit and audit.get("status") != "PASS"),
        error=error,
        artifacts={
            "returncode": completed.returncode,
            "request_audit": dict(audit),
            "conservative_cost_usd": round(conservative, 8),
            "hard_cost_limit_usd": cap,
            "output_allowance_tokens": OUTPUT_ALLOWANCE_TOKENS,
            "replacement_calls_allowed": 0,
        },
    )


def install_r8i() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    r8.install_r8_stage_d()
    r8.MAX_GLOBAL_CALLS = MAX_GLOBAL_CALLS
    base.GlobalLedger.add_external = _truthful_add_external
    base._v3_strategy = _bounded_v3_strategy


def main(argv: Sequence[str] | None = None) -> int:
    install_r8i()
    return r8.economy.hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
