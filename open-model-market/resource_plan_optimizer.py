"""Jointly optimize work-package grouping, prompt modules, model, provider, and parameters."""
from __future__ import annotations

import itertools
import json
import math
from typing import Any, Mapping, Sequence

from ortools.sat.python import cp_model

import dynamic_runtime
import resource_requirements as rr
import seat_scoring as scoring
import task_matrix_optimizer as legacy
from model_market import (
    ExpertTeamError, ModelInfo, RunConfig, SelectedExpert,
    SelectedJudge, TaskProfile, estimate_call_cost, load_json, POLICY_FILE,
)


def _affinity(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    if a["independence_group"] or b["independence_group"]:
        return -1.0
    score = 0.55 if a["domain"] == b["domain"] else 0.0
    score += 0.20 if a["operation"] == b["operation"] else 0.0
    if {a["operation"], b["operation"]} <= {"analysis", "decision", "evidence"}:
        score += 0.30
    if {a["operation"], b["operation"]} <= {"quantitative", "forecast", "evidence"}:
        score += 0.25
    if "implementation" in {a["operation"], b["operation"]} and a["domain"] != b["domain"]:
        score -= 0.35
    if "creative" in {a["operation"], b["operation"]} and a["operation"] != b["operation"]:
        score -= 0.45
    return score


def _profession(domain: str, role: str) -> str:
    policy = load_json(POLICY_FILE)
    rows = policy["professions"].get(domain, policy["professions"]["general"])
    return str(rows.get(role) or rows.get("core") or "综合专家")


def _package(units: Sequence[Mapping[str, Any]], primary: str) -> dict[str, Any]:
    ordered = sorted(units, key=lambda x: (-x["importance"], x["id"]))
    operations = sorted({str(x["operation"]) for x in ordered})
    domains = sorted({str(x["domain"]) for x in ordered})
    independent = next((x["independence_group"] for x in ordered if x["independence_group"]), None)
    domain = primary if primary in domains else str(ordered[0]["domain"])
    package_id = "red" if independent else rr.digest("pkg", [x["id"] for x in ordered])
    labels = "、".join(rr.OP_LABELS.get(x, x) for x in operations)
    role = "red" if independent else "core" if any(
        x["operation"] == "analysis" and x["domain"] == primary for x in ordered
    ) else "cross"
    return {
        "id": package_id,
        "function": f"{legacy.DOMAIN_LABEL.get(domain, domain)}·{labels}工作包",
        "profession": _profession(domain, role),
        "domain": domain,
        "unit_ids": [x["id"] for x in ordered],
        "operations": operations,
        "required_prompt_modules": sorted({m for x in ordered for m in x["required_prompt_modules"]}),
        "minimum_reasoning_level": max(int(x["minimum_reasoning_level"]) for x in ordered),
        "structured_output_required": any(bool(x["structured_output_required"]) for x in ordered),
        "minimum_context_tokens": max(int(x["minimum_context_tokens"]) for x in ordered),
        "expected_output_tokens": min(5000, sum(int(x["expected_output_tokens"]) for x in ordered)),
        "independence_group": independent,
        "mission": "；".join(
            f"{rr.OP_LABELS.get(x['operation'], x['operation'])}@{legacy.DOMAIN_LABEL.get(x['domain'], x['domain'])}"
            for x in ordered
        ),
        "importance_mass": round(sum(float(x["importance"]) for x in ordered), 6),
    }


def generate_packages(requirements: Mapping[str, Any]) -> list[dict[str, Any]]:
    units = list(requirements["atomic_work_units"])
    primary = str(requirements["task_signals"]["primary_domain"])
    groups: dict[tuple[str, ...], Sequence[Mapping[str, Any]]] = {}

    def add(rows: Sequence[Mapping[str, Any]]) -> None:
        if rows and len(rows) <= 4:
            groups.setdefault(tuple(sorted(str(x["id"]) for x in rows)), rows)

    for unit in units:
        add([unit])
    for domain in sorted({str(x["domain"]) for x in units}):
        add([x for x in units if x["domain"] == domain and not x["independence_group"]])
    for operation in sorted({str(x["operation"]) for x in units}):
        add([x for x in units if x["operation"] == operation and not x["independence_group"]])
    for size in (2, 3):
        for rows in itertools.combinations(units, size):
            if all(_affinity(a, b) >= 0.25 for a, b in itertools.combinations(rows, 2)):
                add(rows)
    plain = [x for x in units if not x["independence_group"]]
    if len(plain) <= 4:
        add(plain)
    result = [_package(rows, primary) for rows in groups.values()]
    result.sort(key=lambda x: (-x["importance_mass"], len(x["unit_ids"]), x["id"]))
    return result


def _synthesis(requirements: Mapping[str, Any]) -> dict[str, Any]:
    raw = requirements["synthesis_requirements"]
    domain = str(requirements["task_signals"]["primary_domain"])
    return {
        "id": "judge", "function": "动态综合与裁决节点",
        "profession": _profession(domain, "judge"), "domain": domain,
        "unit_ids": [], "operations": ["analysis", "decision"],
        "required_prompt_modules": raw["required_prompt_modules"],
        "minimum_reasoning_level": raw["minimum_reasoning_level"],
        "structured_output_required": raw["structured_output_required"],
        "minimum_context_tokens": raw["minimum_context_tokens"],
        "expected_output_tokens": raw["expected_output_tokens"],
        "independence_group": "synthesis",
        "mission": "综合所有工作包结果，审查覆盖、证据、分歧和最终决策。",
        "importance_mass": max(1.0, len(requirements["atomic_work_units"]) * 0.45),
    }


def _supports(model: ModelInfo, package: Mapping[str, Any]) -> bool:
    supported = set(model.supported_parameters)
    if model.context_length < int(package["minimum_context_tokens"]) or model.max_completion_tokens <= 0:
        return False
    if package["structured_output_required"] and not {"structured_outputs", "response_format"} & supported:
        return False
    if int(package["minimum_reasoning_level"]) >= 2 and "reasoning" not in supported:
        return False
    return True


def _prompt_profiles(package: Mapping[str, Any], high_stakes: bool) -> list[dict[str, Any]]:
    required = set(package["required_prompt_modules"]) | {"scope", "delivery"}
    variants = [required]
    enhanced = set(required)
    if high_stakes:
        enhanced.update({"evidence", "uncertainty", "independence"})
    elif len(package["unit_ids"]) >= 2:
        enhanced.add("uncertainty")
    if enhanced != required:
        variants.append(enhanced)
    result = []
    for modules in variants:
        ordered = sorted(modules)
        result.append({
            "id": rr.digest("prompt", ordered),
            "modules": ordered,
            "instructions": [rr.PROMPT_MODULES[x][0] for x in ordered],
            "token_overhead": sum(rr.PROMPT_MODULES[x][1] for x in ordered),
            "quality_gain": sum(rr.PROMPT_MODULES[x][2] for x in ordered),
        })
    return result


def _parameter_profiles(model: ModelInfo, package: Mapping[str, Any], requirements: Mapping[str, Any]) -> list[dict[str, Any]]:
    supported = set(model.supported_parameters)
    minimum = int(package["minimum_reasoning_level"])
    levels = [0] if "reasoning" not in supported else list(range(minimum, 3))
    creativity = max(
        [float(requirements["operation_scores"].get(x, 0)) for x in package["operations"] if x == "creative"] or [0.0]
    )
    precision = max(
        [float(requirements["operation_scores"].get(x, 0)) for x in package["operations"] if x in {"evidence", "quantitative", "decision"}] or [0.0]
    )
    base_temp = legacy._clamp(0.04 + 0.22 * creativity - 0.03 * precision)
    temperatures = sorted({round(legacy._clamp(base_temp + d), 2) for d in (0.0, 0.04)})
    expected = min(int(package["expected_output_tokens"]), int(model.max_completion_tokens))
    token_values = sorted({
        max(512, min(int(model.max_completion_tokens), int(expected * f))) for f in (0.85, 1.0, 1.15)
    })
    detail = len(package["unit_ids"]) + int(requirements["task_signals"]["complexity"] == "complex")
    verbosity = ["high"] if detail >= 5 else ["medium", "high"] if detail >= 3 else ["low", "medium"]
    if "verbosity" not in supported:
        verbosity = ["unsupported"]
    result = []
    for level, temp, tokens, verbose in itertools.product(levels, temperatures, token_values, verbosity):
        parameters = {
            "effort": {0: "low", 1: "medium", 2: "high"}[level],
            "temperature": temp, "verbosity": verbose,
            "structured_output": bool(
                package["structured_output_required"] and {"structured_outputs", "response_format"} & supported
            ),
            "expected_output_tokens": tokens,
        }
        result.append({"id": rr.digest("params", parameters), "parameters": parameters})
    return result


def _operation_fit(model: ModelInfo, package: Mapping[str, Any]) -> float:
    text = f"{model.id} {model.name} {model.description}".casefold()
    values = []
    for operation in package["operations"]:
        terms = legacy.OP_TERMS.get(operation, ())
        values.append(legacy._clamp(sum(t.casefold() in text for t in terms) / max(1.0, min(3.0, len(terms)))))
    return sum(values) / max(1, len(values))


def _quality(model: ModelInfo, package: Mapping[str, Any], params: Mapping[str, Any], prompt: Mapping[str, Any], preferred: set[str]) -> tuple[int, dict[str, float]]:
    benchmark = legacy._clamp(scoring._benchmark_score(model, str(package["domain"])) / 100.0)
    domain_fit = legacy._clamp(scoring._domain_fit(model, str(package["domain"])))
    operation_fit = _operation_fit(model, package)
    stability = legacy._live_stability(model)
    context_fit = legacy._clamp(model.context_length / max(1.0, float(package["minimum_context_tokens"]) * 2))
    effort = {"low": 0.55, "medium": 0.80, "high": 1.0}.get(str(params["parameters"]["effort"]), 0.55)
    prompt_fit = legacy._clamp(float(prompt["quality_gain"]) / 500.0)
    utility = 0.34*benchmark + 0.21*domain_fit + 0.14*operation_fit + 0.12*stability + 0.07*context_fit + 0.07*effort + 0.05*prompt_fit
    if model.id in preferred:
        utility += 0.02
    weighted = utility * max(0.35, float(package["importance_mass"]))
    parts = {
        "benchmark": benchmark, "domain_fit": domain_fit, "operation_fit": operation_fit,
        "live_stability": stability, "context_fit": context_fit,
        "reasoning_fit": effort, "prompt_fit": prompt_fit,
        "importance_mass": float(package["importance_mass"]), "weighted_utility": weighted,
    }
    return int(round(weighted * 1_000_000)), {k: round(v, 6) for k, v in parts.items()}


def _solver(seconds: float) -> cp_model.CpSolver:
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    return solver


def select_team(ranked: Sequence[ModelInfo], profile: TaskProfile, run: RunConfig) -> tuple[list[SelectedExpert], SelectedJudge, float]:
    legacy._disable_history()
    requirements = rr.compile_requirements(profile, run)
    constraints = requirements["constraints"]
    packages = generate_packages(requirements)
    synthesis = _synthesis(requirements)
    pool = [x for x in legacy._eligible_pool(ranked, profile) if x.id not in set(constraints["forbidden_models"])]
    scoring._enrich_benchmarks(run, pool)
    limit = int(constraints["candidate_pool_per_work_package"])
    candidates: dict[str, list[ModelInfo]] = {}
    for package in packages + [synthesis]:
        rows = [model for model in pool if _supports(model, package)]
        if package["id"] == "red":
            maximum = max((scoring._term_fit(x, scoring.RISK_TERMS) for x in rows), default=0.0)
            if maximum > 0:
                rows = [x for x in rows if scoring._term_fit(x, scoring.RISK_TERMS) == maximum]
        rows.sort(key=lambda x: (
            -scoring._benchmark_score(x, str(package["domain"])),
            -scoring._domain_fit(x, str(package["domain"])),
            -_operation_fit(x, package), -legacy._live_stability(x),
            x.blended_price_per_million if x.blended_price_per_million is not None else math.inf, x.id,
        ))
        candidates[package["id"]] = rows[:limit]
    packages = [x for x in packages if candidates.get(x["id"])]
    if not candidates.get("judge"):
        raise ExpertTeamError("No eligible synthesis model satisfies resource requirements.")

    preferred = set(constraints["preferred_models"])
    options: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for package in packages + [synthesis]:
        for model in candidates[package["id"]]:
            for params in _parameter_profiles(model, package, requirements):
                for prompt in _prompt_profiles(package, bool(requirements["task_signals"]["high_stakes"])):
                    chars = len(run.task) + int(prompt["token_overhead"] * 4)
                    if package["id"] == "judge":
                        chars += len(packages) * 5000 + 3000
                    cost = estimate_call_cost(model, chars, int(params["parameters"]["expected_output_tokens"]))
                    score, parts = _quality(model, package, params, prompt, preferred)
                    key = (package["id"], model.id, params["id"], prompt["id"])
                    options[key] = {"package": package, "model": model, "params": params, "prompt": prompt, "cost": cost, "score": score, "parts": parts}

    cp = cp_model.CpModel()
    variables = {key: cp.NewBoolVar("x__" + "__".join(v.replace("/", "_") for v in key)) for key in options}
    active = {x["id"]: cp.NewBoolVar(f"active__{x['id']}") for x in packages}
    for package_id in active:
        cp.Add(sum(v for key, v in variables.items() if key[0] == package_id) == active[package_id])
    cp.Add(sum(v for key, v in variables.items() if key[0] == "judge") == 1)
    for unit_id, copies in requirements["coverage_requirements"].items():
        covering = [active[x["id"]] for x in packages if unit_id in x["unit_ids"]]
        if not covering:
            raise ExpertTeamError(f"No work package covers atomic unit {unit_id}.")
        cp.Add(sum(covering) == int(copies))
    cp.Add(sum(active.values()) >= int(constraints["min_experts"]))
    dynamic_max = sum(int(v) for v in requirements["coverage_requirements"].values())
    if constraints["max_experts"] is not None:
        dynamic_max = min(dynamic_max, int(constraints["max_experts"]))
    cp.Add(sum(active.values()) <= dynamic_max)
    for model_id in {key[1] for key in variables}:
        cp.Add(sum(v for key, v in variables.items() if key[1] == model_id) <= 1)
    if constraints["strict_provider_diversity"]:
        providers: dict[str, list[Any]] = {}
        for key, var in variables.items():
            providers.setdefault(options[key]["model"].author, []).append(var)
        for rows in providers.values():
            cp.Add(sum(rows) <= 1)

    quality = sum(options[key]["score"] * var for key, var in variables.items())
    cost = sum(int(round(options[key]["cost"] * 1_000_000_000)) * var for key, var in variables.items())
    if constraints["budget_usd"] is not None:
        cp.Add(cost <= int(round(float(constraints["budget_usd"]) * 1_000_000_000)))
    timeout = float(constraints["solver_timeout_seconds"])
    cp.Maximize(quality)
    s1 = _solver(timeout); st1 = s1.Solve(cp)
    if st1 not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise ExpertTeamError(f"No feasible full-dynamic resource plan: {s1.StatusName(st1)}")
    best = int(round(s1.ObjectiveValue()))
    floor = int(math.floor(best * (1 - float(constraints["quality_tolerance_pct"]) / 100)))
    cp.Add(quality >= floor)
    cp.Minimize(cost + (sum(active.values()) + 1) * 1000)
    solver = _solver(timeout); status = solver.Solve(cp)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise ExpertTeamError(f"Cost-performance optimization failed: {solver.StatusName(status)}")
    selected = {key[0]: {**options[key]} for key, var in variables.items() if solver.Value(var)}
    rows = [selected[x["id"]] for x in packages if x["id"] in selected]
    rows.sort(key=lambda x: (0 if requirements["atomic_work_units"][0]["id"] in x["package"]["unit_ids"] else 1, 0 if x["package"]["id"] == "red" else 1, x["package"]["id"]))

    experts = []
    plan_selected: dict[str, Any] = {}
    for index, row in enumerate(rows, 1):
        package = row["package"]
        seat_key = "red" if package["id"] == "red" else f"expert_{index:02d}"
        reason = (
            f"资源需求先行+CP-SAT；原子工作={','.join(package['unit_ids'])}；"
            f"提示词模块={','.join(row['prompt']['modules'])}；参数={row['params']['id']}；"
            f"质量效用={row['parts']['weighted_utility']:.3f}；预计成本=${row['cost']:.6f}"
        )
        experts.append(SelectedExpert(
            seat_key, package["function"], package["profession"], package["domain"],
            package["mission"], row["model"].id, reason,
        ))
        plan_selected[seat_key] = {
            "seat": seat_key, "work_package_id": package["id"],
            "function": package["function"], "profession": package["profession"],
            "domain": package["domain"], "atomic_work_units": package["unit_ids"],
            "operations": package["operations"], "model": row["model"].id,
            "provider": row["model"].author, "parameter_template": row["params"]["id"],
            "resource_profile_id": row["params"]["id"], "parameters": row["params"]["parameters"],
            "prompt_profile_id": row["prompt"]["id"], "prompt_modules": row["prompt"]["modules"],
            "prompt_instructions": row["prompt"]["instructions"],
            "prompt_token_overhead": row["prompt"]["token_overhead"],
            "estimated_cost_usd": round(row["cost"], 9), "score_components": row["parts"],
        }
    judge_row = selected["judge"]
    judge = SelectedJudge(
        synthesis["function"], synthesis["profession"], judge_row["model"].id,
        f"动态综合资源；提示词={','.join(judge_row['prompt']['modules'])}；参数={judge_row['params']['id']}；预计成本=${judge_row['cost']:.6f}",
    )
    plan_selected["judge"] = {
        "seat": "judge", "work_package_id": "judge", "function": synthesis["function"],
        "profession": synthesis["profession"], "domain": synthesis["domain"],
        "atomic_work_units": [], "operations": synthesis["operations"],
        "model": judge_row["model"].id, "provider": judge_row["model"].author,
        "parameter_template": judge_row["params"]["id"], "resource_profile_id": judge_row["params"]["id"],
        "parameters": judge_row["params"]["parameters"], "prompt_profile_id": judge_row["prompt"]["id"],
        "prompt_modules": judge_row["prompt"]["modules"], "prompt_instructions": judge_row["prompt"]["instructions"],
        "prompt_token_overhead": judge_row["prompt"]["token_overhead"],
        "estimated_cost_usd": round(judge_row["cost"], 9), "score_components": judge_row["parts"],
    }
    estimated = sum(float(x["cost"]) for x in selected.values())
    plan = {
        "version": 3, "optimizer": "google-or-tools-cp-sat",
        "selection_method": "resource-demand-first-quality-band-pareto-v3",
        "architecture": {
            "stage_a": "task_to_atomic_work_prompt_capability_parameter_demands",
            "stage_b": "live_market_joint_optimization",
            "execution_graph": "parallel_dynamic_work_packages_then_dynamic_synthesis",
        },
        "solver_status": solver.StatusName(status),
        "phase_status": {"maximum_quality": s1.StatusName(st1), "minimum_cost_in_quality_band": solver.StatusName(status)},
        "objective_order": ["hard_resource_coverage", "maximum_task_quality", "minimum_cost_and_calls_inside_quality_band"],
        "best_quality_score": best, "quality_floor": floor,
        "quality_tolerance_pct": constraints["quality_tolerance_pct"],
        "resource_requirements": requirements, "candidate_work_packages": packages,
        "team_pattern": f"{len(experts)}-dynamic-work-packages-plus-synthesis",
        "expert_count": len(experts), "estimated_cost_usd": round(estimated, 9),
        "selected": plan_selected,
        "constraints": {
            "atomic_work_coverage": requirements["coverage_requirements"],
            "model_reuse_forbidden": True,
            "provider_reuse_forbidden": constraints["strict_provider_diversity"],
            "intelligence_rank_hard_ceiling": scoring.MAX_INTELLIGENCE_RANK,
            "live_stable_direct_models_only": True, "budget_usd": constraints["budget_usd"],
            "history_input_used": False, "fixed_team_mode_used": False,
            "fixed_seat_template_used": False, "fixed_prompt_template_used": False,
            "fixed_parameter_template_used": False, "external_tools_allowed": False,
        },
        "requested_market_attributes": requirements["requested_market_attributes"],
        "candidate_counts": {key: len(value) for key, value in candidates.items()},
        "fallback_used": False,
    }
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "task-resource-requirements.json").write_text(json.dumps(requirements, ensure_ascii=False, indent=2), encoding="utf-8")
    (run.output_dir / "task-parameter-matrix.json").write_text(json.dumps(requirements, ensure_ascii=False, indent=2), encoding="utf-8")
    (run.output_dir / "team-optimization.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    object.__setattr__(profile, "team_pattern", plan["team_pattern"])
    object.__setattr__(profile, "expert_count", len(experts))
    if len(experts) != 3:
        object.__setattr__(run, "require_all_experts", False)
    dynamic_runtime.activate_runtime(plan, run, profile, experts, judge)
    return experts, judge, estimated
