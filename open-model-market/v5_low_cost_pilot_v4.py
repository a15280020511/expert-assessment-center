"""Tiered candidate-market layer for the bounded V5 low-cost pilot."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_candidate_diagnostics
import v5_candidate_diversity
import v5_live_benchmark as base
import v5_low_cost_pilot as pilot
import v5_low_cost_pilot_v2 as pilot_v2
import v5_planner

PRICE_TIERS = (
    {"name": "strict-low-cost", "prompt": 1.50, "completion": 4.00},
    {"name": "expanded-value", "prompt": 3.00, "completion": 10.00},
    {"name": "bounded-capability", "prompt": 5.00, "completion": 15.00},
)
_INSTALLED = False
_TIER_HISTORY: list[dict[str, Any]] = []


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def filter_market_for_active_tier(market: Mapping[str, Any]) -> dict[str, Any]:
    """Apply the active pilot price/reliability caps to real endpoint rows."""
    source = [row for row in market.get("endpoints", []) if isinstance(row, Mapping)]
    kept = [
        dict(row)
        for row in source
        if float(row.get("prompt_price_per_million", float("inf"))) <= float(pilot.MAX_PROMPT_PPM)
        and float(row.get("completion_price_per_million", float("inf"))) <= float(pilot.MAX_COMPLETION_PPM)
        and float(row.get("reliability", 0.0)) >= 0.80
    ]
    if not kept:
        raise v5_planner.V5PlanningError(
            "No real provider endpoint satisfies the active pilot price and reliability tier."
        )
    rejected = list(market.get("rejected", []) or [])
    rejected.extend({
        "model": str(row.get("model_id") or ""),
        "provider": str(row.get("provider_slug") or ""),
        "reason": "outside-active-pilot-price-or-reliability-tier",
    } for row in source if row not in kept)
    result = dict(market)
    result.update({
        "endpoints": kept,
        "endpoint_count": len(kept),
        "real_endpoint_count": sum(not bool(row.get("synthetic_fixture_only")) for row in kept),
        "synthetic_fixture_count": sum(bool(row.get("synthetic_fixture_only")) for row in kept),
        "rejected": rejected,
        "pilot_active_price_tier": {
            "prompt_usd_per_million": float(pilot.MAX_PROMPT_PPM),
            "completion_usd_per_million": float(pilot.MAX_COMPLETION_PPM),
            "minimum_reliability": 0.80,
        },
    })
    return result


def _install_tiered_market() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    v5_candidate_diversity.install()

    original_market_compiler = v5_planner.compile_model_endpoint_market

    def tier_filtered_market(
        ranked: Sequence[Any],
        resource_bundle: Mapping[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        compiled = original_market_compiler(ranked, resource_bundle, **kwargs)
        return filter_market_for_active_tier(compiled)

    v5_planner.compile_model_endpoint_market = tier_filtered_market
    v5_candidate_diagnostics.install()
    original_budget_installer = pilot_v2._install_dynamic_budget

    def install_budget_and_market() -> None:
        original_budget_installer()
        if getattr(base._v5_strategy, "_tiered_market_installed", False):
            return
        original_dynamic_v5 = base._v5_strategy

        def tiered_v5(
            task: Mapping[str, Any],
            root: Path,
            ledger: base.GlobalLedger,
            models: Mapping[str, Any],
            endpoint_cache: dict[str, Mapping[str, Any]],
            strategy_cap: float,
        ) -> tuple[base.StrategyOutcome, Mapping[str, Any]]:
            _TIER_HISTORY.clear()
            last_error: Exception | None = None
            for index, tier in enumerate(PRICE_TIERS, 1):
                pilot.MAX_PROMPT_PPM = float(tier["prompt"])
                pilot.MAX_COMPLETION_PPM = float(tier["completion"])
                before_calls = ledger.calls
                before_cost = ledger.actual_cost_usd
                try:
                    outcome = original_dynamic_v5(
                        task, root, ledger, models, endpoint_cache, strategy_cap
                    )
                except v5_planner.V5PlanningError as exc:
                    last_error = exc
                    structure = pilot_v2._PLANNING_DIAGNOSTIC.get("candidate_structure")
                    structural = pilot_v2._PLANNING_DIAGNOSTIC.get("structural_feasibility")
                    _TIER_HISTORY.append({
                        "tier_index": index,
                        "tier_name": tier["name"],
                        "prompt_usd_per_million": tier["prompt"],
                        "completion_usd_per_million": tier["completion"],
                        "status": "planning-infeasible",
                        "structural_feasibility": structural,
                        "candidate_structure": structure,
                        "model_calls": ledger.calls - before_calls,
                        "actual_cost_usd": round(ledger.actual_cost_usd - before_cost, 8),
                        "error": str(exc),
                    })
                    if ledger.calls != before_calls or ledger.actual_cost_usd > before_cost + 1e-12:
                        raise RuntimeError("candidate-market expansion must occur before model calls") from exc
                    # A budget-only failure with a structurally feasible market will
                    # not improve by admitting more expensive models.
                    if structural != "infeasible-without-budget":
                        pilot_v2._PLANNING_DIAGNOSTIC["market_tier_history"] = list(_TIER_HISTORY)
                        raise
                    continue
                else:
                    _TIER_HISTORY.append({
                        "tier_index": index,
                        "tier_name": tier["name"],
                        "prompt_usd_per_million": tier["prompt"],
                        "completion_usd_per_million": tier["completion"],
                        "status": "planning-feasible",
                        "model_calls_before_execution": before_calls,
                        "actual_cost_before_execution_usd": round(before_cost, 8),
                    })
                    pilot_v2._PLANNING_DIAGNOSTIC.update({
                        "market_tier_history": list(_TIER_HISTORY),
                        "effective_market_tier": dict(tier),
                        "market_expansion_changed_hard_constraints": False,
                    })
                    return outcome
            pilot_v2._PLANNING_DIAGNOSTIC.update({
                "market_tier_history": list(_TIER_HISTORY),
                "effective_market_tier": None,
                "market_expansion_changed_hard_constraints": False,
            })
            if last_error is not None:
                raise last_error
            raise v5_planner.V5PlanningError("No candidate market tier was attempted")

        tiered_v5._tiered_market_installed = True  # type: ignore[attr-defined]
        base._v5_strategy = tiered_v5

    pilot_v2._install_dynamic_budget = install_budget_and_market


def _annotate(output_dir: str | Path) -> None:
    root = Path(output_dir)
    result_path = root / "v5-low-cost-pilot-result.json"
    if not result_path.exists():
        return
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["candidate_market_policy"] = {
        "tiers": list(PRICE_TIERS),
        "attempt_history": list(_TIER_HISTORY),
        "expansion_occurs_before_model_calls": True,
        "hard_actual_run_ceiling_usd": 0.50,
        "maximum_v5_planning_cap_usd": 0.35,
        "endpoint_tier_enforced_on_real_provider_rows": True,
        "capability_thresholds_relaxed": False,
        "independence_constraints_relaxed": False,
        "quality_requirements_relaxed": False,
        "production_cutover_allowed": False,
    }
    _write_json(result_path, result)
    summary_path = root / "v5-low-cost-pilot-summary.md"
    if summary_path.exists():
        lines = ["", "## Candidate market tiers", ""]
        for row in _TIER_HISTORY:
            lines.append(
                f"- `{row['tier_name']}`: prompt `${float(row['prompt_usd_per_million']):.2f}`/M, "
                f"completion `${float(row['completion_usd_per_million']):.2f}`/M, status `{row['status']}`, "
                f"calls before expansion `{row.get('model_calls', 0)}`"
            )
        lines.extend([
            "- Real Provider Endpoint price tier enforced: `true`",
            "- Capability thresholds relaxed: `false`",
            "- Independence constraints relaxed: `false`",
            "- Quality requirements relaxed: `false`",
            "- Production cutover allowed: `false`",
        ])
        summary_path.write_text(
            summary_path.read_text(encoding="utf-8").rstrip() + "\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )


def run(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    _install_tiered_market()
    code = pilot_v2.run(config_path, suite_path, output_dir)
    _annotate(output_dir)
    return code
