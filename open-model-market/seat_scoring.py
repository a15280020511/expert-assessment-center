"""Deterministic value-first model selection for the fixed 3+1 topology.

The selector uses hard capability and reliability gates, enriches eligible
models with OpenRouter benchmark data, and makes cost-performance the first
decision key for the default ``value`` tier. It never uses speed or popularity.
"""
from __future__ import annotations

import json
import math
import re
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from model_market import (
    ExpertTeamError,
    ModelInfo,
    RunConfig,
    SelectedExpert,
    SelectedJudge,
    TaskProfile,
    build_fixed_seats,
    estimate_call_cost,
)
from openrouter_api import request_json
from reasoning_policy import expert_inference_plan, judge_inference_plan

BENCHMARKS_URL = "https://openrouter.ai/api/v1/benchmarks"
RISK_TERMS = (
    "risk", "security", "safety", "legal", "law", "compliance", "audit",
    "adversarial", "failure", "red team", "监管", "合规", "安全", "风险", "审计", "反证",
)
CODE_SPECIALIST_TERMS = ("/code", "-code", " coder", "coding", "code specialist", "programming")
UNSTABLE_TERMS = ("preview", "experimental", "alpha", "beta", "spark", ":free")
SMALL_MODEL_RE = re.compile(r"(?:^|[-_/])(?:0\.\d+|[1-8])b(?:[-_/]|$)", re.IGNORECASE)
MAX_INTELLIGENCE_RANK = 50
VALUE_PRICE_FLOOR_PER_MILLION = 0.25

RULE_ORDER = {
    "quality": "稳定版本→智能排名前50→排除1B-8B→席位专业资格→绝对基准能力→厂商独立→预算",
    "value": "稳定版本→智能排名前50→排除1B-8B→席位专业资格→性价比→绝对基准能力→厂商独立→预算",
    "budget": "稳定版本→智能排名前50→排除1B-8B→席位专业资格→价格→性价比→能力→厂商独立",
}


def _text(model: ModelInfo) -> str:
    return f"{model.id} {model.name} {model.description}".lower()


def _term_fit(model: ModelInfo, terms: Sequence[str]) -> float:
    text = _text(model)
    return min(1.0, sum(1 for term in terms if term in text) / 3.0)


def _domain_fit(model: ModelInfo, domain: str) -> float:
    terms = {
        "international_relations": (
            "geopolit", "foreign policy", "diplomacy", "policy", "international", "reasoning", "analysis",
        ),
        "legal": ("legal", "law", "compliance", "regulation", "contract", "policy", "reasoning"),
        "medical": ("medical", "clinical", "health", "science", "evidence", "research", "reasoning"),
        "security": ("security", "cyber", "risk", "audit", "adversarial", "reasoning", "analysis"),
        "supply_chain": ("supply chain", "logistics", "operations", "optimization", "business", "analysis"),
        "public_policy": ("policy", "government", "research", "evidence", "economics", "analysis"),
        "coding": ("code", "coding", "software", "programming", "developer", "debug", "security"),
        "math": ("math", "mathematical", "reasoning", "science", "proof", "statistics", "simulation"),
        "research": ("research", "knowledge", "evidence", "document", "policy", "analysis"),
        "business": ("analysis", "business", "finance", "economic", "enterprise", "strategy", "investment"),
        "creative": ("creative", "writing", "story", "style", "design"),
    }.get(domain, ("general", "reasoning", "analysis"))
    return _term_fit(model, terms)


def _is_unstable(model: ModelInfo) -> bool:
    return any(term in _text(model) for term in UNSTABLE_TERMS)


def _is_explicitly_small(model: ModelInfo) -> bool:
    return bool(SMALL_MODEL_RE.search(f"{model.id} {model.name}"))


def _within_capability_floor(model: ModelInfo) -> bool:
    rank = model.ranks.get("intelligence-high-to-low")
    return rank is not None and rank <= MAX_INTELLIGENCE_RANK and not _is_explicitly_small(model)


def _history_bucket(model: ModelInfo) -> int:
    value = float(model.components.get("history", 0.55))
    if value >= 0.55:
        return 2
    if value >= 0.30:
        return 1
    return 0


def _quality(model: ModelInfo) -> float:
    return float(model.components.get("quality", 0.0))


def _price(model: ModelInfo) -> float:
    return model.blended_price_per_million if model.blended_price_per_million is not None else math.inf


