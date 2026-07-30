"""OR-Tools CP-SAT global optimizer for dynamic expert-team composition."""
from __future__ import annotations

import json
import math
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from ortools.sat.python import cp_model

import seat_scoring as base
from dynamic_runtime import activate_runtime
from model_market import (
    ExpertTeamError,
    ModelInfo,
    RunConfig,
    SeatSpec,
    SelectedExpert,
    SelectedJudge,
    TaskProfile,
    estimate_call_cost,
    load_json,
    POLICY_FILE,
)

CONFIG_RE = re.compile(
    r"<expert-team-config>\s*(\{.*?\})\s*</expert-team-config>",
    re.IGNORECASE | re.DOTALL,
)
DEFAULT_TEMPLATES = {
    "concise": {"effort": "low", "temperature": 0.05, "verbosity": "low", "expected_output_tokens": 900},
    "balanced": {"effort": "medium", "temperature": 0.10, "verbosity": "low", "expected_output_tokens": 1500},
    "deep": {"effort": "high", "temperature": 0.06, "verbosity": "low", "expected_output_tokens": 2400},
    "red_team": {"effort": "high", "temperature": 0.14, "verbosity": "medium", "expected_output_tokens": 2000},
    "judge": {"effort": "high", "temperature": 0.04, "verbosity": "low", "expected_output_tokens": 2600},
}
SEAT_TEMPLATE_ALLOWLIST = {
    "core": ("concise", "balanced", "deep"),
    "cross": ("concise", "balanced", "deep"),
    "red": ("balanced", "deep", "red_team"),
    "evidence": ("balanced", "deep"),
    "judge": ("balanced", "deep", "judge"),
}
DEFAULT_POLICY = {
    "topology": {"simple": 1, "medium": 2, "complex": 3, "high_stakes_min": 3, "complex_escalated": 4},
    "candidate_pool_per_seat": 12,
    "solver_timeout_seconds": 8.0,
    "strict_provider_diversity": True,
}
TIER_WEIGHTS = {
    "budget": {"benchmark": 15, "fit": 15, "reliability": 20, "value": 15, "cost": 35},
    "value": {"benchmark": 28, "fit": 22, "reliability": 20, "value": 20, "cost": 10},
    "quality": {"benchmark": 45, "fit": 25, "reliability": 20, "value": 5, "cost": 5},
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _optimizer_policy() -> Dict[str, Any]:
    path = Path(__file__).with_name("optimization_policy.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return json.loads(json.dumps(DEFAULT_POLICY))
    if not isinstance(data, dict):
        return json.loads(json.dumps(DEFAULT_POLICY))
    merged = json.loads(json.dumps(DEFAULT_POLICY))
    for key, value in data.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _json_overrides(task: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {}
    match = CONFIG_RE.search(task)
    if match:
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ExpertTeamError(f"Invalid <expert-team-config> JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ExpertTeamError("<expert-team-config> must contain one JSON object.")
        data.update(parsed)
    raw = os.getenv("EXPERT_TEAM_INPUT_JSON", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExpertTeamError(f"EXPERT_TEAM_INPUT_JSON is invalid: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ExpertTeamError("EXPERT_TEAM_INPUT_JSON must be a JSON object.")
        data.update(parsed)
    return data


def infer_task_input(profile: TaskProfile, run: RunConfig) -> Dict[str, Any]:
    """Convert task text/profile into validated optimizer inputs with optional overrides."""
    policy = _optimizer_policy()
    overrides = _json_overrides(run.task)
    topology = policy.get("topology", {})
    expert_count = int(topology.get(profile.complexity, 3))
    if profile.high_stakes:
        expert_count = max(expert_count, int(topology.get("high_stakes_min", 3)))
    if profile.complexity == "complex" and (
        profile.high_stakes or profile.long_context or len(profile.domains) >= 3
    ):
        expert_count = int(topology.get("complex_escalated", 4))
    requested = overrides.get("expert_count")
    if requested is not None:
        try:
            expert_count = int(requested)
        except (TypeError, ValueError) as exc:
            raise ExpertTeamError("expert_count must be an integer from 1 to 4.") from exc
    expert_count = max(1, min(4, expert_count))
    if profile.high_stakes and expert_count < 3:
        raise ExpertTeamError("High-stakes tasks require at least three experts including an independent red-team seat.")

    objective = str(overrides.get("objective") or run.quality_tier or "value")
    if objective not in TIER_WEIGHTS:
        raise ExpertTeamError("objective must be budget, value, or quality.")
    budget = overrides.get("budget_usd", run.max_estimated_cost_usd)
    budget_usd = None if budget in {None, ""} else _finite(budget, -1.0)
    if budget_usd is not None and budget_usd <= 0:
        raise ExpertTeamError("budget_usd must be greater than zero when supplied.")
    forbidden = sorted({str(item) for item in overrides.get("forbidden_models", []) if str(item).strip()})
    preferred = sorted({str(item) for item in overrides.get("preferred_models", []) if str(item).strip()})
    strict_diversity = bool(overrides.get("strict_provider_diversity", policy.get("strict_provider_diversity", True)))
    default_pool = max(int(policy.get("candidate_pool_per_seat", 12)), run.candidate_pool_per_seat)
    candidate_limit = int(overrides.get("candidate_pool_per_seat", default_pool))
    candidate_limit = max(4, min(25, candidate_limit))
    default_timeout = _finite(policy.get("solver_timeout_seconds", 8.0), 8.0)
    timeout = _finite(overrides.get("solver_timeout_seconds", default_timeout), default_timeout)
    timeout = max(1.0, min(30.0, timeout))
    return {
        "version": 1,
        "source": "task-profile+optional-embedded-json+environment",
        "objective": objective,
        "expert_count": expert_count,
        "budget_usd": budget_usd,
        "strict_provider_diversity": strict_diversity,
        "candidate_pool_per_seat": candidate_limit,
        "solver_timeout_seconds": timeout,
        "forbidden_models": forbidden,
        "preferred_models": preferred,
        "task_profile": asdict(profile),
        "policy_file": "optimization_policy.json",
        "accepted_override_fields": [
            "objective", "expert_count", "budget_usd", "strict_provider_diversity",
            "candidate_pool_per_seat", "solver_timeout_seconds", "forbidden_models", "preferred_models",
        ],
    }


def _evidence_seat(profile: TaskProfile) -> SeatSpec:
    policy = load_json(POLICY_FILE)
    professions = policy["professions"]
    domain = "math" if "math" in profile.domains else "research"
    profession = professions.get(domain, professions["general"])["cross"]
    return SeatSpec(
        "evidence",
        "证据与定量校准席",
        profession,
        domain,
        "独立检查数据、基准、计算、敏感性、可证伪条件和结果校准，不重复核心席观点。",
    )


def build_dynamic_seats(profile: TaskProfile, expert_count: int) -> tuple[list[SeatSpec], str]:
    base_seats, judge_profession = base.build_fixed_seats(profile)
    by_key = {seat.key: seat for seat in base_seats}
    ordered = [by_key["core"]]
    if expert_count >= 2:
        ordered.append(by_key["cross"])
    if expert_count >= 3:
        ordered.append(by_key["red"])
    if expert_count >= 4:
        ordered.append(_evidence_seat(profile))
    return ordered, judge_profession


def _templates(path: Path | None = None) -> Dict[str, Dict[str, Any]]:
    candidate = path or Path(__file__).with_name("parameter_templates.json")
    try:
        data = json.loads(candidate.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {key: dict(value) for key, value in DEFAULT_TEMPLATES.items()}
    raw = data.get("templates") if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {key: dict(value) for key, value in DEFAULT_TEMPLATES.items()}
    merged = {key: dict(value) for key, value in DEFAULT_TEMPLATES.items()}
    for key, value in raw.items():
        if key in merged and isinstance(value, dict):
            merged[key].update(value)
    return merged


def _template_fit(seat_key: str, template: str, profile: TaskProfile) -> float:
    preferred = {
        "core": "deep" if profile.complexity == "complex" else "balanced" if profile.complexity == "medium" else "concise",
        "cross": "deep" if profile.complexity == "complex" else "balanced",
        "red": "red_team",
        "evidence": "deep",
        "judge": "judge" if profile.complexity != "simple" or profile.high_stakes else "balanced",
    }[seat_key]
    if template == preferred:
        return 1.0
    if template in {"deep", "judge", "red_team"} and profile.complexity == "complex":
        return 0.82
    return 0.62


def _eligible_templates(model: ModelInfo, seat_key: str, templates: Mapping[str, Mapping[str, Any]]) -> list[str]:
    supported = set(model.supported_parameters)
    result = []
    for name in SEAT_TEMPLATE_ALLOWLIST[seat_key]:
        if name not in templates:
            continue
        effort = str(templates[name].get("effort") or "low")
        if effort in {"medium", "high"} and "reasoning" not in supported:
            continue
        result.append(name)
    return result or (["concise"] if "concise" in templates else list(templates)[:1])


def _expected_cost(
    model: ModelInfo,
    run: RunConfig,
    seat_key: str,
    template: Mapping[str, Any],
    expert_count: int,
) -> float:
    output_tokens = max(256, int(template.get("expected_output_tokens") or 1200))
    input_chars = len(run.task) + 1200
    if seat_key == "judge":
        input_chars = len(run.task) + expert_count * 1800 * 4 + 3000
    return estimate_call_cost(model, input_chars, output_tokens)


def _pool_for_seat(pool: Sequence[ModelInfo], seat: SeatSpec, limit: int, tier: str) -> list[ModelInfo]:
    rows = base._seat_pool(pool, seat.key, seat.domain_focus)
    ordered = base._ordered(rows, seat.key, seat.domain_focus, tier)
    return ordered[:limit]


def _judge_pool(pool: Sequence[ModelInfo], profile: TaskProfile, limit: int, tier: str) -> list[ModelInfo]:
    rows = [
        model for model in pool
        if profile.primary_domain == "coding" or not any(term in base._text(model) for term in base.CODE_SPECIALIST_TERMS)
    ] or list(pool)
    return base._ordered(rows, "judge", profile.primary_domain, tier)[:limit]


def _score_components(
    model: ModelInfo,
    domain: str,
    cost: float,
    max_cost: float,
    max_value: float,
    tier: str,
    template_fit: float,
    preferred: set[str],
    seat_key: str,
) -> tuple[int, Dict[str, float]]:
    benchmark = max(0.0, min(100.0, _finite(base._benchmark_score(model, domain))))
    fit = max(0.0, min(1.0, _finite(base._domain_fit(model, domain)))) * 100.0
    reliability = max(0.0, min(1.0, _finite(model.components.get("history"), 0.55))) * 100.0
    raw_value = max(0.0, _finite(base._value_index(model, domain)))
    value = 100.0 * raw_value / max(max_value, 1e-9)
    cost_score = 100.0 * (1.0 - cost / max(max_cost, 1e-9))
    weights = TIER_WEIGHTS[tier]
    utility = (
        benchmark * weights["benchmark"]
        + fit * weights["fit"]
        + reliability * weights["reliability"]
        + value * weights["value"]
        + cost_score * weights["cost"]
    ) / 100.0
    utility += template_fit * 8.0
    if model.id in preferred:
        utility += 5.0
    if seat_key == "red":
        utility += base._term_fit(model, base.RISK_TERMS) * 8.0
    if seat_key == "judge":
        supported = set(model.supported_parameters)
        if "reasoning" not in supported or bool(model.reasoning.get("supports_max_tokens")):
            utility += 4.0
    components = {
        "benchmark": round(benchmark, 6),
        "domain_fit": round(fit / 100.0, 6),
        "reliability": round(reliability / 100.0, 6),
        "normalized_value": round(value / 100.0, 6),
        "normalized_cost": round(cost_score / 100.0, 6),
        "template_fit": round(template_fit, 6),
        "utility": round(utility, 6),
    }
    return int(round(utility * 1000)), components


def select_team(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
) -> tuple[list[SelectedExpert], SelectedJudge, float]:
    """Solve the global model-seat-parameter assignment with CP-SAT."""
    task_input = infer_task_input(profile, run)
    tier = task_input["objective"]
    seats, judge_profession = build_dynamic_seats(profile, task_input["expert_count"])
    pool = base._stable_pool(ranked, profile)
    base._enrich_benchmarks(run, pool)
    forbidden = set(task_input["forbidden_models"])
    pool = [model for model in pool if model.id not in forbidden]
    if len({model.author for model in pool}) < len(seats) + 1 and task_input["strict_provider_diversity"]:
        raise ExpertTeamError(
            f"Dynamic {len(seats)}+1 requires at least {len(seats)+1} distinct eligible providers."
        )

    candidates: Dict[str, list[ModelInfo]] = {}
    for seat in seats:
        candidates[seat.key] = _pool_for_seat(
            pool, seat, task_input["candidate_pool_per_seat"], tier
        )
    judge_seat = SeatSpec("judge", "综合裁决席", judge_profession, profile.primary_domain, "综合全部专家证据并形成最终裁决。")
    candidates["judge"] = _judge_pool(pool, profile, task_input["candidate_pool_per_seat"], tier)
    if any(not rows for rows in candidates.values()):
        missing = [key for key, rows in candidates.items() if not rows]
        raise ExpertTeamError(f"No eligible models for optimizer seats: {missing}")

    templates = _templates()
    all_options: Dict[tuple[str, str, str], Dict[str, Any]] = {}
    all_costs: list[float] = []
    all_values: list[float] = []
    seat_map = {seat.key: seat for seat in seats}
    seat_map["judge"] = judge_seat
    for seat_key, models in candidates.items():
        domain = seat_map[seat_key].domain_focus
        for model in models:
            all_values.append(max(0.0, _finite(base._value_index(model, domain))))
            for template_name in _eligible_templates(model, seat_key, templates):
                cost = _expected_cost(model, run, seat_key, templates[template_name], len(seats))
                all_costs.append(cost)
                all_options[(seat_key, model.id, template_name)] = {
                    "model": model,
                    "template": template_name,
                    "cost": cost,
                }
    max_cost = max(all_costs or [1.0])
    max_value = max(all_values or [1.0])
    preferred = set(task_input["preferred_models"])
    for (seat_key, _model_id, template_name), row in all_options.items():
        seat = seat_map[seat_key]
        score, components = _score_components(
            row["model"], seat.domain_focus, row["cost"], max_cost, max_value, tier,
            _template_fit(seat_key, template_name, profile), preferred, seat_key,
        )
        row["score"] = score
        row["components"] = components

    model = cp_model.CpModel()
    variables: Dict[tuple[str, str, str], Any] = {
        key: model.NewBoolVar("assign__" + "__".join(part.replace("/", "_") for part in key))
        for key in all_options
    }
    for seat_key in candidates:
        model.Add(sum(var for key, var in variables.items() if key[0] == seat_key) == 1)
    for model_id in {key[1] for key in variables}:
        model.Add(sum(var for key, var in variables.items() if key[1] == model_id) <= 1)
    if task_input["strict_provider_diversity"]:
        by_author: Dict[str, list[Any]] = {}
        for key, var in variables.items():
            author = all_options[key]["model"].author
            by_author.setdefault(author, []).append(var)
        for rows in by_author.values():
            model.Add(sum(rows) <= 1)
    if task_input["budget_usd"] is not None:
        cost_scale = 1_000_000
        model.Add(
            sum(int(round(all_options[key]["cost"] * cost_scale)) * var for key, var in variables.items())
            <= int(round(task_input["budget_usd"] * cost_scale))
        )
    model.Maximize(sum(all_options[key]["score"] * var for key, var in variables.items()))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = task_input["solver_timeout_seconds"]
    solver.parameters.num_search_workers = 1
    solver.parameters.random_seed = 0
    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise ExpertTeamError(f"OR-Tools CP-SAT found no usable expert-team solution: {status_name}")

    selected_rows: Dict[str, Dict[str, Any]] = {}
    for key, var in variables.items():
        if solver.Value(var):
            selected_rows[key[0]] = {**all_options[key], "seat_key": key[0]}
    experts: list[SelectedExpert] = []
    for seat in seats:
        row = selected_rows[seat.key]
        chosen = row["model"]
        reason = (
            f"OR-Tools CP-SAT全局组合优化；目标={tier}；参数模板={row['template']}；"
            f"任务领域={seat.domain_focus}；组合效用={row['components']['utility']:.3f}；"
            f"预计调用成本=${row['cost']:.6f}；模型与Provider全局去重"
        )
        experts.append(SelectedExpert(
            seat.key, seat.function, seat.profession, seat.domain_focus, seat.mission, chosen.id, reason
        ))
    judge_row = selected_rows["judge"]
    judge_model = judge_row["model"]
    judge = SelectedJudge(
        "综合裁决席",
        judge_profession,
        judge_model.id,
        (
            f"OR-Tools CP-SAT全局组合优化；目标={tier}；参数模板={judge_row['template']}；"
            f"组合效用={judge_row['components']['utility']:.3f}；预计调用成本=${judge_row['cost']:.6f}；"
            "与全部专家模型及Provider独立"
        ),
    )
    estimated = sum(row["cost"] for row in selected_rows.values())
    plan = {
        "version": 1,
        "optimizer": "google-or-tools-cp-sat",
        "solver_status": status_name,
        "objective_value": solver.ObjectiveValue(),
        "best_objective_bound": solver.BestObjectiveBound(),
        "task_input": task_input,
        "team_pattern": f"{len(experts)}-experts-plus-one-judge",
        "expert_count": len(experts),
        "estimated_cost_usd": round(estimated, 9),
        "strict_provider_diversity": task_input["strict_provider_diversity"],
        "selected": {
            key: {
                "seat": key,
                "function": seat_map[key].function,
                "profession": seat_map[key].profession,
                "domain": seat_map[key].domain_focus,
                "model": row["model"].id,
                "provider": row["model"].author,
                "parameter_template": row["template"],
                "parameters": templates[row["template"]],
                "estimated_cost_usd": round(row["cost"], 9),
                "score_components": row["components"],
            }
            for key, row in selected_rows.items()
        },
        "constraints": {
            "one_model_per_seat": True,
            "model_reuse_forbidden": True,
            "provider_reuse_forbidden": task_input["strict_provider_diversity"],
            "intelligence_top_50_and_stability_gates": True,
            "budget_usd": task_input["budget_usd"],
            "external_tools_allowed": False,
        },
        "candidate_counts": {key: len(value) for key, value in candidates.items()},
        "fallback_used": False,
    }
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "team-optimization.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    object.__setattr__(profile, "team_pattern", plan["team_pattern"])
    object.__setattr__(profile, "expert_count", len(experts))
    if len(experts) != 3:
        object.__setattr__(run, "require_all_experts", False)
    activate_runtime(plan, run, profile, experts, judge)
    return experts, judge, estimated
