#!/usr/bin/env python3
"""Benchmark-only V3 runner with real pre-call monetary bounds.

Production V3 remains unchanged. Stage-D treats 10,000 tokens as a maximum,
allocates a smaller allowance dynamically from the remaining strategy budget,
executes sequentially, and disables replacements.
"""
from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

import direct_calls
import expert_team as team
import expert_team_hardened as hardened
from model_market import ExpertTeamError, ModelInfo, SelectedExpert, estimate_call_cost

OUTPUT_ALLOWANCE_TOKENS = 10_000
MINIMUM_USEFUL_ALLOWANCE_TOKENS = 1_024
DEFAULT_HARD_CAP_USD = 0.25
_EXPERT_ALLOWANCES: dict[str, int] = {}
_JUDGE_ALLOWANCE: int | None = None


def _hard_cap() -> float:
    raw = os.getenv("STAGE_D_V3_HARD_COST_USD", str(DEFAULT_HARD_CAP_USD))
    try:
        value = float(raw)
    except ValueError as exc:
        raise ExpertTeamError("STAGE_D_V3_HARD_COST_USD must be numeric") from exc
    if not math.isfinite(value) or value <= 0:
        raise ExpertTeamError("STAGE_D_V3_HARD_COST_USD must be finite and positive")
    return value


def _provider_allowance(model: ModelInfo) -> int:
    provider_limit = int(model.max_completion_tokens or OUTPUT_ALLOWANCE_TOKENS)
    return max(MINIMUM_USEFUL_ALLOWANCE_TOKENS, min(OUTPUT_ALLOWANCE_TOKENS, provider_limit))


def _budgeted_allowance(
    model: ModelInfo,
    input_chars: int,
    available_budget_usd: float,
    safety_factor: float,
) -> int | None:
    """Return the largest allowance whose conservative estimate fits budget."""
    factor = max(1.0, float(safety_factor))
    available = max(0.0, float(available_budget_usd))
    high = _provider_allowance(model)
    minimum_cost = estimate_call_cost(model, input_chars, MINIMUM_USEFUL_ALLOWANCE_TOKENS) * factor
    if minimum_cost > available + 1e-12:
        return None
    low = MINIMUM_USEFUL_ALLOWANCE_TOKENS
    best = low
    while low <= high:
        middle = (low + high) // 2
        projected = estimate_call_cost(model, input_chars, middle) * factor
        if projected <= available + 1e-12:
            best = middle
            low = middle + 1
        else:
            high = middle - 1
    return best


