"""Cost-performance-first optimizer for the production dynamic resource planner.

Hard resource constraints remain mandatory. Among feasible plans, the solver
maximizes risk-adjusted task utility per effective dollar instead of first
maximizing quality and only then reducing cost inside a quality band.
"""
from __future__ import annotations

import json
import math
from typing import Any, Sequence

from ortools.sat.python import cp_model

import resource_plan_optimizer as base
from model_market import (
    ExpertTeamError,
    ModelInfo,
    RunConfig,
    SelectedExpert,
    SelectedJudge,
    TaskProfile,
    estimate_call_cost,
)

# Re-export the active planner helpers so tests and compatibility callers can
# use this module as a drop-in replacement for resource_plan_optimizer.
generate_packages = base.generate_packages
_prompt_profiles = base._prompt_profiles
_parameter_profiles = base._parameter_profiles
legacy = base.legacy
scoring = base.scoring
dynamic_runtime = base.dynamic_runtime

COST_SCALE = 1_000_000
CALL_OVERHEAD_USD = 0.0001
MAX_RATIO_ITERATIONS = 10


def _expression_value(solver: cp_model.CpSolver, expression: Any) -> int:
    return int(solver.Value(expression))


def _solve_cost_performance(
    model: cp_model.CpModel,
    quality: Any,
    effective_cost: Any,
    timeout_seconds: float,
) -> tuple[cp_model.CpSolver, int, list[str]]:
    """Maximize quality/effective-cost with a bounded Dinkelbach iteration.

    CP-SAT only accepts linear objectives. Each iteration solves the linearized
    objective ``quality * denominator - effective_cost * numerator``. The final
    tie-break minimizes effective cost while preserving the best ratio found.
    """
    statuses: list[str] = []

    model.Minimize(effective_cost)
    solver = base._solver(timeout_seconds)
    status = solver.Solve(model)
    statuses.append(f"minimum-feasible-cost:{solver.StatusName(status)}")
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise ExpertTeamError(
            f"No feasible full-dynamic resource plan: {solver.StatusName(status)}"
        )

    best_quality = max(0, _expression_value(solver, quality))
    best_cost = max(1, _expression_value(solver, effective_cost))

    for iteration in range(MAX_RATIO_ITERATIONS):
        divisor = math.gcd(best_quality, best_cost) or 1
        numerator = best_quality // divisor
        denominator = best_cost // divisor
        model.Maximize(quality * denominator - effective_cost * numerator)
        candidate_solver = base._solver(timeout_seconds)
        candidate_status = candidate_solver.Solve(model)
        statuses.append(
            f"ratio-iteration-{iteration + 1}:{candidate_solver.StatusName(candidate_status)}"
        )
        if candidate_status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            break
        candidate_quality = max(0, _expression_value(candidate_solver, quality))
        candidate_cost = max(1, _expression_value(candidate_solver, effective_cost))
        residual = candidate_quality * denominator - candidate_cost * numerator
        solver = candidate_solver
        best_quality = candidate_quality
        best_cost = candidate_cost
        if residual <= 0:
            break

    model.Add(quality * best_cost >= effective_cost * best_quality)
    model.Minimize(effective_cost)
    tie_solver = base._solver(timeout_seconds)
    tie_status = tie_solver.Solve(model)
    statuses.append(f"best-ratio-lowest-cost:{tie_solver.StatusName(tie_status)}")
    if tie_status in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        solver = tie_solver
        status = tie_status
    return solver, status, statuses


