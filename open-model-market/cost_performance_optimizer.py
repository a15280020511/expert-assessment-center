"""Production resource planner with cost-performance as the highest objective.

This module reuses the established resource-demand compiler and candidate
construction from ``resource_plan_optimizer`` but replaces its former
quality-first/quality-band solve with direct fractional optimization.
"""
from __future__ import annotations

import json
from typing import Any, Sequence

import resource_plan_optimizer as base
from cost_performance_solver import RATIO_SCALE, solve_cost_performance
from model_market import (
    ExpertTeamError,
    ModelInfo,
    RunConfig,
    SelectedExpert,
    SelectedJudge,
    TaskProfile,
    estimate_call_cost,
)

COST_SCALE = 1_000_000_000
ZERO_PRICE_GUARD_UNITS_PER_CALL = 1_000  # one micro-dollar in nanodollar units


def select_team(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
) -> tuple[list[SelectedExpert], SelectedJudge, float]:
    """Select the feasible plan with the highest utility-per-total-cost ratio."""
    base.legacy._disable_history()
    requirements = dict(base.rr.compile_requirements(profile, run))
    constraints = dict(requirements["constraints"])
    # The old quality-band control is deliberately removed from active inputs
    # and audit artifacts. It is not used by this optimizer.
    constraints.pop("quality_tolerance_pct", None)
    requirements["constraints"] = constraints

    packages = base.generate_packages(requirements)
    synthesis = base._synthesis(requirements)
    forbidden = set(constraints["forbidden_models"])
    pool = [x for x in base.legacy._eligible_pool(ranked, profile) if x.id not in forbidden]
    base.scoring._enrich_benchmarks(run, pool)
    limit = int(constraints["candidate_pool_per_work_package"])

    candidates: dict[str, list[ModelInfo]] = {}
    for package in packages + [synthesis]:
        rows = [model for model in pool if base._supports(model, package)]
        if package["id"] == "red":
            maximum = max((base.scoring._term_fit(x, base.scoring.RISK_TERMS) for x in rows), default=0.0)
            if maximum > 0:
                rows = [x for x in rows if base.scoring._term_fit(x, base.scoring.RISK_TERMS) == maximum]
        rows.sort(key=lambda x: (
            -base.scoring._benchmark_score(x, str(package["domain"])),
            -base.scoring._domain_fit(x, str(package["domain"])),
            -base._operation_fit(x, package),
            -base.legacy._live_stability(x),
            x.blended_price_per_million if x.blended_price_per_million is not None else base.math.inf,
            x.id,
        ))
        candidates[package["id"]] = rows[:limit]

    packages = [x for x in packages if candidates.get(x["id"])]
    if not candidates.get("judge"):
        raise ExpertTeamError("No eligible synthesis model satisfies resource requirements.")

    preferred = set(constraints["preferred_models"])
    options: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for package in packages + [synthesis]:
        for model in candidates[package["id"]]:
            for params in base._parameter_profiles(model, package, requirements):
                for prompt in base._prompt_profiles(package, bool(requirements["task_signals"]["high_stakes"])):
                    chars = len(run.task) + int(prompt["token_overhead"] * 4)
                    if package["id"] == "judge":
                        chars += len(packages) * 5000 + 3000
                    option_cost = estimate_call_cost(
                        model,
                        chars,
                        int(params["parameters"]["expected_output_tokens"]),
                    )
                    score, parts = base._quality(model, package, params, prompt, preferred)
                    key = (package["id"], model.id, params["id"], prompt["id"])
                    options[key] = {
                        "package": package,
                        "model": model,
                        "params": params,
                        "prompt": prompt,
                        "cost": option_cost,
                        "score": score,
                        "parts": parts,
                    }

    cp = base.cp_model.CpModel()
    variables = {
        key: cp.NewBoolVar("x__" + "__".join(value.replace("/", "_") for value in key))
        for key in options
    }
    active = {row["id"]: cp.NewBoolVar(f"active__{row['id']}") for row in packages}
    for package_id in active:
        cp.Add(sum(var for key, var in variables.items() if key[0] == package_id) == active[package_id])
    cp.Add(sum(var for key, var in variables.items() if key[0] == "judge") == 1)

    for unit_id, copies in requirements["coverage_requirements"].items():
        covering = [active[row["id"]] for row in packages if unit_id in row["unit_ids"]]
        if not covering:
            raise ExpertTeamError(f"No work package covers atomic unit {unit_id}.")
        cp.Add(sum(covering) == int(copies))

    cp.Add(sum(active.values()) >= int(constraints["min_experts"]))
    dynamic_max = sum(int(value) for value in requirements["coverage_requirements"].values())
    if constraints["max_experts"] is not None:
        dynamic_max = min(dynamic_max, int(constraints["max_experts"]))
    cp.Add(sum(active.values()) <= dynamic_max)

    for model_id in {key[1] for key in variables}:
        cp.Add(sum(var for key, var in variables.items() if key[1] == model_id) <= 1)
    if constraints["strict_provider_diversity"]:
        providers: dict[str, list[Any]] = {}
        for key, var in variables.items():
            providers.setdefault(options[key]["model"].author, []).append(var)
        for rows in providers.values():
            cp.Add(sum(rows) <= 1)

    utility_expr = sum(options[key]["score"] * var for key, var in variables.items())
    cost_expr = sum(
        int(round(options[key]["cost"] * COST_SCALE)) * var
        for key, var in variables.items()
    )
    call_count_expr = sum(active.values()) + 1
    effective_cost_expr = cost_expr + call_count_expr * ZERO_PRICE_GUARD_UNITS_PER_CALL
    if constraints["budget_usd"] is not None:
        cp.Add(cost_expr <= int(round(float(constraints["budget_usd"]) * COST_SCALE)))

    try:
        solved = solve_cost_performance(
            cp,
            numerator_expr=utility_expr,
            denominator_expr=effective_cost_expr,
            actual_cost_expr=cost_expr,
            call_count_expr=call_count_expr,
            tie_break_penalty_expr=cost_expr * 100 + call_count_expr * 10_000,
            timeout_seconds=float(constraints["solver_timeout_seconds"]),
            workers=1,
        )
    except RuntimeError as exc:
        raise ExpertTeamError(str(exc)) from exc

    solver = solved.solver
    selected = {key[0]: {**options[key]} for key, var in variables.items() if solver.Value(var)}
    rows = [selected[row["id"]] for row in packages if row["id"] in selected]
    rows.sort(key=lambda row: (
        0 if requirements["atomic_work_units"][0]["id"] in row["package"]["unit_ids"] else 1,
        0 if row["package"]["id"] == "red" else 1,
        row["package"]["id"],
    ))

    ratio = solved.ratio_scaled / RATIO_SCALE
    experts: list[SelectedExpert] = []
    plan_selected: dict[str, Any] = {}
    for index, row in enumerate(rows, 1):
        package = row["package"]
        seat_key = "red" if package["id"] == "red" else f"expert_{index:02d}"
        reason = (
            f"资源需求先行+CP-SAT直接性价比优化；原子工作={','.join(package['unit_ids'])}；"
            f"提示词模块={','.join(row['prompt']['modules'])}；参数={row['params']['id']}；"
            f"风险调整效用={row['parts']['weighted_utility']:.3f}；预计成本=${row['cost']:.6f}；"
            f"全局性价比={ratio:.6f}"
        )
        experts.append(SelectedExpert(
            seat_key,
            package["function"],
            package["profession"],
            package["domain"],
            package["mission"],
            row["model"].id,
            reason,
        ))
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
            f"动态综合资源；直接性价比优化；提示词={','.join(judge_row['prompt']['modules'])}；"
            f"参数={judge_row['params']['id']}；预计成本=${judge_row['cost']:.6f}；"
            f"全局性价比={ratio:.6f}"
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
    plan = {
        "version": 3,
        "optimizer": "google-or-tools-cp-sat",
        "selection_method": "resource-demand-first-direct-cost-performance",
        "architecture": {
            "stage_a": "task_to_atomic_work_prompt_capability_parameter_demands",
            "stage_b": "live_market_joint_cost_performance_optimization",
            "execution_graph": "parallel_dynamic_work_packages_then_dynamic_synthesis",
        },
        "solver_status": solved.status_name,
        "phase_status": {"maximum_cost_performance": solved.status_name},
        "objective_order": [
            "hard_resource_coverage",
            "maximum_cost_performance",
            "minimum_cost_and_calls_as_tiebreakers",
        ],
        "cost_performance": {
            "definition": "risk_adjusted_task_utility / effective_total_estimated_cost",
            "ratio": round(ratio, 9),
            "ratio_scaled": solved.ratio_scaled,
            "utility_numerator": solved.numerator_value,
            "effective_cost_denominator_units": solved.denominator_value,
            "actual_estimated_cost_usd": round(solved.actual_cost_value / COST_SCALE, 9),
            "zero_price_guard_usd_per_call": ZERO_PRICE_GUARD_UNITS_PER_CALL / COST_SCALE,
            "call_count": solved.call_count,
            "iterations": list(solved.iterations),
        },
        "quality_first_phase_used": False,
        "quality_tolerance_band_used": False,
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
            "intelligence_rank_hard_ceiling": base.scoring.MAX_INTELLIGENCE_RANK,
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
        json.dumps(requirements, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run.output_dir / "task-parameter-matrix.json").write_text(
        json.dumps(requirements, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (run.output_dir / "team-optimization.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    object.__setattr__(profile, "team_pattern", plan["team_pattern"])
    object.__setattr__(profile, "expert_count", len(experts))
    if len(experts) != 3:
        object.__setattr__(run, "require_all_experts", False)
    base.dynamic_runtime.activate_runtime(plan, run, profile, experts, judge)
    return experts, judge, estimated