def _attempt_cost(attempt: Mapping[str, Any]) -> float:
    if attempt.get("budget_denied"):
        return 0.0
    info = attempt.get("response_diagnostics")
    if isinstance(info, Mapping):
        try:
            value = info.get("cost")
            if value is not None:
                return max(0.0, float(value))
        except (TypeError, ValueError):
            pass
    try:
        return max(0.0, float(attempt.get("estimated_cost") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def _conservative_spent(results: Sequence[Any]) -> float:
    return sum(
        _attempt_cost(attempt)
        for result in results
        for attempt in (getattr(result, "attempts", []) or [])
        if isinstance(attempt, Mapping) and attempt.get("replacement") is not True
    )


def _denied_result(expert: SelectedExpert, model: ModelInfo, estimate: float, reason: str) -> direct_calls.ExpertResult:
    return direct_calls.ExpertResult(
        seat_key=expert.seat_key,
        function=expert.function,
        profession=expert.profession,
        requested_model=expert.model_id,
        resolved_model=None,
        provider=None,
        status="failed",
        answer=None,
        response_id=None,
        finish_reason=None,
        native_finish_reason=None,
        usage={},
        estimated_cost=estimate,
        latency_seconds=0.0,
        attempts=[{
            "model": model.id,
            "estimated_cost": estimate,
            "budget_denied": True,
            "call_consumed": False,
            "error": reason,
        }],
    )


def _install_payload_allowance() -> None:
    original_expert = direct_calls.build_expert_payload
    original_judge = team.build_judge_payload

    def bounded_expert(run: Any, profile: Any, expert: Any, model: ModelInfo) -> dict[str, Any]:
        payload = original_expert(run, profile, expert, model)
        payload.pop("max_completion_tokens", None)
        payload["max_tokens"] = _EXPERT_ALLOWANCES.get(expert.seat_key, MINIMUM_USEFUL_ALLOWANCE_TOKENS)
        return payload

    def bounded_judge(run: Any, profile: Any, judge: Any, model: ModelInfo, results: Sequence[Any]) -> dict[str, Any]:
        payload = original_judge(run, profile, judge, model, results)
        payload.pop("max_completion_tokens", None)
        payload["max_tokens"] = _JUDGE_ALLOWANCE or MINIMUM_USEFUL_ALLOWANCE_TOKENS
        return payload

    direct_calls.build_expert_payload = bounded_expert
    team.build_judge_payload = bounded_judge


def _install_sequential_budget_guard() -> None:
    def bounded_execute(run: Any, profile: Any, ranked: Sequence[ModelInfo], experts: Sequence[SelectedExpert]) -> list[Any]:
        by_id = {model.id: model for model in ranked}
        results: list[Any] = []
        cap = _hard_cap()
        factor = max(1.0, float(run.budget_safety_factor))
        _EXPERT_ALLOWANCES.clear()
        for index, expert in enumerate(experts):
            model = by_id[expert.model_id]
            input_chars = len(run.task) + 1200
            remaining_budget = max(0.0, cap - _conservative_spent(results))
            remaining_slots = max(1, len(experts) - index + 1)  # keep one share for the judge
            per_call_budget = remaining_budget / remaining_slots
            allowance = _budgeted_allowance(model, input_chars, per_call_budget, factor)
            if allowance is None:
                minimum_estimate = estimate_call_cost(model, input_chars, MINIMUM_USEFUL_ALLOWANCE_TOKENS)
                results.append(_denied_result(
                    expert,
                    model,
                    minimum_estimate,
                    (
                        "Stage-D V3 request denied before call: remaining equal-share budget "
                        f"${per_call_budget:.6f} cannot fund the minimum useful allowance"
                    ),
                ))
                continue
            _EXPERT_ALLOWANCES[expert.seat_key] = allowance
            estimate = estimate_call_cost(model, input_chars, allowance)
            projected = _conservative_spent(results) + estimate * factor
            if projected > cap + 1e-12:
                results.append(_denied_result(
                    expert,
                    model,
                    estimate,
                    f"Stage-D V3 request denied before call: projected conservative spend ${projected:.6f} exceeds ${cap:.6f}",
                ))
                continue
            results.append(direct_calls._attempt_expert(run, profile, expert, model))

        order = {expert.seat_key: index for index, expert in enumerate(experts)}
        results.sort(key=lambda item: order[item.seat_key])
        run.output_dir.mkdir(parents=True, exist_ok=True)
        (run.output_dir / "expert-responses.json").write_text(
            json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return results

    def bounded_pre_judge(run: Any, profile: Any, ranked: Sequence[ModelInfo], judge: Any, results: Sequence[Any]) -> float:
        global _JUDGE_ALLOWANCE
        by_id = {model.id: model for model in ranked}
        judge_model = by_id[judge.model_id]
        input_chars = len(run.task) + sum(len(getattr(result, "answer", None) or "") for result in results) + 3000
        factor = max(1.0, float(run.budget_safety_factor))
        remaining_budget = max(0.0, _hard_cap() - _conservative_spent(results))
        allowance = _budgeted_allowance(judge_model, input_chars, remaining_budget, factor)
        if allowance is None:
            raise ExpertTeamError(
                "Stage-D V3 judge denied before call: remaining budget cannot fund the minimum useful allowance"
            )
        _JUDGE_ALLOWANCE = allowance
        estimate = estimate_call_cost(judge_model, input_chars, allowance)
        projected = _conservative_spent(results) + estimate * factor
        cap = _hard_cap()
        if projected > cap + 1e-12:
            raise ExpertTeamError(
                f"Stage-D V3 judge denied before call: projected conservative spend ${projected:.6f} exceeds ${cap:.6f}"
            )
        return estimate

    team.execute_experts = bounded_execute
    team.enforce_pre_judge_budget = bounded_pre_judge


def _postprocess(output: Path) -> None:
    cap = _hard_cap()
    for filename in ("model-selection.json", "expert-team-result.json", "cost-evidence.json"):
        path = output / filename
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            data["hard_cost_limit_usd"] = cap
            data["maximum_output_allowance_tokens"] = OUTPUT_ALLOWANCE_TOKENS
            data["output_allowance_policy"] = "dynamic-equal-share-budgeted-per-call"
            data["stage_d_budget_policy"] = "sequential-conservative-pre-call-reservation"
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    audit_path = output / "request-audit.json"
    if audit_path.exists():
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            audit = {}
        if isinstance(audit, dict):
            requests = audit.get("requests") if isinstance(audit.get("requests"), list) else []
            values = [
                int(row.get("max_tokens"))
                for row in requests
                if isinstance(row, Mapping) and str(row.get("max_tokens", "")).isdigit()
            ]
            audit["approved_output_allowance_maximum_tokens"] = OUTPUT_ALLOWANCE_TOKENS
            audit["actual_request_output_allowances"] = values
            audit["output_allowance_scope"] = "Stage-D benchmark only"
            audit["output_allowance_policy"] = "dynamic-equal-share-budgeted-per-call"
            audit_path.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    ledger_path = output / "call-ledger.json"
    if ledger_path.exists():
        try:
            ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            ledger = {}
        if isinstance(ledger, dict):
            summary = ledger.get("summary") if isinstance(ledger.get("summary"), dict) else {}
            summary["hard_cost_limit_usd"] = cap
            summary["maximum_output_allowance_tokens"] = OUTPUT_ALLOWANCE_TOKENS
            summary["output_allowance_policy"] = "dynamic-equal-share-budgeted-per-call"
            ledger["summary"] = summary
            ledger_path.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


def _output_dir(argv: Sequence[str]) -> Path:
    values = list(argv)
    for index, value in enumerate(values):
        if value == "--output-dir" and index + 1 < len(values):
            return Path(values[index + 1])
        if value.startswith("--output-dir="):
            return Path(value.split("=", 1)[1])
    return Path("artifacts")


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    os.environ["EXPERT_MAX_REPLACEMENTS"] = "0"
    _install_payload_allowance()
    _install_sequential_budget_guard()
    hardened._token_ceiling_paths = lambda value, prefix="": []
    code = hardened.main(arguments)
    _postprocess(_output_dir(arguments))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
