#!/usr/bin/env python3
"""Dynamic-budget hardening layer for the bounded V5 low-cost pilot."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

import v5_live_benchmark as base
import v5_low_cost_pilot as pilot
import v5_planner

_INITIAL_V5_SHARE = 0.60
_MAXIMUM_V5_SHARE = 0.70
_PLANNING_DIAGNOSTIC: dict[str, Any] = {}
_INSTALLED = False


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def budget_plan(total_cost_usd: float, generic_strategy_cap_usd: float) -> dict[str, float]:
    total = max(0.0, float(total_cost_usd))
    generic = max(0.0, float(generic_strategy_cap_usd))
    initial = min(total, max(generic, total * _INITIAL_V5_SHARE))
    maximum = min(total, max(initial, total * _MAXIMUM_V5_SHARE))
    return {
        "total_cost_usd": round(total, 8),
        "generic_strategy_cap_usd": round(generic, 8),
        "initial_v5_planning_cap_usd": round(initial, 8),
        "maximum_v5_planning_cap_usd": round(maximum, 8),
        "minimum_reserved_for_other_strategies_usd": round(max(0.0, total - maximum), 8),
    }


def _install_dynamic_budget() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    original_compile = base.compile_and_optimize_v5

    def diagnostic_compile(*args: Any, **kwargs: Any) -> Mapping[str, Any]:
        limits = kwargs.get("limits")
        try:
            return original_compile(*args, **kwargs)
        except v5_planner.V5PlanningError as exc:
            allocation = _PLANNING_DIAGNOSTIC.get("budget_allocation")
            attempt_count = int(_PLANNING_DIAGNOSTIC.get("planning_attempt_count", 0)) + 1
            _PLANNING_DIAGNOSTIC.clear()
            if allocation is not None:
                _PLANNING_DIAGNOSTIC["budget_allocation"] = allocation
            _PLANNING_DIAGNOSTIC.update({
                "status": "budgeted-planning-failed",
                "planning_attempt_count": attempt_count,
                "budgeted_error": str(exc),
                "budgeted_max_cost_usd": getattr(limits, "max_budget_usd", None),
                "model_calls_before_failure": 0,
            })
            if limits is None or getattr(limits, "max_budget_usd", None) is None:
                _PLANNING_DIAGNOSTIC["structural_feasibility"] = "unknown-no-budget-to-remove"
                raise
            unbudgeted_limits = replace(limits, max_budget_usd=None)
            try:
                unbudgeted = original_compile(*args, **{**kwargs, "limits": unbudgeted_limits})
            except v5_planner.V5PlanningError as structural_exc:
                _PLANNING_DIAGNOSTIC.update({
                    "structural_feasibility": "infeasible-without-budget",
                    "unbudgeted_error": str(structural_exc),
                })
            else:
                graph = unbudgeted.get("optimization", {}).get("execution_graph", {})
                _PLANNING_DIAGNOSTIC.update({
                    "structural_feasibility": "feasible-without-budget",
                    "quality_band_minimum_estimated_cost_usd": float(graph.get("estimated_total_cost", 0.0) or 0.0),
                    "quality_band_node_count": len(graph.get("nodes", []) or []),
                    "quality_band_stage_count": len(graph.get("execution_stages", []) or []),
                    "selected_interpretation": unbudgeted.get("optimization", {}).get("selected_interpretation"),
                    "market_endpoint_count": unbudgeted.get("market", {}).get("endpoint_count"),
                    "candidate_count_after_pareto": unbudgeted.get("candidate_graph", {}).get("candidate_count_after_pareto"),
                })
            raise

    base.compile_and_optimize_v5 = diagnostic_compile
    original_v5 = base._v5_strategy

    def dynamically_budgeted_v5(
        task: Mapping[str, Any],
        root: Path,
        ledger: base.GlobalLedger,
        models: Mapping[str, Any],
        endpoint_cache: dict[str, Mapping[str, Any]],
        strategy_cap: float,
    ) -> tuple[base.StrategyOutcome, Mapping[str, Any]]:
        allocation = budget_plan(ledger.max_cost_usd, strategy_cap)
        initial_cap = allocation["initial_v5_planning_cap_usd"]
        maximum_cap = allocation["maximum_v5_planning_cap_usd"]
        _PLANNING_DIAGNOSTIC.clear()
        _PLANNING_DIAGNOSTIC.update({
            "budget_allocation": allocation,
            "planning_attempt_count": 0,
            "adaptive_retry_attempted": False,
        })
        try:
            outcome = original_v5(task, root, ledger, models, endpoint_cache, initial_cap)
            _PLANNING_DIAGNOSTIC.update({
                "status": "initial-budget-feasible",
                "final_v5_planning_cap_usd": initial_cap,
            })
            return outcome
        except v5_planner.V5PlanningError:
            minimum = _PLANNING_DIAGNOSTIC.get("quality_band_minimum_estimated_cost_usd")
            retry_cap = initial_cap
            if isinstance(minimum, (int, float)) and float(minimum) > 0:
                retry_cap = min(maximum_cap, max(initial_cap, float(minimum) * 1.03))
            _PLANNING_DIAGNOSTIC["adaptive_retry_cap_usd"] = round(retry_cap, 8)
            _PLANNING_DIAGNOSTIC["adaptive_retry_attempted"] = retry_cap > initial_cap + 1e-12
            if retry_cap <= initial_cap + 1e-12:
                raise
            outcome = original_v5(task, root, ledger, models, endpoint_cache, retry_cap)
            _PLANNING_DIAGNOSTIC.update({
                "status": "adaptive-budget-feasible",
                "final_v5_planning_cap_usd": round(retry_cap, 8),
            })
            return outcome

    base._v5_strategy = dynamically_budgeted_v5


def _annotate(output_dir: str | Path) -> None:
    root = Path(output_dir)
    result_path = root / "v5-low-cost-pilot-result.json"
    if not result_path.exists():
        return
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["v5_dynamic_budget"] = dict(_PLANNING_DIAGNOSTIC)
    result["v5_budget_policy"] = {
        "initial_share_of_run_ceiling": _INITIAL_V5_SHARE,
        "maximum_share_of_run_ceiling": _MAXIMUM_V5_SHARE,
        "hard_actual_run_ceiling_unchanged": True,
        "independence_constraints_relaxed": False,
        "quality_requirements_relaxed": False,
        "production_cutover_allowed": False,
    }
    _write_json(result_path, result)
    summary_path = root / "v5-low-cost-pilot-summary.md"
    if summary_path.exists():
        summary = summary_path.read_text(encoding="utf-8")
        allocation = _PLANNING_DIAGNOSTIC.get("budget_allocation", {})
        diagnostic = [
            "",
            "## V5 dynamic budget diagnostic",
            "",
            f"- Initial V5 planning cap: `${float(allocation.get('initial_v5_planning_cap_usd', 0.0)):.6f}`",
            f"- Maximum V5 planning cap: `${float(allocation.get('maximum_v5_planning_cap_usd', 0.0)):.6f}`",
            f"- Reserved for other strategies: `${float(allocation.get('minimum_reserved_for_other_strategies_usd', 0.0)):.6f}`",
            f"- Structural feasibility: `{_PLANNING_DIAGNOSTIC.get('structural_feasibility', 'not-needed')}`",
            f"- Quality-band estimated cost: `${float(_PLANNING_DIAGNOSTIC.get('quality_band_minimum_estimated_cost_usd', 0.0)):.6f}`",
            f"- Adaptive retry attempted: `{str(bool(_PLANNING_DIAGNOSTIC.get('adaptive_retry_attempted'))).lower()}`",
            f"- Final V5 planning cap: `${float(_PLANNING_DIAGNOSTIC.get('final_v5_planning_cap_usd', 0.0)):.6f}`",
            "- Independence constraints relaxed: `false`",
            "- Quality requirements relaxed: `false`",
            "- Production cutover allowed: `false`",
        ]
        summary_path.write_text(summary.rstrip() + "\n" + "\n".join(diagnostic) + "\n", encoding="utf-8")


def run(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    _install_dynamic_budget()
    code = pilot.run_pilot(config_path, suite_path, output_dir)
    _annotate(output_dir)
    return code
