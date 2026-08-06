#!/usr/bin/env python3
"""Fail-closed contract gate for admitted task-adaptive Top-50 OR-Tools tickets."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

PRIMARY_COUNT = 4
WARM_RECOVERY_COUNT = 4
MINIMUM_TOP50_CALLS = PRIMARY_COUNT + WARM_RECOVERY_COUNT
TASK_SCORING_SCHEMA_VERSION = "v5-task-adaptive-value-scoring-1"
SELECTION_PRINCIPLES = [
    "concrete-problem-concrete-analysis",
    "dynamic-adaptation",
    "small-effort-large-return",
]


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _optional_float(value: str) -> float | None:
    text = str(value or "").strip()
    if text.lower() in {"", "none", "null"}:
        return None
    number = float(text)
    if not math.isfinite(number) or number < 0:
        raise ValueError("cost advisory must be finite and nonnegative")
    return number


def _require_task_adaptive_plan(ticket: Mapping[str, Any]) -> Mapping[str, Any]:
    plan = ticket.get("governance_model_plan")
    if not isinstance(plan, Mapping):
        raise RuntimeError("materialized governance model plan is missing")
    if plan.get("selected_from_top50_reasoning_pool_only") is not True:
        raise RuntimeError("ticket is not using the frozen Top-50 path")
    if plan.get("task_adaptive_scoring_completed") is not True:
        raise RuntimeError("task-adaptive value scoring was not completed")
    if plan.get("task_adaptive_scoring_schema_version") != TASK_SCORING_SCHEMA_VERSION:
        raise RuntimeError("task-adaptive scoring schema is invalid")
    if plan.get("selection_principles") != SELECTION_PRINCIPLES:
        raise RuntimeError("task-adaptive selection principles are missing")
    profile = plan.get("task_demand_profile")
    if not isinstance(profile, Mapping):
        raise RuntimeError("current-task demand profile is missing")
    if profile.get("schema_version") != TASK_SCORING_SCHEMA_VERSION:
        raise RuntimeError("current-task demand profile schema is invalid")
    if profile.get("semantic_keyword_routing_used") is not False:
        raise RuntimeError("semantic keyword routing is forbidden")
    if profile.get("cross_task_history_used") is not False:
        raise RuntimeError("cross-task history is forbidden")
    if profile.get("provider_metric_used") is not False:
        raise RuntimeError("Provider metrics cannot affect model assignment")
    audit = plan.get("optimizer_audit")
    if not isinstance(audit, Mapping):
        raise RuntimeError("optimizer audit is missing")
    constraints = audit.get("constraints")
    if not isinstance(constraints, Mapping):
        raise RuntimeError("optimizer constraint audit is missing")
    for field in (
        "task_role_native_capacity_compatibility",
        "dynamic_role_weights_used",
        "marginal_return_used",
        "warm_recovery_priority_uses_same_objective",
    ):
        if constraints.get(field) is not True:
            raise RuntimeError(f"task-adaptive optimizer evidence missing: {field}")
    if constraints.get("provider_resilience_used") is not False:
        raise RuntimeError("Provider resilience unexpectedly affects assignment")
    return plan


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-calls", required=True, type=int)
    parser.add_argument("--expected-recovery-calls", required=True, type=int)
    parser.add_argument("--expected-cost-anomaly-usd", default="")
    args = parser.parse_args()
    root = Path(args.output_dir)
    status = _load(root / "ticket-status.json")
    ticket = _load(root / "ticket.json")
    plan = _require_task_adaptive_plan(ticket)
    expected_cost = _optional_float(args.expected_cost_anomaly_usd)
    observed_cost = status.get("cost_anomaly_usd")

    if status.get("accepted") is not True:
        raise RuntimeError("ticket was not accepted")
    if status.get("runtime_version") != "v5-governance-top50-ortools-open-provider-runtime-1":
        raise RuntimeError("ticket was not admitted for the top-50 open-provider runtime")
    if args.expected_calls < MINIMUM_TOP50_CALLS or args.expected_calls > 16:
        raise RuntimeError("top-50 execution requires 8-16 approved total calls")
    if args.expected_recovery_calls != WARM_RECOVERY_COUNT:
        raise RuntimeError("top-50 execution requires exactly four warm recovery calls")
    if int(status.get("calls") or 0) != args.expected_calls:
        raise RuntimeError("admitted total-call ceiling changed")
    if int(status.get("maximum_recovery_calls") or 0) != WARM_RECOVERY_COUNT:
        raise RuntimeError("admitted recovery reserve must equal four")
    if int(status.get("selected_expert_count") or 0) != PRIMARY_COUNT:
        raise RuntimeError("top-50 assignment must contain four primary experts")
    if int(status.get("selected_recovery_count") or 0) != WARM_RECOVERY_COUNT:
        raise RuntimeError("top-50 assignment must contain four warm recovery models")
    if int(status.get("maximum_initial_calls") or 0) != args.expected_calls - WARM_RECOVERY_COUNT:
        raise RuntimeError("initial expert capacity is inconsistent")
    if args.expected_calls - WARM_RECOVERY_COUNT < PRIMARY_COUNT:
        raise RuntimeError("ticket does not leave capacity for four primary experts")
    if status.get("optimizer") != "ortools-cp-sat":
        raise RuntimeError("ticket optimizer is not OR-Tools CP-SAT")
    if status.get("optimizer_optimality_proven") is not True:
        raise RuntimeError("ticket does not prove OR-Tools optimality")
    if plan.get("optimizer") != "ortools-cp-sat":
        raise RuntimeError("materialized plan optimizer is not OR-Tools CP-SAT")
    if plan.get("optimizer_audit", {}).get("optimality_proven") is not True:
        raise RuntimeError("materialized plan does not prove OR-Tools optimality")
    if status.get("claude_mechanism_enabled") is not False:
        raise RuntimeError("Claude mechanism is not disabled in admission evidence")
    if int(status.get("governance_model_calls") or 0) != 0:
        raise RuntimeError("governance model calls are not zero")
    if status.get("provider_routing_mode") != "unrestricted-openrouter":
        raise RuntimeError("Provider routing is not unrestricted")
    if status.get("provider_restrictions_applied") is not False:
        raise RuntimeError("Provider restrictions are present in admission evidence")
    if status.get("provider_fallback_allowed") is not True:
        raise RuntimeError("Provider fallback is not enabled")
    if status.get("unrestricted_provider_fallback_allowed") is not True:
        raise RuntimeError("unrestricted Provider fallback is not enabled")
    if status.get("openrouter_selects_provider") is not True:
        raise RuntimeError("OpenRouter is not the Provider routing authority")
    if status.get("model_substitution_allowed") is not False:
        raise RuntimeError("model substitution is unexpectedly enabled")
    if expected_cost is None:
        if observed_cost is not None:
            raise RuntimeError("unexpected cost advisory appeared after admission")
    elif not math.isclose(float(observed_cost), expected_cost, rel_tol=0, abs_tol=1e-12):
        raise RuntimeError("cost advisory changed after admission")
    route = ticket.get("route")
    if route is not None and str(route) != "expert-team":
        raise RuntimeError("ticket route is not expert-team")
    if not (root / "task.txt").is_file():
        raise RuntimeError("canonical task projection is missing")
    print(
        json.dumps(
            {
                "status": "PASS",
                "calls": args.expected_calls,
                "primary_experts": PRIMARY_COUNT,
                "warm_recoveries": WARM_RECOVERY_COUNT,
                "optimizer": "ortools-cp-sat",
                "task_adaptive_scoring": True,
                "selection_principles": SELECTION_PRINCIPLES,
                "provider_routing_mode": "unrestricted-openrouter",
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
