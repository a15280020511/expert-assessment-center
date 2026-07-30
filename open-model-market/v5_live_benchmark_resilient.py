#!/usr/bin/env python3
"""R7 entry: R6 controls plus the production-resilient V5 executor."""
from __future__ import annotations

import sys
from dataclasses import replace
from typing import Any, Sequence

import v5_live_benchmark_economy as economy
import v5_live_benchmark_economy_r6 as r6
from v5_resilient_executor import execute_v5_graph

_INSTALLED = False


def planning_limits(limits: Any) -> Any:
    """Reserve the runtime cost-risk envelope while selecting the graph."""
    hard_budget = getattr(limits, "max_budget_usd", None)
    if hard_budget is None:
        return limits
    multiplier = max(1.0, float(getattr(limits, "cost_risk_multiplier", 4.0)))
    return replace(limits, max_budget_usd=float(hard_budget) / multiplier)


def install_resilient_alignment() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    r6.install_r6_alignment()
    original_compile = economy.base.compile_and_optimize_v5

    def risk_bounded_compile(*args: Any, **kwargs: Any) -> Any:
        runtime_limits = kwargs.get("limits")
        if runtime_limits is not None:
            kwargs["limits"] = planning_limits(runtime_limits)
        result = original_compile(*args, **kwargs)
        if runtime_limits is not None and isinstance(result, dict):
            optimization = result.get("optimization")
            if isinstance(optimization, dict):
                optimization["planning_budget_usd"] = getattr(kwargs["limits"], "max_budget_usd", None)
                optimization["runtime_hard_budget_usd"] = getattr(runtime_limits, "max_budget_usd", None)
                optimization["cost_risk_multiplier"] = getattr(runtime_limits, "cost_risk_multiplier", 4.0)
        return result

    economy.base.compile_and_optimize_v5 = risk_bounded_compile
    economy.base.execute_v5_graph = execute_v5_graph


def main(argv: Sequence[str] | None = None) -> int:
    install_resilient_alignment()
    return economy.hardened.main(list(argv if argv is not None else sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
