#!/usr/bin/env python3
"""Deterministic zero-call simulation for V5 budget parity and benchmark stop-loss."""
from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


@dataclass(frozen=True)
class Scenario:
    name: str
    raw_graph_cost_usd: float
    hard_budget_usd: float
    cost_risk_multiplier: float
    provider_rebalance_delta_usd: float
    provider_diversity_required: bool = False
    old_downstream_calls_after_v5_failure: int = 3
    old_downstream_cost_usd: float = 0.2562488367


def evaluate(scenario: Scenario) -> dict[str, object]:
    multiplier = max(1.0, float(scenario.cost_risk_multiplier))
    raw_budget = float(scenario.hard_budget_usd) / multiplier
    planner_feasible = scenario.raw_graph_cost_usd <= raw_budget + 1e-12
    rebalanced_cost = scenario.raw_graph_cost_usd + max(
        0.0, scenario.provider_rebalance_delta_usd
    )
    rebalance_budget_safe = rebalanced_cost <= raw_budget + 1e-12
    chosen_cost = rebalanced_cost if rebalance_budget_safe else scenario.raw_graph_cost_usd
    provider_policy_pass = rebalance_budget_safe or not scenario.provider_diversity_required
    runtime_risk_cost = chosen_cost * multiplier
    runtime_preflight_pass = (
        planner_feasible
        and provider_policy_pass
        and runtime_risk_cost <= scenario.hard_budget_usd + 1e-12
    )
    old_downstream_calls = (
        0 if runtime_preflight_pass else scenario.old_downstream_calls_after_v5_failure
    )
    old_wasted_cost = 0.0 if runtime_preflight_pass else scenario.old_downstream_cost_usd
    new_downstream_calls = 0 if not runtime_preflight_pass else None
    new_wasted_cost = 0.0
    return {
        **asdict(scenario),
        "planning_raw_budget_usd": round(raw_budget, 8),
        "planner_feasible_under_parity": planner_feasible,
        "provider_rebalance_budget_safe": rebalance_budget_safe,
        "provider_policy_pass": provider_policy_pass,
        "chosen_raw_cost_usd": round(chosen_cost, 8),
        "runtime_risk_adjusted_cost_usd": round(runtime_risk_cost, 8),
        "runtime_preflight_pass": runtime_preflight_pass,
        "old_workflow_downstream_calls_after_failed_v5": old_downstream_calls,
        "old_workflow_wasted_downstream_cost_usd": round(old_wasted_cost, 8),
        "new_workflow_downstream_calls_after_failed_v5": new_downstream_calls,
        "new_workflow_wasted_downstream_cost_usd": new_wasted_cost,
        "paid_execution_allowed": runtime_preflight_pass,
    }


def default_scenarios() -> tuple[Scenario, ...]:
    return (
        Scenario(
            name="r8h-parity-gap",
            raw_graph_cost_usd=0.205,
            hard_budget_usd=0.25,
            cost_risk_multiplier=1.35,
            provider_rebalance_delta_usd=0.018,
            old_downstream_calls_after_v5_failure=3,
            old_downstream_cost_usd=0.2562488367,
        ),
        Scenario(
            name="budget-safe-soft-provider-diversity",
            raw_graph_cost_usd=0.160,
            hard_budget_usd=0.25,
            cost_risk_multiplier=1.35,
            provider_rebalance_delta_usd=0.010,
        ),
        Scenario(
            name="soft-diversity-expensive-alternative-keeps-original",
            raw_graph_cost_usd=0.170,
            hard_budget_usd=0.25,
            cost_risk_multiplier=1.35,
            provider_rebalance_delta_usd=0.050,
        ),
        Scenario(
            name="strict-diversity-no-budget-safe-alternative",
            raw_graph_cost_usd=0.170,
            hard_budget_usd=0.25,
            cost_risk_multiplier=1.35,
            provider_rebalance_delta_usd=0.050,
            provider_diversity_required=True,
        ),
    )


def simulate(scenarios: Sequence[Scenario] | None = None) -> dict[str, object]:
    rows = [evaluate(row) for row in (scenarios or default_scenarios())]
    return {
        "version": 1,
        "simulation": "v5-r8-budget-parity-provider-rebalance-stop-loss",
        "model_inference_calls": 0,
        "actual_model_cost_usd": 0.0,
        "scenarios": rows,
        "aggregate": {
            "scenario_count": len(rows),
            "blocked_before_paid_execution": sum(
                not bool(row["paid_execution_allowed"]) for row in rows
            ),
            "old_wasted_downstream_calls": sum(
                int(row["old_workflow_downstream_calls_after_failed_v5"] or 0)
                for row in rows
            ),
            "new_wasted_downstream_calls": sum(
                int(row["new_workflow_downstream_calls_after_failed_v5"] or 0)
                for row in rows
            ),
            "old_wasted_downstream_cost_usd": round(
                sum(float(row["old_workflow_wasted_downstream_cost_usd"]) for row in rows),
                8,
            ),
            "new_wasted_downstream_cost_usd": 0.0,
        },
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the zero-call R8 preflight simulation")
    parser.add_argument("--output")
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = simulate()
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
