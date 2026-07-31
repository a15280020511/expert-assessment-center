#!/usr/bin/env python3
"""R8 Stage-D benchmark with truthful accounting and bounded dynamic allowances."""
from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_executor as executor
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
    """Account external subprocess usage before enforcing global ceilings."""
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


def _positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _allowance_field(supported_parameters: Sequence[Any]) -> str:
    supported = {str(value).casefold() for value in supported_parameters}
    return "max_completion_tokens" if "max_completion_tokens" in supported else "max_tokens"


def _dynamic_node_allowance(node: Any) -> int:
    """Use the optimizer's node-specific recommendation, capped at 10,000."""
    profile = node.parameter_profile if isinstance(getattr(node, "parameter_profile", None), Mapping) else {}
    recommended = _positive_int(profile.get("recommended_output_allowance_tokens"))
    estimated = _positive_int(profile.get("estimated_completion_usage_tokens"))
    if recommended is None and estimated is not None:
        recommended = int(math.ceil(estimated * 1.20))
    if recommended is None:
        control = profile.get("dynamic_parameter_decisions")
        control_score = float(control.get("control_score", 0.5)) if isinstance(control, Mapping) else 0.5
        recommended = int(round(1800 + max(0.0, min(1.0, control_score)) * 4200))
    return max(1024, min(OUTPUT_ALLOWANCE_TOKENS, recommended))


def _dynamic_judge_allowance(endpoint: Mapping[str, Any], system: str, user: str) -> int:
    """Derive a bounded judge allowance from input size and endpoint price."""
    input_tokens = max(1, math.ceil((len(system) + len(user)) / 4))
    content_need = 1400 + min(3600, math.ceil(input_tokens * 0.35))
    try:
        completion_ppm = float(endpoint.get("completion_price_per_million") or 0.0)
    except (TypeError, ValueError):
        completion_ppm = 0.0
    # Keep the maximum completion reservation near four cents per judge while
    # retaining a usable minimum. This is a request ceiling, not expected use.
    price_bound = int(0.04 * 1_000_000 / completion_ppm) if completion_ppm > 0 else OUTPUT_ALLOWANCE_TOKENS
    provider_bound = _positive_int(endpoint.get("max_completion_tokens")) or OUTPUT_ALLOWANCE_TOKENS
    return max(1024, min(OUTPUT_ALLOWANCE_TOKENS, provider_bound, content_need, max(1024, price_bound)))


def _install_dynamic_output_allowance() -> None:
    """Replace the legacy fixed-10k benchmark override with dynamic ceilings."""
    current_node_payload = executor.build_node_payload
    current_safe_payload = base._safe_payload
    hardened = r8.economy.hardened

    def dynamic_node_payload(node: Any, original_task: str, upstream: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        payload = current_node_payload(node, original_task, upstream)
        supported = node.parameter_profile.get("supported_parameters", []) if isinstance(node.parameter_profile, Mapping) else []
        field = _allowance_field(supported)
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
        payload[field] = _dynamic_node_allowance(node)
        return payload

    def dynamic_safe_payload(endpoint: Mapping[str, Any], system: str, user: str) -> dict[str, Any]:
        payload = current_safe_payload(endpoint, system, user)
        field = _allowance_field(endpoint.get("supported_parameters", []))
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
        payload[field] = _dynamic_judge_allowance(endpoint, system, user)
        return payload

    def annotate_dynamic_audit(output_dir: str | Path | None) -> None:
        if output_dir is None:
            return
        audit_path = Path(output_dir) / "v5-request-audit.json"
        if not audit_path.exists():
            return
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        requests = audit.get("requests") if isinstance(audit.get("requests"), list) else []
        values: list[int] = []
        fields: list[str] = []
        valid = bool(requests)
        for row in requests:
            if not isinstance(row, Mapping):
                valid = False
                continue
            field = "max_completion_tokens" if row.get("max_completion_tokens") is not None else "max_tokens" if row.get("max_tokens") is not None else ""
            value = _positive_int(row.get(field)) if field else None
            if not field or value is None or value > OUTPUT_ALLOWANCE_TOKENS:
                valid = False
                continue
            fields.append(field)
            values.append(value)
        audit["benchmark_output_allowance_maximum_tokens"] = OUTPUT_ALLOWANCE_TOKENS
        audit["benchmark_output_allowance_values"] = values
        audit["benchmark_output_allowance_parameters"] = sorted(set(fields))
        audit["benchmark_output_allowance_policy"] = "dynamic-per-request-capped-at-maximum-not-required-output"
        audit["benchmark_output_allowance_consistent"] = valid
        audit["fixed_10000_request_allowance_used"] = bool(values) and all(value == OUTPUT_ALLOWANCE_TOKENS for value in values)
        audit["production_policy_changed"] = False
        audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    executor.build_node_payload = dynamic_node_payload
    base._safe_payload = dynamic_safe_payload
    hardened._annotate_v5_audit = annotate_dynamic_audit


def _single_key_credit_preflight(config_path: str | Path, output_dir: str | Path) -> int:
    """Preserve single-key policy while stating account-balance uncertainty."""
    code = r8.credit_preflight(config_path, output_dir)
    path = Path(output_dir) / "credit-preflight.json"
    report = dict(_load(path))
    current = report.get("current_key") if isinstance(report.get("current_key"), Mapping) else {}
    if current.get("limit_remaining_usd") is None:
        report["status"] = "ordinary-key-account-balance-unverified-runtime-402-guard"
        report["account_balance_verified"] = False
        report["warning"] = (
            "The ordinary inference key does not report account-level remaining credits. "
            "Runtime HTTP 402 fail-fast remains authoritative."
        )
    else:
        report["account_balance_verified"] = False
        report["warning"] = (
            "API-key limit remaining is known, but account-level credits are not exposed by the ordinary inference key."
        )
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return code


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
            "maximum_output_allowance_tokens": OUTPUT_ALLOWANCE_TOKENS,
            "output_allowance_policy": "dynamic-per-call-budgeted-not-required-output",
            "replacement_calls_allowed": 0,
        },
    )


def install_r8i() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    r8.install_r8_stage_d()
    _install_dynamic_output_allowance()
    r8.MAX_GLOBAL_CALLS = MAX_GLOBAL_CALLS
    base.GlobalLedger.add_external = _truthful_add_external
    base._v3_strategy = _bounded_v3_strategy
    r8.economy.hardened.credit_preflight = _single_key_credit_preflight


def main(argv: Sequence[str] | None = None) -> int:
    install_r8i()
    return r8.economy.hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