def _finite_score(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _fallback_intelligence(model: ModelInfo) -> float:
    rank = model.ranks.get("intelligence-high-to-low")
    if rank is not None:
        return max(1.0, 100.0 * (MAX_INTELLIGENCE_RANK - rank + 1) / MAX_INTELLIGENCE_RANK)
    quality = _quality(model)
    return max(1.0, min(100.0, quality * 100.0))


def _benchmark_scores(model: ModelInfo) -> Dict[str, float]:
    raw = getattr(model, "benchmark_scores", {})
    return dict(raw) if isinstance(raw, Mapping) else {}


def _benchmark_source(model: ModelInfo) -> str:
    return str(getattr(model, "benchmark_source", "intelligence-rank-fallback"))


def _benchmark_score(model: ModelInfo, domain: str) -> float:
    scores = _benchmark_scores(model)
    intelligence = _finite_score(scores.get("intelligence_index"))
    coding = _finite_score(scores.get("coding_index"))
    agentic = _finite_score(scores.get("agentic_index"))

    if domain == "coding":
        parts: List[Tuple[float, float]] = []
        if coding is not None:
            parts.append((coding, 0.70))
        if intelligence is not None:
            parts.append((intelligence, 0.20))
        if agentic is not None:
            parts.append((agentic, 0.10))
        if parts:
            total_weight = sum(weight for _, weight in parts)
            return sum(score * weight for score, weight in parts) / total_weight

    if intelligence is not None:
        return intelligence
    if coding is not None:
        return coding
    if agentic is not None:
        return agentic
    return _fallback_intelligence(model)


def _value_index(model: ModelInfo, domain: str) -> float:
    price = max(_price(model), VALUE_PRICE_FLOOR_PER_MILLION)
    return _benchmark_score(model, domain) / price


def _fallback_scores(model: ModelInfo) -> Dict[str, float]:
    return {"intelligence_index": round(_fallback_intelligence(model), 6)}


def _apply_benchmark_rows(
    models: Sequence[ModelInfo],
    rows: Mapping[str, Mapping[str, Any]],
    *,
    source: str,
) -> Dict[str, int]:
    direct = 0
    fallback = 0
    for model in models:
        row = rows.get(model.id, {})
        scores: Dict[str, float] = {}
        for key in ("intelligence_index", "coding_index", "agentic_index"):
            value = _finite_score(row.get(key)) if isinstance(row, Mapping) else None
            if value is not None:
                scores[key] = value
        if scores:
            direct += 1
            model.benchmark_scores = scores
            model.benchmark_source = source
        else:
            fallback += 1
            model.benchmark_scores = _fallback_scores(model)
            model.benchmark_source = "intelligence-rank-fallback"
        model.components["benchmark_intelligence"] = _benchmark_score(model, "general")
        model.components["benchmark_coding"] = _benchmark_score(model, "coding")
        model.components["value_general"] = _value_index(model, "general")
        model.components["value_coding"] = _value_index(model, "coding")
    return {"direct": direct, "fallback": fallback}


def _enrich_benchmarks(run: RunConfig, models: Sequence[ModelInfo]) -> Dict[str, Any]:
    rows: Dict[str, Mapping[str, Any]] = {}
    source = "intelligence-rank-fallback"
    degraded = True
    error = ""
    meta: Dict[str, Any] = {}

    if not run.catalog_file and run.api_key:
        try:
            payload = request_json(
                BENCHMARKS_URL,
                run.api_key,
                run.catalog_timeout_seconds,
                run.catalog_max_retries,
            )
            data = payload.get("data") if isinstance(payload, Mapping) else None
            if not isinstance(data, list):
                raise ExpertTeamError("OpenRouter benchmarks response missing data array.")
            for row in data:
                if not isinstance(row, Mapping):
                    continue
                model_id = str(row.get("model_permaslug") or "")
                if model_id:
                    rows[model_id] = row
            meta = dict(payload.get("meta") or {}) if isinstance(payload.get("meta"), Mapping) else {}
            if rows:
                source = "openrouter-benchmarks"
                degraded = False
            else:
                error = "OpenRouter benchmark endpoint returned no usable model rows."
        except Exception as exc:
            error = str(exc)

    counts = _apply_benchmark_rows(models, rows, source=source)
    if counts["fallback"]:
        degraded = True
    evidence = {
        "version": 1,
        "source": source,
        "degraded": degraded,
        "error": error,
        "meta": meta,
        "eligible_model_count": len(models),
        "direct_benchmark_count": counts["direct"],
        "fallback_count": counts["fallback"],
        "fallback_policy": "official intelligence rank converted to a 1-100 capability score",
        "value_formula": "task benchmark score / max(blended prompt-output USD per million tokens, 0.25)",
        "models": {
            model.id: {
                "scores": _benchmark_scores(model),
                "source": _benchmark_source(model),
                "blended_price_usd_per_million": _price(model),
                "value_general": round(_value_index(model, "general"), 6),
                "value_coding": round(_value_index(model, "coding"), 6),
            }
            for model in models
        },
    }
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "benchmark-market.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return evidence


def _priority_key(model: ModelInfo, seat_key: str, domain: str, tier: str) -> tuple:
    stability = _history_bucket(model)
    benchmark = _benchmark_score(model, domain)
    value = _value_index(model, domain)
    quality = _quality(model)
    domain_fit = _domain_fit(model, domain)
    risk_fit = _term_fit(model, RISK_TERMS)
    price = _price(model)
    domain_gate = 0 if domain_fit > 0 else 1
    risk_gate = 0 if risk_fit > 0 else 1

    if tier == "budget":
        if seat_key == "red":
            return (risk_gate, domain_gate, price, -value, -stability, -benchmark, model.id)
        return (domain_gate, price, -value, -stability, -benchmark, model.id)
    if tier == "quality":
        if seat_key == "red":
            return (risk_gate, domain_gate, -benchmark, -stability, -risk_fit, price, model.id)
        if seat_key == "judge":
            structured = int("structured_outputs" in model.supported_parameters)
            reasoning = int("reasoning" in model.supported_parameters)
            return (domain_gate, -benchmark, -stability, -structured, -reasoning, price, model.id)
        return (domain_gate, -benchmark, -stability, price, model.id)
    if seat_key == "red":
        return (risk_gate, domain_gate, -value, -stability, -risk_fit, -benchmark, price, model.id)
    if seat_key == "judge":
        structured = int("structured_outputs" in model.supported_parameters)
        reasoning = int("reasoning" in model.supported_parameters)
        return (domain_gate, -value, -stability, -benchmark, -structured, -reasoning, price, model.id)
    return (domain_gate, -value, -stability, -benchmark, price, -quality, model.id)


def _stable_pool(models: Iterable[ModelInfo], profile: TaskProfile) -> List[ModelInfo]:
    del profile
    capable = [
        model
        for model in models
        if not _is_unstable(model)
        and _history_bucket(model) > 0
        and _within_capability_floor(model)
    ]
    if len(capable) < 4:
        raise ExpertTeamError(
            f"Fixed 3+1 requires at least four stable direct models ranked within the top "
            f"{MAX_INTELLIGENCE_RANK}, from distinct providers, and not explicitly sized 1B-8B. "
            "The selector will not widen the intelligence range."
        )
    return capable


def _ordered(models: Iterable[ModelInfo], seat_key: str, domain: str, tier: str) -> List[ModelInfo]:
    return sorted(models, key=lambda model: _priority_key(model, seat_key, domain, tier))


def _seat_pool(models: Sequence[ModelInfo], seat_key: str, domain: str) -> List[ModelInfo]:
    del seat_key, domain
    return list(models)


def _eligible_distinct(
    ordered: Sequence[ModelInfo],
    used_ids: set[str],
    used_authors: set[str],
) -> List[ModelInfo]:
    return [model for model in ordered if model.id not in used_ids and model.author not in used_authors]


def _pick(
    ordered: Sequence[ModelInfo],
    used_ids: set[str],
    used_authors: set[str],
    limit: int,
) -> ModelInfo:
    candidates = _eligible_distinct(ordered, used_ids, used_authors)[:limit]
    if not candidates:
        raise ExpertTeamError(
            "Unable to build a provider-diverse fixed 3+1 combination from the intelligence top-50 candidates."
        )
    return candidates[0]


def _combination_cost(
    run: RunConfig,
    profile: TaskProfile,
    seats,
    expert_models: Sequence[ModelInfo],
    judge_model: ModelInfo,
) -> float:
    expert_plans = []
    for seat, model in zip(seats, expert_models):
        stub = SelectedExpert(
            seat.key, seat.function, seat.profession, seat.domain_focus, seat.mission, model.id, ""
        )
        expert_plans.append(expert_inference_plan(run, profile, stub, model))
    judge_stub = SelectedJudge("综合裁决席", "judge", judge_model.id, "")
    judge_plan = judge_inference_plan(run, profile, judge_stub, judge_model)
    expert_cost = sum(
        estimate_call_cost(model, len(run.task) + 1200, plan.max_tokens)
        for model, plan in zip(expert_models, expert_plans)
    )
    judge_input_chars = len(run.task) + sum(plan.max_tokens * 4 for plan in expert_plans) + 3000
    judge_cost = estimate_call_cost(judge_model, judge_input_chars, judge_plan.max_tokens)
    return expert_cost + judge_cost


def _choose_once(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
    tier: str,
) -> tuple[list, str, List[ModelInfo], ModelInfo, float]:
    seats, judge_profession = build_fixed_seats(profile)
    pool = _stable_pool(ranked, profile)
    limit = max(1, min(3, run.candidate_pool_per_seat))
    used_ids: set[str] = set()
    used_authors: set[str] = set()
    expert_models: List[ModelInfo] = []

    for seat in seats:
        seat_pool = _seat_pool(pool, seat.key, seat.domain_focus)
        model = _pick(
            _ordered(seat_pool, seat.key, seat.domain_focus, tier),
            used_ids,
            used_authors,
            limit,
        )
        expert_models.append(model)
        used_ids.add(model.id)
        used_authors.add(model.author)

    judge_base = _seat_pool(pool, "judge", profile.primary_domain)
    judge_pool = [
        model
        for model in judge_base
        if profile.primary_domain == "coding"
        or not any(term in _text(model) for term in CODE_SPECIALIST_TERMS)
    ] or judge_base or pool
    judge_model = _pick(
        _ordered(judge_pool, "judge", profile.primary_domain, tier),
        used_ids,
        used_authors,
        limit,
    )
    estimated = _combination_cost(run, profile, seats, expert_models, judge_model)
    return seats, judge_profession, expert_models, judge_model, estimated


def _reason_lines(
    model: ModelInfo,
    *,
    tier: str,
    domain: str,
    history_label: str,
) -> List[str]:
    return [
        f"规则顺序={RULE_ORDER[tier]}",
        "智能榜硬上限=前50",
        "候选池上限=3",
        f"智能排名={model.ranks.get('intelligence-high-to-low')}",
        f"基准来源={_benchmark_source(model)}",
        f"有效基准能力={_benchmark_score(model, domain):.3f}",
        f"综合价格={_price(model):.6f}美元/百万token",
        f"性价比指数={_value_index(model, domain):.6f}",
        "席位专业资格优先，专业池不足时自动回退",
        "已排除明确1B-8B小模型",
        "模型与厂商独立",
        f"领域匹配={_domain_fit(model, domain):.3f}",
        f"历史状态={history_label}",
    ]


def _write_value_selection(
    run: RunConfig,
    profile: TaskProfile,
    tier: str,
    experts: Sequence[SelectedExpert],
    judge: SelectedJudge,
    by_id: Mapping[str, ModelInfo],
) -> None:
    rows = []
    for expert in experts:
        model = by_id[expert.model_id]
        rows.append({
            "seat": expert.seat_key,
            "function": expert.function,
            "model": model.id,
            "domain": expert.domain_focus,
            "benchmark_source": _benchmark_source(model),
            "benchmark_score": round(_benchmark_score(model, expert.domain_focus), 6),
            "blended_price_usd_per_million": round(_price(model), 6),
            "value_index": round(_value_index(model, expert.domain_focus), 6),
        })
    judge_model = by_id[judge.model_id]
    rows.append({
        "seat": "judge",
        "function": judge.function,
        "model": judge_model.id,
        "domain": profile.primary_domain,
        "benchmark_source": _benchmark_source(judge_model),
        "benchmark_score": round(_benchmark_score(judge_model, profile.primary_domain), 6),
        "blended_price_usd_per_million": round(_price(judge_model), 6),
        "value_index": round(_value_index(judge_model, profile.primary_domain), 6),
    })
    evidence = {
        "version": 1,
        "quality_tier": tier,
        "primary_rule": "seat-qualified-cost-performance-first" if tier == "value" else tier,
        "value_formula": "task benchmark score / max(blended prompt-output USD per million tokens, 0.25)",
        "selected": rows,
    }
    (run.output_dir / "value-selection.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def select_team(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
) -> Tuple[List[SelectedExpert], SelectedJudge, float]:
    pool_for_benchmarks = _stable_pool(ranked, profile)
    _enrich_benchmarks(run, pool_for_benchmarks)

    chosen_tier = run.quality_tier
    seats, judge_profession, expert_models, judge_model, estimated = _choose_once(
        ranked, profile, run, chosen_tier
    )
    if run.max_estimated_cost_usd is not None and estimated * run.budget_safety_factor > run.max_estimated_cost_usd:
        if chosen_tier != "budget":
            chosen_tier = "budget"
            seats, judge_profession, expert_models, judge_model, estimated = _choose_once(
                ranked, profile, run, chosen_tier
            )
        if estimated * run.budget_safety_factor > run.max_estimated_cost_usd:
            required = estimated * run.budget_safety_factor
            raise ExpertTeamError(
                f"No rule-selected fixed 3+1 combination fits the hard budget after safety factor; "
                f"minimum selected requirement is ${required:.4f}."
            )

    selected: List[SelectedExpert] = []
    for seat, model in zip(seats, expert_models):
        reasons = _reason_lines(
            model,
            tier=chosen_tier,
            domain=seat.domain_focus,
            history_label="正常" if _history_bucket(model) == 2 else "观察",
        )
        if seat.key == "red":
            reasons.append(f"风险反证匹配={_term_fit(model, RISK_TERMS):.3f}")
        if chosen_tier == "budget" and run.quality_tier != "budget":
            reasons.append("预算超限后按低成本规则降档")
        selected.append(
            SelectedExpert(
                seat.key,
                seat.function,
                seat.profession,
                seat.domain_focus,
                seat.mission,
                model.id,
                "；".join(reasons),
            )
        )

    judge = SelectedJudge(
        "综合裁决席",
        judge_profession,
        judge_model.id,
        "；".join(_reason_lines(
            judge_model,
            tier=chosen_tier,
            domain=profile.primary_domain,
            history_label="正常" if _history_bucket(judge_model) == 2 else "观察",
        )),
    )
    by_id = {model.id: model for model in ranked}
    _write_value_selection(run, profile, chosen_tier, selected, judge, by_id)
    return selected, judge, estimated


def replacement_candidates(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    expert: SelectedExpert,
    used_ids: set[str],
    used_authors: set[str],
) -> List[ModelInfo]:
    del profile
    pool = [
        model
        for model in ranked
        if model.id not in used_ids
        and not _is_unstable(model)
        and _history_bucket(model) > 0
        and _within_capability_floor(model)
    ]
    pool = _seat_pool(pool, expert.seat_key, expert.domain_focus)
    distinct = [model for model in pool if model.author not in used_authors]
    return _ordered(distinct or pool, expert.seat_key, expert.domain_focus, "value")[:3]


def _candidate_row(
    model: ModelInfo,
    *,
    index: int,
    domain: str,
    selected: bool,
) -> Dict[str, float | str | bool]:
    return {
        "model": model.id,
        "rule_rank": index,
        "selected": selected,
        "intelligence_rank": model.ranks.get("intelligence-high-to-low") or "",
        "benchmark_source": _benchmark_source(model),
        "benchmark_score": round(_benchmark_score(model, domain), 6),
        "domain_fit": round(_domain_fit(model, domain), 6),
        "price_per_million": round(_price(model), 6),
        "value_index": round(_value_index(model, domain), 6),
    }


def top_candidates_for_evidence(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
    limit: int = 3,
) -> Dict[str, List[Dict[str, float | str | bool]]]:
    seats, _ = build_fixed_seats(profile)
    pool = _stable_pool(ranked, profile)
    effective_tier = run.quality_tier
    tier_path = run.output_dir / "value-selection.json"
    if tier_path.exists():
        try:
            stored = json.loads(tier_path.read_text(encoding="utf-8"))
            candidate_tier = str(stored.get("quality_tier") or "")
            if candidate_tier in RULE_ORDER:
                effective_tier = candidate_tier
        except (json.JSONDecodeError, OSError):
            pass
    evidence: Dict[str, List[Dict[str, float | str | bool]]] = {}
    capped = min(3, max(1, limit))
    used_ids: set[str] = set()
    used_authors: set[str] = set()

    for seat in seats:
        seat_pool = _seat_pool(pool, seat.key, seat.domain_focus)
        ordered = _ordered(seat_pool, seat.key, seat.domain_focus, effective_tier)
        models = _eligible_distinct(ordered, used_ids, used_authors)[:capped]
        evidence[seat.key] = [
            _candidate_row(model, index=index, domain=seat.domain_focus, selected=index == 1)
            for index, model in enumerate(models, 1)
        ]
        if models:
            used_ids.add(models[0].id)
            used_authors.add(models[0].author)

    judge_base = _seat_pool(pool, "judge", profile.primary_domain)
    judge_pool = [
        model
        for model in judge_base
        if profile.primary_domain == "coding"
        or not any(term in _text(model) for term in CODE_SPECIALIST_TERMS)
    ] or judge_base or pool
    judge_models = _eligible_distinct(
        _ordered(judge_pool, "judge", profile.primary_domain, effective_tier),
        used_ids,
        used_authors,
    )[:capped]
    evidence["judge"] = [
        _candidate_row(model, index=index, domain=profile.primary_domain, selected=index == 1)
        for index, model in enumerate(judge_models, 1)
    ]
    return evidence