def select_team(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
) -> tuple[list[SelectedExpert], SelectedJudge, float]:
    """Select the feasible plan with the highest total cost-performance."""
    legacy._disable_history()
    requirements = base.rr.compile_requirements(profile, run)
    constraints = requirements["constraints"]
    packages = generate_packages(requirements)
    synthesis = base._synthesis(requirements)
    pool = [
        item
        for item in legacy._eligible_pool(ranked, profile)
        if item.id not in set(constraints["forbidden_models"])
    ]
    scoring._enrich_benchmarks(run, pool)
    limit = int(constraints["candidate_pool_per_work_package"])
    candidates: dict[str, list[ModelInfo]] = {}
    for package in packages + [synthesis]:
        rows = [model for model in pool if base._supports(model, package)]
        if package["id"] == "red":
            maximum = max(
                (scoring._term_fit(item, scoring.RISK_TERMS) for item in rows),
                default=0.0,
            )
            if maximum > 0:
                rows = [
                    item
                    for item in rows
                    if scoring._term_fit(item, scoring.RISK_TERMS) == maximum
                ]
        rows.sort(
            key=lambda item: (
                -scoring._benchmark_score(item, str(package["domain"])),
                -scoring._domain_fit(item, str(package["domain"])),
                -base._operation_fit(item, package),
                -legacy._live_stability(item),
                item.blended_price_per_million
                if item.blended_price_per_million is not None
                else math.inf,
                item.id,
            )
        )
        candidates[package["id"]] = rows[:limit]
    packages = [package for package in packages if candidates.get(package["id"])]
    if not candidates.get("judge"):
        raise ExpertTeamError(
            "No eligible synthesis model satisfies resource requirements."
        )

    preferred = set(constraints["preferred_models"])
    options: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for package in packages + [synthesis]:
        for model_info in candidates[package["id"]]:
            for params in _parameter_profiles(model_info, package, requirements):
                for prompt in _prompt_profiles(
                    package,
                    bool(requirements["task_signals"]["high_stakes"]),
                ):
                    chars = len(run.task) + int(prompt["token_overhead"] * 4)
                    if package["id"] == "judge":
                        chars += len(packages) * 5000 + 3000
                    option_cost = estimate_call_cost(
                        model_info,
                        chars,
                        int(params["parameters"]["expected_output_tokens"]),
                    )
                    score, parts = base._quality(
                        model_info,
                        package,
                        params,
                        prompt,
                        preferred,
                    )
                    key = (
                        package["id"],
                        model_info.id,
                        params["id"],
                        prompt["id"],
                    )
                    options[key] = {
                        "package": package,
                        "model": model_info,
                        "params": params,
                        "prompt": prompt,
                        "cost": option_cost,
                        "score": score,
                        "parts": parts,
                    }

    cp = cp_model.CpModel()
    variables = {
        key: cp.NewBoolVar(
            "x__" + "__".join(value.replace("/", "_") for value in key)
        )
        for key in options
    }
    active = {
        package["id"]: cp.NewBoolVar(f"active__{package['id']}")
        for package in packages
    }
    for package_id in active:
        cp.Add(
            sum(
                variable
                for key, variable in variables.items()
                if key[0] == package_id
            )
            == active[package_id]
        )
    cp.Add(
        sum(variable for key, variable in variables.items() if key[0] == "judge")
        == 1
    )
    for unit_id, copies in requirements["coverage_requirements"].items():
        covering = [
            active[package["id"]]
            for package in packages
            if unit_id in package["unit_ids"]
        ]
        if not covering:
            raise ExpertTeamError(f"No work package covers atomic unit {unit_id}.")
        cp.Add(sum(covering) == int(copies))
    cp.Add(sum(active.values()) >= int(constraints["min_experts"]))
    dynamic_max = sum(
        int(value) for value in requirements["coverage_requirements"].values()
    )
    if constraints["max_experts"] is not None:
        dynamic_max = min(dynamic_max, int(constraints["max_experts"]))
    cp.Add(sum(active.values()) <= dynamic_max)
    for model_id in {key[1] for key in variables}:
        cp.Add(
            sum(
                variable
                for key, variable in variables.items()
                if key[1] == model_id
            )
            <= 1
        )
    if constraints["strict_provider_diversity"]:
        providers: dict[str, list[Any]] = {}
        for key, variable in variables.items():
            providers.setdefault(options[key]["model"].author, []).append(variable)
        for rows in providers.values():
            cp.Add(sum(rows) <= 1)

    quality = sum(
        options[key]["score"] * variable for key, variable in variables.items()
    )
    actual_cost = sum(
        int(round(options[key]["cost"] * COST_SCALE)) * variable
        for key, variable in variables.items()
    )
    call_count = sum(active.values()) + 1
    call_overhead = max(1, int(round(CALL_OVERHEAD_USD * COST_SCALE)))
    effective_cost = actual_cost + call_count * call_overhead
    if constraints["budget_usd"] is not None:
        cp.Add(
            actual_cost
            <= int(round(float(constraints["budget_usd"]) * COST_SCALE))
        )

    timeout = float(constraints["solver_timeout_seconds"])
    solver, status, phase_status = _solve_cost_performance(
        cp,
        quality,
        effective_cost,
        timeout,
    )

    selected = {
        key[0]: {**options[key]}
        for key, variable in variables.items()
        if solver.Value(variable)
    }
    rows = [selected[package["id"]] for package in packages if package["id"] in selected]
    rows.sort(
        key=lambda row: (
            0
            if requirements["atomic_work_units"][0]["id"]
            in row["package"]["unit_ids"]
            else 1,
            0 if row["package"]["id"] == "red" else 1,
            row["package"]["id"],
        )
    )

    experts: list[SelectedExpert] = []
    plan_selected: dict[str, Any] = {}
    for index, row in enumerate(rows, 1):
        package = row["package"]
        seat_key = "red" if package["id"] == "red" else f"expert_{index:02d}"
        reason = (
            f"资源需求先行+CP-SAT性价比优化；原子工作={','.join(package['unit_ids'])}；"
            f"提示词模块={','.join(row['prompt']['modules'])}；参数={row['params']['id']}；"
            f"风险调整效用={row['parts']['weighted_utility']:.3f}；预计成本=${row['cost']:.6f}"
        )
        experts.append(
            SelectedExpert(
                seat_key,
                package["function"],
                package["profession"],
                package["domain"],
                package["mission"],
                row["model"].id,
                reason,
            )
        )
        plan_selected[seat_key] = {
            "seat": seat_key,
            "work_package_id": package["id"],
            "function": package["function"],
            "profession": package["profession"],
            "domain": package["domain"],
            "atomic_work_units": package["unit_ids"],
            "operations": package["operations"],
            "model": row["model"].id,
            "provider": row["model"].author,
            "parameter_template": row["params"]["id"],
            "resource_profile_id": row["params"]["id"],
            "parameters": row["params"]["parameters"],
            "prompt_profile_id": row["prompt"]["id"],
            "prompt_modules": row["prompt"]["modules"],
            "prompt_instructions": row["prompt"]["instructions"],
            "prompt_token_overhead": row["prompt"]["token_overhead"],
            "estimated_cost_usd": round(row["cost"], 9),
            "score_components": row["parts"],
        }

    judge_row = selected["judge"]
    judge = SelectedJudge(
        synthesis["function"],
        synthesis["profession"],
        judge_row["model"].id,
        (
            f"动态综合资源；性价比优先；提示词={','.join(judge_row['prompt']['modules'])}；"
            f"参数={judge_row['params']['id']}；预计成本=${judge_row['cost']:.6f}"
        ),
    )
    plan_selected["judge"] = {
        "seat": "judge",
        "work_package_id": "judge",
        "function": synthesis["function"],
        "profession": synthesis["profession"],
        "domain": synthesis["domain"],
        "atomic_work_units": [],
        "operations": synthesis["operations"],
        "model": judge_row["model"].id,
        "provider": judge_row["model"].author,
        "parameter_template": judge_row["params"]["id"],
        "resource_profile_id": judge_row["params"]["id"],
        "parameters": judge_row["params"]["parameters"],
        "prompt_profile_id": judge_row["prompt"]["id"],
        "prompt_modules": judge_row["prompt"]["modules"],
        "prompt_instructions": judge_row["prompt"]["instructions"],
        "prompt_token_overhead": judge_row["prompt"]["token_overhead"],
        "estimated_cost_usd": round(judge_row["cost"], 9),
        "score_components": judge_row["parts"],
    }

    estimated = sum(float(row["cost"]) for row in selected.values())
    selected_quality = max(0, _expression_value(solver, quality))
    selected_effective_cost = max(1, _expression_value(solver, effective_cost))
    ratio = selected_quality / selected_effective_cost
    plan = {
        "version": 3,
        "optimizer": "google-or-tools-cp-sat",
        "selection_method": "resource-demand-first-cost-performance-v3",
        "architecture": {
            "stage_a": "task_to_atomic_work_prompt_capability_parameter_demands",
            "stage_b": "live_market_joint_cost_performance_optimization",
            "execution_graph": "parallel_dynamic_work_packages_then_dynamic_synthesis",
        },
        "solver_status": solver.StatusName(status),
        "phase_status": phase_status,
        "highest_principle": "maximum_cost_performance",
        "objective_order": [
            "hard_resource_coverage",
            "maximum_cost_performance",
        ],
        "cost_performance_definition": (
            "risk_adjusted_task_utility_divided_by_estimated_model_cost_plus_call_overhead"
        ),
        "selected_quality_score": selected_quality,
        "selected_effective_cost_scaled": selected_effective_cost,
        "cost_scale": COST_SCALE,
        "call_overhead_usd": CALL_OVERHEAD_USD,
        "cost_performance_ratio": round(ratio, 9),
        "deprecated_quality_tolerance_pct_ignored": constraints.get(
            "quality_tolerance_pct"
        ),
        "resource_requirements": requirements,
        "candidate_work_packages": packages,
        "team_pattern": f"{len(experts)}-dynamic-work-packages-plus-synthesis",
        "expert_count": len(experts),
        "estimated_cost_usd": round(estimated, 9),
        "selected": plan_selected,
        "constraints": {
            "atomic_work_coverage": requirements["coverage_requirements"],
            "model_reuse_forbidden": True,
            "provider_reuse_forbidden": constraints["strict_provider_diversity"],
            "intelligence_rank_hard_ceiling": scoring.MAX_INTELLIGENCE_RANK,
            "live_stable_direct_models_only": True,
            "budget_usd": constraints["budget_usd"],
            "history_input_used": False,
            "fixed_team_mode_used": False,
            "fixed_seat_template_used": False,
            "fixed_prompt_template_used": False,
            "fixed_parameter_template_used": False,
            "external_tools_allowed": False,
        },
        "requested_market_attributes": requirements["requested_market_attributes"],
        "candidate_counts": {key: len(value) for key, value in candidates.items()},
        "fallback_used": False,
    }
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "task-resource-requirements.json").write_text(
        json.dumps(requirements, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run.output_dir / "task-parameter-matrix.json").write_text(
        json.dumps(requirements, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run.output_dir / "team-optimization.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    object.__setattr__(profile, "team_pattern", plan["team_pattern"])
    object.__setattr__(profile, "expert_count", len(experts))
    if len(experts) != 3:
        object.__setattr__(run, "require_all_experts", False)
    dynamic_runtime.activate_runtime(plan, run, profile, experts, judge)
    return experts, judge, estimated
