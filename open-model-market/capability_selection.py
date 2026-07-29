"""Capability-aware deterministic seat design layered over the existing selector.

The semantic router may describe required capabilities, but code remains the only
component that creates seats and chooses exact model IDs. Selection and candidate
audit use the same scoped policy context; reliability overrides are explicitly
recorded rather than hidden.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Callable, Iterable, List, Sequence, Tuple

import benchmark_selection
import seat_scoring as base
from model_market import (
    ExpertTeamError,
    ModelInfo,
    POLICY_FILE,
    RunConfig,
    SeatSpec,
    SelectedExpert,
    SelectedJudge,
    TaskProfile,
    load_json,
)

CAPABILITY_DOMAIN_TERMS = {
    "security": ("网络安全", "数据安全", "信息安全", "cyber", "security", "threat", "攻击", "漏洞"),
    "legal": ("合规", "监管", "法规", "法律", "注册", "compliance", "regulation", "legal"),
    "medical": ("医疗", "临床", "诊疗", "器械", "medical", "clinical", "health"),
    "supply_chain": ("供应链", "采购", "物流", "零部件", "交付", "supply chain", "procurement"),
    "public_policy": ("公共政策", "产业政策", "政府", "政策", "public policy", "government"),
    "international_relations": ("跨境", "出口管制", "制裁", "国际", "geopolit", "sanction"),
    "math": ("建模", "仿真", "统计", "敏感性", "modeling", "simulation", "statistics"),
    "business": ("商业", "战略", "财务", "融资", "投资", "市场", "finance", "business", "strategy"),
    "research": ("证据", "研究", "核验", "数据", "evidence", "research"),
    "coding": ("代码", "软件", "API", "编程", "code", "software"),
}
RISK_DOMAIN_ORDER = (
    "security",
    "legal",
    "medical",
    "international_relations",
    "supply_chain",
    "public_policy",
    "math",
    "research",
    "business",
    "coding",
    "creative",
    "general",
)
_LAST_REQUIRED_CAPABILITIES: tuple[str, ...] = ()


def capability_domains(profile: TaskProfile, capabilities: Sequence[str]) -> List[str]:
    """Conservatively infer extra domains from router capability labels."""
    domains = list(profile.domains)
    text = " ".join(str(item or "") for item in capabilities).casefold()
    for domain, terms in CAPABILITY_DOMAIN_TERMS.items():
        if any(term.casefold() in text for term in terms) and domain not in domains:
            domains.append(domain)
    return domains


def _distinct_authors(models: Iterable[ModelInfo]) -> int:
    return len({model.author for model in models})


def _filtered_pool(models: Sequence[ModelInfo], seat_key: str, domain: str) -> List[ModelInfo]:
    """Use professional pools only when they can still support fixed 3+1 diversity."""
    rows = list(models)
    if domain != "coding":
        non_code = [model for model in rows if not any(term in base._text(model) for term in base.CODE_SPECIALIST_TERMS)]
        if _distinct_authors(non_code) >= 4:
            rows = non_code

    domain_rows = [model for model in rows if base._domain_fit(model, domain) > 0]
    if seat_key == "red":
        risk_domain = [model for model in domain_rows if base._term_fit(model, base.RISK_TERMS) > 0]
        if _distinct_authors(risk_domain) >= 4:
            return risk_domain
        risk_rows = [model for model in rows if base._term_fit(model, base.RISK_TERMS) > 0]
        if _distinct_authors(risk_rows) >= 4:
            return risk_rows
    if _distinct_authors(domain_rows) >= 4:
        return domain_rows
    return rows


def _judge_safe(model: ModelInfo) -> bool:
    """Diagnostic only: unbounded production may use any reliable direct judge."""
    supported = set(model.supported_parameters)
    return "reasoning" not in supported or bool(model.reasoning.get("supports_max_tokens"))


def _seat_policy(profile: TaskProfile, required_capabilities: Sequence[str]):
    policy = load_json(POLICY_FILE)
    professions = policy["professions"]
    domains = capability_domains(profile, required_capabilities)
    primary = profile.primary_domain if profile.primary_domain in professions else "general"
    cross = profile.secondary_domain if profile.secondary_domain in professions else primary
    if cross == primary:
        cross = next((domain for domain in domains if domain != primary and domain in professions), primary)
    red = next((domain for domain in RISK_DOMAIN_ORDER if domain in domains and domain != primary), cross)
    if red not in professions:
        red = primary
    return policy, professions, domains, primary, cross, red


@contextmanager
def _policy_context(profile: TaskProfile, required_capabilities: Sequence[str]):
    policy, professions, domains, primary, cross, red = _seat_policy(profile, required_capabilities)
    capabilities_text = "、".join(str(item) for item in required_capabilities if str(item).strip())

    def build_capability_seats(_profile: TaskProfile):
        seat_domains = {"core": primary, "cross": cross, "red": red}
        result: List[SeatSpec] = []
        for raw in policy["seats"]:
            key = raw["key"]
            domain = seat_domains[key]
            profession = professions.get(domain, professions["general"])[key]
            mission = raw["mission"]
            if capabilities_text:
                mission += f" 本任务必须覆盖的能力画像：{capabilities_text}。"
            result.append(SeatSpec(key, raw["function"], profession, domain, mission))
        return result, professions.get(primary, professions["general"])["judge"]

    original_build = base.build_fixed_seats
    original_pool = base._seat_pool
    original_priority = base._priority_key

    def priority_with_judge_safety(model: ModelInfo, seat_key: str, domain: str, tier: str):
        original = original_priority(model, seat_key, domain, tier)
        if seat_key != "judge":
            return original
        return (0 if _judge_safe(model) else 1,) + tuple(original)

    base.build_fixed_seats = build_capability_seats
    base._seat_pool = _filtered_pool
    base._priority_key = priority_with_judge_safety
    try:
        yield {
            "required_capabilities": [str(item) for item in required_capabilities],
            "derived_domains": domains,
            "seat_domains": {"core": primary, "cross": cross, "red": red, "judge": primary},
        }
    finally:
        base.build_fixed_seats = original_build
        base._seat_pool = original_pool
        base._priority_key = original_priority


def _pool_status(model: ModelInfo, seat_key: str, domain: str) -> str:
    domain_fit = base._domain_fit(model, domain)
    risk_fit = base._term_fit(model, base.RISK_TERMS) if seat_key == "red" else 0.0
    if seat_key == "red" and domain_fit > 0 and risk_fit > 0:
        return "风险与领域专业池命中"
    if seat_key == "red" and risk_fit > 0:
        return "风险专业池命中"
    if domain_fit > 0:
        return "领域专业池命中"
    return "专业池不足，按能力底线、厂商独立和性价比回退通用模型"


def _annotate_selected(
    ranked: Sequence[ModelInfo],
    experts: Sequence[SelectedExpert],
    judge: SelectedJudge,
) -> Tuple[list[SelectedExpert], SelectedJudge]:
    by_id = {model.id: model for model in ranked}
    annotated = []
    for expert in experts:
        model = by_id[expert.model_id]
        annotated.append(replace(
            expert,
            selection_reason=(
                expert.selection_reason
                + f"；能力席位领域={expert.domain_focus}"
                + f"；专业池状态={_pool_status(model, expert.seat_key, expert.domain_focus)}"
            ),
        ))
    judge_model = by_id[judge.model_id]
    safe = "是" if _judge_safe(judge_model) else "否，使用无Token上限和历史完整交付保护"
    return annotated, replace(judge, selection_reason=judge.selection_reason + f"；裁判最终答案安全优先={safe}")


def select_team(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
    required_capabilities: Sequence[str] = (),
) -> Tuple[list[SelectedExpert], SelectedJudge, float]:
    """Select the team with capability-aware seats and final-answer safeguards."""
    global _LAST_REQUIRED_CAPABILITIES
    _LAST_REQUIRED_CAPABILITIES = tuple(str(item) for item in required_capabilities)
    with _policy_context(profile, required_capabilities):
        experts, judge, estimated = benchmark_selection.select_team(ranked, profile, run)
    experts, judge = _annotate_selected(ranked, experts, judge)
    return experts, judge, estimated


def _override_candidate(data: dict, seat: str, model_id: str) -> None:
    candidates = data.get("seat_candidates")
    if not isinstance(candidates, dict):
        raise ExpertTeamError("model-selection.json is missing seat_candidates.")
    rows = candidates.get(seat)
    if not isinstance(rows, list):
        raise ExpertTeamError(f"Candidate audit is missing seat {seat}.")
    found = False
    for row in rows:
        if not isinstance(row, dict):
            continue
        row["selected"] = row.get("model") == model_id
        found = found or bool(row["selected"])
    if found:
        return
    if seat != "judge":
        raise ExpertTeamError(
            f"Selected model {model_id} for seat {seat} is absent from the audited candidate list."
        )

    ranking = data.get("ranking") if isinstance(data.get("ranking"), list) else []
    ranked = next((row for row in ranking if isinstance(row, dict) and row.get("model") == model_id), {})
    if not ranked:
        raise ExpertTeamError(
            f"History reliability judge override {model_id} is absent from the audited full ranking."
        )
    rows.append(
        {
            "model": model_id,
            "rule_rank": "history_reliability_override",
            "selected": True,
            "intelligence_rank": (ranked.get("official_ranks") or {}).get("intelligence-high-to-low", "")
            if isinstance(ranked.get("official_ranks"), dict)
            else "",
            "benchmark_source": "runtime-history-override",
            "benchmark_score": (ranked.get("components") or {}).get("benchmark_intelligence", 0)
            if isinstance(ranked.get("components"), dict)
            else 0,
            "domain_fit": (ranked.get("components") or {}).get("fit", 0)
            if isinstance(ranked.get("components"), dict)
            else 0,
            "price_per_million": ranked.get("completion_usd_per_million", 0),
            "value_index": (ranked.get("components") or {}).get("value_general", 0)
            if isinstance(ranked.get("components"), dict)
            else 0,
        }
    )


def _reconcile_value_selection(path: Path, judge: SelectedJudge) -> None:
    value_path = path.parent / "value-selection.json"
    if not value_path.exists():
        return
    data = json.loads(value_path.read_text(encoding="utf-8"))
    rows = data.get("selected") if isinstance(data.get("selected"), list) else []
    original = None
    for row in rows:
        if isinstance(row, dict) and row.get("seat") == "judge":
            original = row.get("model")
            row["model"] = judge.model_id
            row["selection_override"] = "history_reliability"
            break
    if original and original != judge.model_id:
        data["judge_history_reliability_override"] = {
            "original_model": original,
            "runtime_model": judge.model_id,
            "reason": judge.selection_reason,
        }
    value_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _reconcile_candidate_flags(
    path: Path,
    experts: Sequence[SelectedExpert],
    judge: SelectedJudge,
    policy_evidence: dict,
) -> None:
    data = json.loads(path.read_text(encoding="utf-8"))
    expected = {expert.seat_key: expert.model_id for expert in experts}
    expected["judge"] = judge.model_id
    for seat, model_id in expected.items():
        _override_candidate(data, seat, model_id)
    data["capability_seat_policy"] = {**policy_evidence, "candidate_audit_consistent": True}
    data["runtime_selected_models"] = expected
    data["judge_history_reliability_override"] = any(
        isinstance(row, dict)
        and row.get("selected") is True
        and row.get("rule_rank") == "history_reliability_override"
        for row in (data.get("seat_candidates", {}).get("judge", []) if isinstance(data.get("seat_candidates"), dict) else [])
    )
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _reconcile_value_selection(path, judge)


def write_selection_artifacts(
    writer: Callable,
    run: RunConfig,
    profile: TaskProfile,
    source: str,
    ranked: Sequence[ModelInfo],
    experts: Sequence[SelectedExpert],
    judge: SelectedJudge,
    estimated: float,
    required_capabilities: Sequence[str] = (),
) -> None:
    """Write evidence under the exact same policy used for actual selection."""
    with _policy_context(profile, required_capabilities) as policy_evidence:
        writer(run, profile, source, ranked, experts, judge, estimated)
        _reconcile_candidate_flags(run.output_dir / "model-selection.json", experts, judge, policy_evidence)


def _install_direct_calls_writer_wrapper() -> None:
    """Patch before expert_team imports the writer from direct_calls."""
    import direct_calls

    original = direct_calls.write_selection_artifacts
    if getattr(original, "_capability_policy_bound", False):
        return

    def bound_writer(run, profile, source, ranked, experts, judge, estimated):
        return write_selection_artifacts(
            original,
            run,
            profile,
            source,
            ranked,
            experts,
            judge,
            estimated,
            _LAST_REQUIRED_CAPABILITIES,
        )

    bound_writer._capability_policy_bound = True
    direct_calls.write_selection_artifacts = bound_writer


_install_direct_calls_writer_wrapper()
