"""History-free OpenRouter task-matrix selector using Google OR-Tools CP-SAT."""
from __future__ import annotations

import json
import math
import os
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from ortools.sat.python import cp_model

import dynamic_runtime
import model_market as market
import seat_scoring as scoring
from model_market import (
    ExpertTeamError, ModelInfo, RunConfig, SeatSpec, SelectedExpert,
    SelectedJudge, TaskProfile, estimate_call_cost, load_json, POLICY_FILE,
)

INPUT_RE = re.compile(r"<expert-team-input>\s*(\{.*?\})\s*</expert-team-input>", re.I | re.S)
UNSTABLE = ("preview", "experimental", "alpha", "beta", "spark", ":free", ":batch", ":online")
DOMAIN_TERMS = {
    "international_relations": ("外交", "战争", "制裁", "地缘", "geopolit", "diplomacy", "sanction", "war"),
    "legal": ("法律", "法规", "合规", "监管", "合同", "legal", "law", "compliance", "regulation"),
    "medical": ("医疗", "临床", "健康", "药品", "medical", "clinical", "health", "drug"),
    "security": ("安全", "网络安全", "攻击", "漏洞", "威胁", "security", "cyber", "attack", "threat"),
    "supply_chain": ("供应链", "物流", "采购", "库存", "运营", "supply chain", "logistics", "procurement"),
    "public_policy": ("公共政策", "政府", "财政", "治理", "政策", "public policy", "government", "governance"),
    "coding": ("代码", "软件", "编程", "接口", "仓库", "code", "software", "programming", "api", "repository"),
    "math": ("数学", "计算", "模型", "仿真", "统计", "优化", "math", "simulation", "statistics", "optimization"),
    "research": ("研究", "证据", "文献", "数据", "核验", "research", "evidence", "literature", "data"),
    "business": ("商业", "金融", "投资", "市场", "财务", "business", "finance", "investment", "market"),
    "creative": ("创意", "写作", "文案", "设计", "creative", "writing", "copy", "design"),
}
OP_TERMS = {
    "analysis": ("分析", "评估", "判断", "解释", "compare", "analyze", "evaluate", "explain"),
    "decision": ("决策", "选择", "方案", "建议", "最优", "策略", "recommend", "decision", "strategy"),
    "evidence": ("证据", "数据", "来源", "核验", "文献", "验证", "evidence", "data", "source", "verify"),
    "quantitative": ("计算", "建模", "仿真", "概率", "统计", "矩阵", "优化", "calculate", "model", "simulate", "statistic"),
    "forecast": ("预测", "推演", "情景", "趋势", "未来", "forecast", "scenario", "projection", "future"),
    "adversarial": ("红队", "反证", "漏洞", "失败", "风险", "否决", "red team", "adversarial", "failure", "risk"),
    "implementation": ("代码", "仓库", "部署", "接口", "软件", "实现", "code", "repository", "deploy", "implementation"),
    "creative": ("创意", "文案", "写作", "故事", "设计", "creative", "copywriting", "story", "design"),
}
DOMAIN_LABEL = {
    "international_relations": "国际关系", "legal": "法律合规", "medical": "医疗健康",
    "security": "安全风险", "supply_chain": "供应链运营", "public_policy": "公共政策",
    "coding": "软件工程", "math": "定量建模", "research": "证据研究",
    "business": "商业金融", "creative": "创意表达", "general": "综合分析",
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _hits(text: str, terms: Iterable[str]) -> int:
    folded = text.casefold()
    return sum(term.casefold() in folded for term in terms)


def _live_stability(model: ModelInfo) -> float:
    text = f"{model.id} {model.name} {model.description}".casefold()
    if any(term in text for term in UNSTABLE):
        return 0.0
    supported = set(model.supported_parameters)
    score = 0.62 + 0.08 * (model.expiration_date in {None, ""})
    score += 0.06 * (model.context_length >= 65536) + 0.06 * (model.max_completion_tokens >= 4096)
    score += 0.05 * ("reasoning" in supported)
    score += 0.05 * bool({"structured_outputs", "response_format"} & supported)
    score += 0.04 * (model.ranks.get("intelligence-high-to-low") is not None)
    return _clamp(score)


def rank_models_live_only(models: Mapping[str, ModelInfo], profile: TaskProfile, run: RunConfig) -> list[ModelInfo]:
    """Filter and pre-rank only with the current OpenRouter catalog; never history."""
    population = max(len(models), 2)
    ranked: list[ModelInfo] = []
    for model in models.values():
        text = f"{model.id} {model.name} {model.description}".casefold()
        if any(term in text for term in UNSTABLE) or market._expired(model.expiration_date):
            continue
        if model.context_length < profile.requested_context or model.max_completion_tokens <= 0:
            continue
        if model.input_modalities and "text" not in model.input_modalities:
            continue
        if model.output_modalities and "text" not in model.output_modalities:
            continue
        if model.prompt_price_per_million is None or model.completion_price_per_million is None:
            continue
        intelligence = market._rank_component(model.ranks.get("intelligence-high-to-low"), population)
        fit, reasons = market._task_fit(model, profile)
        price = model.blended_price_per_million or max(run.soft_price_cap, 0.01) * 10
        cost = 1.0 / (1.0 + price / max(run.soft_price_cap, 0.01))
        context = min(1.0, math.log2(max(model.context_length, 2)) / math.log2(max(profile.requested_context * 4, 4)))
        stability = _live_stability(model)
        model.components = {"quality": intelligence, "fit": fit, "cost": cost, "context": context, "live_stability": stability}
        model.score = 0.44 * intelligence + 0.26 * fit + 0.17 * cost + 0.08 * context + 0.05 * stability
        model.fit_reasons = reasons + ["未使用历史运行账本"]
        ranked.append(model)
    ranked.sort(key=lambda row: (-row.score, row.blended_price_per_million or math.inf, row.id))
    if len(ranked) < 2:
        raise ExpertTeamError("At least two eligible direct models are required: one expert and one judge.")
    return ranked


market.rank_models = rank_models_live_only


def _input(task: str, run: RunConfig) -> dict[str, Any]:
    raw: dict[str, Any] = {}
    match = INPUT_RE.search(task)
    if match:
        try:
            parsed = json.loads(match.group(1))
        except json.JSONDecodeError as exc:
            raise ExpertTeamError(f"Invalid <expert-team-input> JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ExpertTeamError("<expert-team-input> must contain one JSON object.")
        raw.update(parsed)
    env = os.getenv("EXPERT_TEAM_INPUT_JSON", "").strip()
    if env:
        try:
            parsed = json.loads(env)
        except json.JSONDecodeError as exc:
            raise ExpertTeamError(f"EXPERT_TEAM_INPUT_JSON is invalid: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ExpertTeamError("EXPERT_TEAM_INPUT_JSON must be a JSON object.")
        raw.update(parsed)
    accepted = {
        "budget_usd", "min_experts", "max_experts", "strict_provider_diversity",
        "candidate_pool_per_seat", "solver_timeout_seconds", "quality_tolerance_pct",
        "forbidden_models", "preferred_models",
    }
    unknown = sorted(set(raw) - accepted)
    if unknown:
        raise ExpertTeamError(f"Unsupported expert-team input fields: {unknown}")
    budget_raw = raw.get("budget_usd", run.max_estimated_cost_usd)
    budget = None if budget_raw in {None, ""} else _finite(budget_raw, -1)
    if budget is not None and budget <= 0:
        raise ExpertTeamError("budget_usd must be greater than zero.")
    minimum = int(raw.get("min_experts", 1)); maximum = int(raw.get("max_experts", 4))
    if not 1 <= minimum <= maximum <= 4:
        raise ExpertTeamError("Require 1 <= min_experts <= max_experts <= 4.")
    limit = max(4, min(30, int(raw.get("candidate_pool_per_seat", max(16, run.candidate_pool_per_seat)))))
    timeout = max(1.0, min(30.0, _finite(raw.get("solver_timeout_seconds", 10), 10)))
    tolerance = max(0.0, min(20.0, _finite(raw.get("quality_tolerance_pct", 2), 2)))
    return {
        "budget_usd": budget, "min_experts": minimum, "max_experts": maximum,
        "strict_provider_diversity": bool(raw.get("strict_provider_diversity", True)),
        "candidate_pool_per_seat": limit, "solver_timeout_seconds": timeout,
        "quality_tolerance_pct": tolerance,
        "forbidden_models": sorted({str(x) for x in raw.get("forbidden_models", []) if str(x)}),
        "preferred_models": sorted({str(x) for x in raw.get("preferred_models", []) if str(x)}),
    }


def build_task_matrix(profile: TaskProfile, run: RunConfig) -> dict[str, Any]:
    text = INPUT_RE.sub(" ", run.task)
    domain_scores = {name: _clamp(_hits(text, terms) / 3.0) for name, terms in DOMAIN_TERMS.items()}
    for index, domain in enumerate(profile.domains[:3]):
        domain_scores[domain] = max(domain_scores.get(domain, 0), 1.0 - index * 0.15)
    domains = [name for name, score in sorted(domain_scores.items(), key=lambda item: (-item[1], item[0])) if score >= 0.25][:3]
    if not domains:
        domains = [profile.primary_domain or "general"]
    operations = {name: _clamp(_hits(text, terms) / 2.0) for name, terms in OP_TERMS.items()}
    operations["analysis"] = max(operations["analysis"], 0.55)
    required = {f"domain:{domains[0]}", "analysis"}
    for domain in domains[1:]:
        if domain_scores[domain] >= 0.5:
            required.add(f"domain:{domain}")
    thresholds = {
        "evidence": 0.5, "quantitative": 0.5, "forecast": 0.5,
        "implementation": 0.5, "creative": 0.5, "adversarial": 0.5,
    }
    for operation, threshold in thresholds.items():
        if operations[operation] >= threshold:
            required.add(operation)
    if profile.high_stakes:
        required.update({"evidence", "adversarial"})
    if profile.complexity == "complex" and len(domains) >= 2:
        required.add(f"domain:{domains[1]}")
    return {
        "version": 2,
        "source": "accepted-task-text+validated-user-constraints",
        "domain_scores": {k: round(v, 6) for k, v in domain_scores.items()},
        "operation_scores": {k: round(v, 6) for k, v in operations.items()},
        "required_demands": sorted(required),
        "signals": {
            "high_stakes": profile.high_stakes, "complexity": profile.complexity,
            "long_context": profile.long_context, "requested_context": profile.requested_context,
            "domain_count": len(domains), "task_characters": len(text),
        },
        "constraints": _input(run.task, run),
        "history_input_used": False,
        "fixed_team_mode_used": False,
        "fixed_parameter_template_used": False,
    }


def _profession(domain: str, key: str) -> str:
    policy = load_json(POLICY_FILE)
    rows = policy["professions"].get(domain, policy["professions"]["general"])
    return str(rows.get(key) or rows.get("core") or "综合专家")


def _seat(key: str, function: str, domain: str, covers: Sequence[str], mission: str, kind: str) -> dict[str, Any]:
    profession_key = "red" if kind == "adversarial" else "cross" if key != "primary" else "core"
    return {"spec": SeatSpec(key, function, _profession(domain, profession_key), domain, mission), "covers": sorted(set(covers)), "kind": kind}


def generate_seats(matrix: Mapping[str, Any], profile: TaskProfile) -> list[dict[str, Any]]:
    demands = set(matrix["required_demands"])
    domains = [x.split(":", 1)[1] for x in demands if x.startswith("domain:")]
    primary = profile.primary_domain if profile.primary_domain in domains else (domains[0] if domains else "general")
    seats = [_seat("primary", "核心分析与决策席", primary, {"analysis", f"domain:{primary}"}, "完成核心问题分析、约束识别和可执行结论。", "primary")]
    for index, domain in enumerate(domain for domain in domains if domain != primary):
        seats.append(_seat(f"domain_{index+1}", f"{DOMAIN_LABEL.get(domain, domain)}交叉验证席", domain, {"analysis", "evidence", f"domain:{domain}"}, "从独立专业领域验证核心结论并识别冲突。", "domain"))
    if {"evidence", "quantitative", "forecast"} & demands:
        domain = "math" if "quantitative" in demands or "forecast" in demands else "research"
        seats.append(_seat("evidence_quant", "证据、定量与情景校准席", domain, {"evidence", "quantitative", "forecast", f"domain:{domain}"}, "核验数据、计算、假设、敏感性、预测和可证伪条件。", "evidence_quant"))
    if "implementation" in demands:
        seats.append(_seat("implementation", "工程实现与可运行性席", "coding", {"implementation", "quantitative", "domain:coding"}, "检查代码、接口、部署、故障边界和可运行性。", "implementation"))
    if "creative" in demands:
        seats.append(_seat("creative", "创意生成与表达席", "creative", {"creative", "domain:creative"}, "生成并评价创意表达，同时保持任务约束。", "creative"))
    if "adversarial" in demands:
        risk_domain = next((d for d in ("security", "legal", "medical", "international_relations") if f"domain:{d}" in demands), primary)
        seats.append(_seat("adversarial", "独立反证与失败模式席", risk_domain, {"adversarial", "evidence", f"domain:{risk_domain}"}, "主动寻找反例、风险、失败路径、否决条件和证据缺口。", "adversarial"))
    primary_covers = set(seats[0]["covers"])
    for demand in ("evidence", "quantitative", "forecast"):
        if matrix["operation_scores"].get(demand, 0) < 0.75:
            primary_covers.add(demand)
    if primary == "coding": primary_covers.add("implementation")
    if primary == "creative": primary_covers.add("creative")
    seats[0]["covers"] = sorted(primary_covers)
    return seats


def _eligible_pool(ranked: Sequence[ModelInfo], profile: TaskProfile) -> list[ModelInfo]:
    rows = []
    for model in ranked:
        text = f"{model.id} {model.name} {model.description}".casefold()
        rank = model.ranks.get("intelligence-high-to-low")
        if rank is None or rank > scoring.MAX_INTELLIGENCE_RANK or scoring.SMALL_MODEL_RE.search(f"{model.id} {model.name}"):
            continue
        if any(term in text for term in UNSTABLE) or model.context_length < profile.requested_context:
            continue
        rows.append(model)
    if len(rows) < 2:
        raise ExpertTeamError("No sufficient live stable models remain inside the intelligence top 50.")
    return rows


def _variants(model: ModelInfo, kind: str, matrix: Mapping[str, Any]) -> list[dict[str, Any]]:
    supported = set(model.supported_parameters)
    complex_task = matrix["signals"]["complexity"] == "complex"
    efforts = ["low"] if "reasoning" not in supported else (["medium", "high"] if complex_task or kind in {"adversarial", "evidence_quant", "judge"} else ["low", "medium"])
    result = []
    for effort in efforts:
        temperature = 0.02 if kind == "judge" else 0.12 if kind in {"adversarial", "creative"} else 0.06
        verbosity = "medium" if kind in {"adversarial", "evidence_quant"} or complex_task else "low"
        tokens = 2600 if kind == "judge" else 2200 if effort == "high" else 1500 if effort == "medium" else 900
        parameters = {
            "effort": effort, "temperature": temperature, "verbosity": verbosity,
            "structured_output": bool({"structured_outputs", "response_format"} & supported),
            "expected_output_tokens": min(tokens, max(256, model.max_completion_tokens)),
        }
        digest = sha256(json.dumps(parameters, sort_keys=True).encode()).hexdigest()[:10]
        result.append({"id": f"generated-{digest}", "parameters": parameters})
    return result


def _pool_for_seat(pool: Sequence[ModelInfo], seat: SeatSpec, kind: str, limit: int) -> list[ModelInfo]:
    rows = list(pool)
    if kind == "adversarial":
        risk = [m for m in rows if scoring._term_fit(m, scoring.RISK_TERMS) > 0]
        if risk: rows = risk
    rows.sort(key=lambda m: (-scoring._benchmark_score(m, seat.domain_focus), -scoring._domain_fit(m, seat.domain_focus), -_live_stability(m), m.blended_price_per_million or math.inf, m.id))
    return rows[:limit]


def _quality(model: ModelInfo, seat: SeatSpec, kind: str, variant: Mapping[str, Any], cost: float, max_cost: float, max_value: float, preferred: set[str]) -> tuple[int, dict[str, float]]:
    benchmark = _clamp(scoring._benchmark_score(model, seat.domain_focus) / 100.0)
    fit = _clamp(scoring._domain_fit(model, seat.domain_focus)); stability = _live_stability(model)
    raw_value = max(0.0, scoring._value_index(model, seat.domain_focus)); value = _clamp(raw_value / max(max_value, 1e-9))
    cost_score = _clamp(1.0 - cost / max(max_cost, 1e-9)); risk = scoring._term_fit(model, scoring.RISK_TERMS) if kind == "adversarial" else 0.0
    effort = {"low": 0.55, "medium": 0.8, "high": 1.0}.get(str(variant["parameters"]["effort"]), 0.55)
    utility = 0.38 * benchmark + 0.23 * fit + 0.14 * stability + 0.12 * value + 0.06 * cost_score + 0.04 * effort + 0.03 * risk
    if model.id in preferred: utility += 0.025
    components = {"benchmark": benchmark, "domain_fit": fit, "live_stability": stability, "normalized_value": value, "normalized_cost": cost_score, "reasoning_fit": effort, "risk_fit": risk, "utility": utility}
    return int(round(utility * 1_000_000)), {k: round(v, 6) for k, v in components.items()}


def _disable_history() -> None:
    import direct_calls, expert_team
    direct_calls._record_history = lambda *_a, **_k: None
    expert_team._record_history = lambda *_a, **_k: None
    expert_team._history_rejects_judge = lambda *_a, **_k: False
    expert_team._prefer_reliable_judge = lambda _r, _p, _m, _e, judge: judge


def _solver(seconds: float) -> cp_model.CpSolver:
    solver = cp_model.CpSolver(); solver.parameters.max_time_in_seconds = seconds
    solver.parameters.num_search_workers = 1; solver.parameters.random_seed = 0
    return solver


def select_team(ranked: Sequence[ModelInfo], profile: TaskProfile, run: RunConfig) -> tuple[list[SelectedExpert], SelectedJudge, float]:
    _disable_history(); matrix = build_task_matrix(profile, run); constraints = matrix["constraints"]
    seats = generate_seats(matrix, profile); pool = _eligible_pool(ranked, profile)
    scoring._enrich_benchmarks(run, pool); forbidden = set(constraints["forbidden_models"])
    pool = [m for m in pool if m.id not in forbidden]
    judge = SeatSpec("judge", "综合裁决席", _profession(profile.primary_domain, "judge"), profile.primary_domain, "综合全部专家证据，形成最终裁决。")
    seat_rows = {row["spec"].key: row for row in seats}; seat_rows["judge"] = {"spec": judge, "covers": [], "kind": "judge"}
    candidates = {key: _pool_for_seat(pool, row["spec"], row["kind"], constraints["candidate_pool_per_seat"]) for key, row in seat_rows.items()}
    if any(not rows for rows in candidates.values()):
        raise ExpertTeamError(f"No eligible models for seats: {[k for k, v in candidates.items() if not v]}")
    options: dict[tuple[str, str, str], dict[str, Any]] = {}; costs = []; values = []
    for key, models in candidates.items():
        row = seat_rows[key]
        for model in models:
            values.append(max(0.0, scoring._value_index(model, row["spec"].domain_focus)))
            for variant in _variants(model, row["kind"], matrix):
                tokens = int(variant["parameters"]["expected_output_tokens"])
                chars = len(run.task) + (len(seats) * 6000 + 3000 if key == "judge" else 1200)
                cost = estimate_call_cost(model, chars, tokens); costs.append(cost)
                options[(key, model.id, variant["id"])] = {"model": model, "variant": variant, "cost": cost, "row": row}
    max_cost = max(costs or [1]); max_value = max(values or [1]); preferred = set(constraints["preferred_models"])
    for option in options.values():
        score, parts = _quality(option["model"], option["row"]["spec"], option["row"]["kind"], option["variant"], option["cost"], max_cost, max_value, preferred)
        option["score"] = score; option["components"] = parts
    cp = cp_model.CpModel(); variables = {key: cp.NewBoolVar("x__" + "__".join(x.replace("/", "_") for x in key)) for key in options}
    active = {key: cp.NewBoolVar(f"active__{key}") for key in seat_rows if key != "judge"}
    for key in seat_rows:
        seat_vars = [var for option_key, var in variables.items() if option_key[0] == key]
        cp.Add(sum(seat_vars) == (1 if key == "judge" else active[key]))
    cp.Add(sum(active.values()) >= constraints["min_experts"]); cp.Add(sum(active.values()) <= constraints["max_experts"])
    for demand in matrix["required_demands"]:
        covering = [active[key] for key, row in seat_rows.items() if key != "judge" and demand in row["covers"]]
        if not covering: raise ExpertTeamError(f"No generated seat covers required demand {demand}.")
        cp.Add(sum(covering) >= 1)
    for model_id in {key[1] for key in variables}:
        cp.Add(sum(var for key, var in variables.items() if key[1] == model_id) <= 1)
    if constraints["strict_provider_diversity"]:
        providers: dict[str, list[Any]] = {}
        for key, var in variables.items(): providers.setdefault(options[key]["model"].author, []).append(var)
        for rows in providers.values(): cp.Add(sum(rows) <= 1)
    total_quality = sum(options[key]["score"] * var for key, var in variables.items())
    total_cost = sum(int(round(options[key]["cost"] * 1_000_000_000)) * var for key, var in variables.items())
    if constraints["budget_usd"] is not None: cp.Add(total_cost <= int(round(constraints["budget_usd"] * 1_000_000_000)))
    cp.Minimize(sum(active.values())); s1 = _solver(constraints["solver_timeout_seconds"]); st1 = s1.Solve(cp)
    if st1 not in {cp_model.OPTIMAL, cp_model.FEASIBLE}: raise ExpertTeamError(f"No feasible task-matrix team: {s1.StatusName(st1)}")
    count = int(round(s1.ObjectiveValue())); cp.Add(sum(active.values()) == count)
    cp.Maximize(total_quality); s2 = _solver(constraints["solver_timeout_seconds"]); st2 = s2.Solve(cp)
    if st2 not in {cp_model.OPTIMAL, cp_model.FEASIBLE}: raise ExpertTeamError(f"Quality optimization failed: {s2.StatusName(st2)}")
    quality = int(round(s2.ObjectiveValue())); floor = int(math.floor(quality * (1 - constraints["quality_tolerance_pct"] / 100)))
    cp.Add(total_quality >= floor); cp.Minimize(total_cost); solver = _solver(constraints["solver_timeout_seconds"]); status = solver.Solve(cp)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}: raise ExpertTeamError(f"Cost optimization failed: {solver.StatusName(status)}")
    selected = {key[0]: {**options[key], "seat_key": key[0]} for key, var in variables.items() if solver.Value(var)}
    experts = []
    for row in seats:
        key = row["spec"].key
        if key not in selected: continue
        option = selected[key]; model = option["model"]; spec = row["spec"]
        reason = f"任务矩阵+CP-SAT；覆盖={','.join(row['covers'])}；实时基准={option['components']['benchmark']:.3f}；匹配={option['components']['domain_fit']:.3f}；预计成本=${option['cost']:.6f}；参数={option['variant']['id']}；无历史账本"
        experts.append(SelectedExpert(spec.key, spec.function, spec.profession, spec.domain_focus, spec.mission, model.id, reason))
    judge_option = selected["judge"]; judge_model = judge_option["model"]
    selected_judge = SelectedJudge(judge.function, judge.profession, judge_model.id, f"任务矩阵+CP-SAT；实时基准={judge_option['components']['benchmark']:.3f}；预计成本=${judge_option['cost']:.6f}；参数={judge_option['variant']['id']}；与专家模型和Provider去重；无历史账本")
    estimated = sum(row["cost"] for row in selected.values())
    plan = {
        "version": 2, "optimizer": "google-or-tools-cp-sat", "selection_method": "history-free-task-matrix-lexicographic-v2",
        "solver_status": solver.StatusName(status), "phase_status": {"cardinality": s1.StatusName(st1), "quality": s2.StatusName(st2), "cost": solver.StatusName(status)},
        "lexicographic_objective": ["minimum_sufficient_expert_count", "maximum_live_quality", "minimum_cost_within_quality_tolerance"],
        "minimum_sufficient_expert_count": count, "best_quality_score": quality, "quality_floor": floor,
        "objective_value": solver.ObjectiveValue(), "best_objective_bound": solver.BestObjectiveBound(),
        "task_matrix": matrix, "team_pattern": f"{len(experts)}-experts-plus-one-judge", "expert_count": len(experts), "estimated_cost_usd": round(estimated, 9),
        "strict_provider_diversity": constraints["strict_provider_diversity"],
        "selected": {key: {"seat": key, "function": row["row"]["spec"].function, "profession": row["row"]["spec"].profession, "domain": row["row"]["spec"].domain_focus, "covers": row["row"]["covers"], "model": row["model"].id, "provider": row["model"].author, "parameter_template": row["variant"]["id"], "parameter_source": "generated_from_task_matrix", "parameters": row["variant"]["parameters"], "estimated_cost_usd": round(row["cost"], 9), "score_components": row["components"]} for key, row in selected.items()},
        "constraints": {"task_demands_must_be_covered": matrix["required_demands"], "one_model_per_active_seat": True, "model_reuse_forbidden": True, "provider_reuse_forbidden": constraints["strict_provider_diversity"], "intelligence_rank_hard_ceiling": scoring.MAX_INTELLIGENCE_RANK, "live_stable_direct_models_only": True, "budget_usd": constraints["budget_usd"], "history_input_used": False, "persistent_history_write_enabled": False, "fixed_team_mode_used": False, "fixed_parameter_template_used": False, "external_tools_allowed": False},
        "candidate_counts": {key: len(rows) for key, rows in candidates.items()}, "generated_seat_count": len(seats), "fallback_used": False,
    }
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "task-parameter-matrix.json").write_text(json.dumps(matrix, ensure_ascii=False, indent=2), encoding="utf-8")
    (run.output_dir / "team-optimization.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    object.__setattr__(profile, "team_pattern", plan["team_pattern"]); object.__setattr__(profile, "expert_count", len(experts))
    if len(experts) != 3: object.__setattr__(run, "require_all_experts", False)
    dynamic_runtime.activate_runtime(plan, run, profile, experts, selected_judge)
    return experts, selected_judge, estimated
