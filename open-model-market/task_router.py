"""Conditional semantic task routing with deterministic fallback and audited budgets.

The router may refine only task metadata (domain, complexity, risk, capabilities).
It is forbidden from answering the substantive user question or selecting model IDs.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from artifact_manifest import write_manifest
from direct_calls import call_model
from model_market import (
    ExpertTeamError,
    ModelInfo,
    RunConfig,
    TaskProfile,
    estimate_call_cost,
    load_json,
)
from response_audit import diagnostics, extract_answer, sanitized

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
OPERATIONAL_METADATA_RE = re.compile(
    r"\b(?:github|openrouter|issue\s*runner|github\s*actions?|artifact)\b|"
    r"GitHub专家团|GitHub执行|执行票据|工作流|运行编号|模型网关|委托边界",
    re.IGNORECASE,
)
SMALL_MODEL_RE = re.compile(r"(?:^|[-_/])(?:0\.\d+|[1-8])b(?:[-_/]|$)", re.IGNORECASE)
UNSTABLE_TERMS = ("preview", "experimental", "alpha", "beta", "spark", ":free")
MAX_ROUTER_OUTPUT_CHARS = 12_000
MIN_TOTAL_MODEL_CALLS = 4
MAX_TOTAL_MODEL_CALLS = 6


@dataclass(frozen=True)
class RoutingConfig:
    enabled: bool
    confidence_threshold: float
    high_stakes_confidence_threshold: float
    max_completion_tokens: int
    max_budget_share: float
    max_intelligence_rank: int


@dataclass(frozen=True)
class RoutingOutcome:
    profile: TaskProfile
    deterministic_confidence: float
    trigger_reasons: List[str]
    attempted: bool
    semantic_profile_used: bool
    call_consumed: bool
    model_id: str
    estimated_cost_usd: float
    actual_cost_usd: float
    budget_reservation_usd: float
    status: str
    error: str
    response_diagnostics: Dict[str, Any]
    response: Dict[str, Any]
    required_capabilities: List[str]
    semantic_confidence: float | None

    def evidence(self) -> Dict[str, Any]:
        data = asdict(self)
        data["profile"] = asdict(self.profile)
        return data


def load_routing_config(config_path: Path) -> RoutingConfig:
    cfg = load_json(config_path)
    raw = cfg.get("routing", {})
    if not isinstance(raw, dict):
        raise ExpertTeamError("routing must be a JSON object.")
    threshold = float(raw.get("confidence_threshold", 0.68))
    high_threshold = float(raw.get("high_stakes_confidence_threshold", 0.82))
    max_tokens = int(raw.get("max_completion_tokens", 900))
    max_share = float(raw.get("max_budget_share", 0.20))
    max_rank = int(raw.get("max_intelligence_rank", 30))
    if not 0.0 <= threshold <= 1.0:
        raise ExpertTeamError("routing.confidence_threshold must be between 0 and 1.")
    if not 0.0 <= high_threshold <= 1.0:
        raise ExpertTeamError("routing.high_stakes_confidence_threshold must be between 0 and 1.")
    if not 256 <= max_tokens <= 4096:
        raise ExpertTeamError("routing.max_completion_tokens must be between 256 and 4096.")
    if not 0.01 <= max_share <= 0.50:
        raise ExpertTeamError("routing.max_budget_share must be between 0.01 and 0.50.")
    if not 5 <= max_rank <= 50:
        raise ExpertTeamError("routing.max_intelligence_rank must be between 5 and 50.")
    return RoutingConfig(
        enabled=bool(raw.get("enabled", True)),
        confidence_threshold=threshold,
        high_stakes_confidence_threshold=high_threshold,
        max_completion_tokens=max_tokens,
        max_budget_share=max_share,
        max_intelligence_rank=max_rank,
    )


def total_model_calls_from_env(run: RunConfig, environ: Mapping[str, str]) -> int:
    raw = str(environ.get("TOTAL_MODEL_CALLS") or "").strip()
    if raw:
        try:
            calls = int(raw)
        except ValueError as exc:
            raise ExpertTeamError("TOTAL_MODEL_CALLS must be an integer.") from exc
    else:
        calls = MIN_TOTAL_MODEL_CALLS + max(0, min(2, int(run.maximum_replacements)))
    if not MIN_TOTAL_MODEL_CALLS <= calls <= MAX_TOTAL_MODEL_CALLS:
        raise ExpertTeamError(
            f"TOTAL_MODEL_CALLS must be between {MIN_TOTAL_MODEL_CALLS} and {MAX_TOTAL_MODEL_CALLS}."
        )
    return calls


def _semantic_text(task: str) -> str:
    text = URL_RE.sub(" ", task)
    text = OPERATIONAL_METADATA_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def deterministic_confidence(task: str, profile: TaskProfile, policy: Mapping[str, Any]) -> tuple[float, List[str]]:
    text = _semantic_text(task)
    keywords = policy.get("keywords") if isinstance(policy, Mapping) else None
    if not isinstance(keywords, Mapping):
        return 0.20, ["policy_keywords_missing"]
    scored: List[tuple[str, int]] = []
    for domain, terms in keywords.items():
        if not isinstance(terms, list):
            continue
        hits = sum(1 for term in terms if str(term).lower() in text)
        if hits:
            scored.append((str(domain), hits))
    scored.sort(key=lambda item: (-item[1], item[0]))
    reasons: List[str] = []
    if not scored:
        confidence = 0.20
        reasons.append("no_domain_keyword_hit")
    else:
        top = scored[0][1]
        second = scored[1][1] if len(scored) > 1 else 0
        if top >= 4 and top - second >= 2:
            confidence = 0.92
        elif top >= 3 and top > second:
            confidence = 0.82
        elif top >= 2 and top > second:
            confidence = 0.70
        else:
            confidence = 0.48
        reasons.append(f"top_domain={scored[0][0]}:{top}")
        reasons.append(f"second_score={second}")
        reasons.append(f"matched_domains={len(scored)}")
        if len(scored) >= 3:
            confidence -= 0.12
            reasons.append("multi_domain_ambiguity")
        if len(scored) >= 2 and scored[0][1] == scored[1][1]:
            confidence -= 0.10
            reasons.append("top_domain_tie")
    if profile.complexity == "complex" and len(profile.domains) >= 2:
        confidence -= 0.08
        reasons.append("complex_cross_domain")
    if profile.high_stakes and len(profile.domains) >= 2:
        confidence = min(confidence, 0.62)
        reasons.append("high_stakes_cross_domain_cap")
    return max(0.05, min(0.97, confidence)), reasons


def _should_route(profile: TaskProfile, confidence: float, config: RoutingConfig) -> tuple[bool, List[str]]:
    reasons: List[str] = []
    threshold = config.high_stakes_confidence_threshold if profile.high_stakes else config.confidence_threshold
    if confidence < threshold:
        reasons.append(f"confidence={confidence:.3f}<threshold={threshold:.3f}")
    if profile.complexity == "complex" and len(profile.domains) >= 3:
        reasons.append("complex_task_has_three_or_more_domains")
    return bool(reasons), reasons


def _model_text(model: ModelInfo) -> str:
    return f"{model.id} {model.name} {model.description}".lower()


def _router_candidates(ranked: Sequence[ModelInfo], config: RoutingConfig) -> List[ModelInfo]:
    eligible = []
    for model in ranked:
        rank = model.ranks.get("intelligence-high-to-low")
        if rank is None or rank > config.max_intelligence_rank:
            continue
        text = _model_text(model)
        if any(term in text for term in UNSTABLE_TERMS):
            continue
        if SMALL_MODEL_RE.search(f"{model.id} {model.name}"):
            continue
        price = model.blended_price_per_million
        if price is None or not math.isfinite(price):
            continue
        capability = max(1.0, 100.0 * (51 - rank) / 50.0)
        value = capability / max(price, 0.25)
        structured = int("structured_outputs" in model.supported_parameters)
        reasoning = int("reasoning" in model.supported_parameters)
        eligible.append((model, structured, reasoning, value, capability, price))
    eligible.sort(key=lambda item: (-item[1], -item[2], -item[3], -item[4], item[5], item[0].id))
    return [item[0] for item in eligible]


def _build_payload(
    run: RunConfig,
    model: ModelInfo,
    profile: TaskProfile,
    policy: Mapping[str, Any],
    max_tokens: int,
) -> Dict[str, Any]:
    domains = sorted(list((policy.get("keywords") or {}).keys()) + ["general"])
    system = (
        "你是任务语义路由器，不是任务分析专家。"
        "你只能判断任务所属领域、复杂度、风险和所需能力；禁止回答原问题、禁止评价证据真假、禁止提出方案或结论、禁止选择或提及任何模型。"
        "禁止调用或假装调用网页、工具、文件、API、插件和其他模型。"
        "只输出一个JSON对象，不要Markdown，不要解释。"
        "允许字段仅为primary_domain、secondary_domains、complexity、high_stakes、required_capabilities、confidence、reason。"
    )
    user = json.dumps(
        {
            "allowed_domains": domains,
            "deterministic_profile": asdict(profile),
            "task": run.task,
            "output_contract": {
                "primary_domain": "one allowed domain",
                "secondary_domains": "array of 0-3 distinct allowed domains",
                "complexity": "simple|medium|complex",
                "high_stakes": "boolean",
                "required_capabilities": "array of 1-8 short capability labels; no model names",
                "confidence": "number from 0 to 1",
                "reason": "routing-only explanation under 300 Chinese characters",
            },
        },
        ensure_ascii=False,
    )
    payload: Dict[str, Any] = {
        "model": model.id,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "provider": run.provider,
    }
    supported = set(model.supported_parameters)
    if "max_tokens" in supported:
        payload["max_tokens"] = min(max_tokens, model.max_completion_tokens)
    elif "max_completion_tokens" in supported:
        payload["max_completion_tokens"] = min(max_tokens, model.max_completion_tokens)
    if "temperature" in supported:
        payload["temperature"] = 0
    if "reasoning" in supported:
        payload["reasoning"] = {"effort": "low", "exclude": True}
    if "verbosity" in supported:
        payload["verbosity"] = "low"
    if "response_format" in supported:
        payload["response_format"] = {"type": "json_object"}
    forbidden = {"tools", "tool_choice", "plugins", "web_search_options", "file_search", "models"}
    present = sorted(forbidden.intersection(payload))
    if present:
        raise ExpertTeamError(f"Forbidden external-tool fields in routing request: {present}")
    if str(payload.get("model") or "").startswith("openrouter/") or ":online" in str(payload.get("model") or ""):
        raise ExpertTeamError("Router/online model is forbidden for semantic routing.")
    return payload


def _json_object(answer: str) -> Dict[str, Any]:
    text = answer.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ExpertTeamError("Semantic router returned no JSON object.")
    if end - start + 1 > MAX_ROUTER_OUTPUT_CHARS:
        raise ExpertTeamError("Semantic router JSON exceeds the allowed size.")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ExpertTeamError("Semantic router output must be one JSON object.")
    return data


def _validate_semantic_profile(data: Mapping[str, Any], policy: Mapping[str, Any]) -> Dict[str, Any]:
    allowed_fields = {
        "primary_domain",
        "secondary_domains",
        "complexity",
        "high_stakes",
        "required_capabilities",
        "confidence",
        "reason",
    }
    unknown = sorted(set(data) - allowed_fields)
    if unknown:
        raise ExpertTeamError(f"Semantic router returned forbidden fields: {unknown}")
    allowed_domains = set((policy.get("keywords") or {}).keys()) | {"general"}
    primary = str(data.get("primary_domain") or "").strip()
    if primary not in allowed_domains:
        raise ExpertTeamError("Semantic router primary_domain is invalid.")
    secondaries_raw = data.get("secondary_domains") or []
    if not isinstance(secondaries_raw, list) or len(secondaries_raw) > 3:
        raise ExpertTeamError("Semantic router secondary_domains must contain at most 3 entries.")
    secondaries: List[str] = []
    for value in secondaries_raw:
        domain = str(value or "").strip()
        if domain not in allowed_domains:
            raise ExpertTeamError("Semantic router secondary domain is invalid.")
        if domain != primary and domain not in secondaries:
            secondaries.append(domain)
    complexity = str(data.get("complexity") or "")
    if complexity not in {"simple", "medium", "complex"}:
        raise ExpertTeamError("Semantic router complexity is invalid.")
    if not isinstance(data.get("high_stakes"), bool):
        raise ExpertTeamError("Semantic router high_stakes must be boolean.")
    capabilities_raw = data.get("required_capabilities") or []
    if not isinstance(capabilities_raw, list) or not 1 <= len(capabilities_raw) <= 8:
        raise ExpertTeamError("Semantic router required_capabilities must contain 1-8 entries.")
    capabilities: List[str] = []
    for value in capabilities_raw:
        text = str(value or "").strip()
        if not text or len(text) > 120:
            raise ExpertTeamError("Semantic router capability labels must be 1-120 characters.")
        lowered = text.lower()
        if "/" in text or "model" in lowered or "模型" in text or "gpt" in lowered or "claude" in lowered:
            raise ExpertTeamError("Semantic router must not choose or name models.")
        capabilities.append(text)
    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ExpertTeamError("Semantic router confidence must be numeric.") from exc
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ExpertTeamError("Semantic router confidence must be between 0 and 1.")
    reason = str(data.get("reason") or "").strip()
    if len(reason) > 600:
        raise ExpertTeamError("Semantic router reason exceeds 600 characters.")
    return {
        "primary_domain": primary,
        "secondary_domains": secondaries,
        "complexity": complexity,
        "high_stakes": bool(data["high_stakes"]),
        "required_capabilities": capabilities,
        "confidence": confidence,
        "reason": reason,
    }


def _refine_profile(initial: TaskProfile, semantic: Mapping[str, Any]) -> TaskProfile:
    order = {"simple": 0, "medium": 1, "complex": 2}
    semantic_complexity = str(semantic["complexity"])
    complexity = max((initial.complexity, semantic_complexity), key=lambda value: order[value])
    minimum_score = {"simple": 0, "medium": 2, "complex": 4}[complexity]
    primary = str(semantic["primary_domain"])
    secondaries = list(semantic["secondary_domains"])
    domains = [primary] + [domain for domain in secondaries if domain != primary]
    secondary = secondaries[0] if secondaries else primary
    return replace(
        initial,
        domains=domains,
        primary_domain=primary,
        secondary_domain=secondary,
        complexity=complexity,
        complexity_score=max(initial.complexity_score, minimum_score),
        high_stakes=bool(initial.high_stakes or semantic["high_stakes"]),
    )


def route_task(
    run: RunConfig,
    initial_profile: TaskProfile,
    ranked: Sequence[ModelInfo],
    policy: Mapping[str, Any],
    config: RoutingConfig,
    total_model_calls: int,
) -> RoutingOutcome:
    confidence, confidence_reasons = deterministic_confidence(run.task, initial_profile, policy)
    should_route, trigger_reasons = _should_route(initial_profile, confidence, config)
    trigger_reasons = confidence_reasons + trigger_reasons
    extra_calls = max(0, total_model_calls - MIN_TOTAL_MODEL_CALLS)
    base = dict(
        profile=initial_profile,
        deterministic_confidence=confidence,
        trigger_reasons=trigger_reasons,
        attempted=False,
        semantic_profile_used=False,
        call_consumed=False,
        model_id="",
        estimated_cost_usd=0.0,
        actual_cost_usd=0.0,
        budget_reservation_usd=0.0,
        error="",
        response_diagnostics={},
        response={},
        required_capabilities=[],
        semantic_confidence=None,
    )
    if not config.enabled:
        return RoutingOutcome(status="disabled", **base)
    if not should_route:
        return RoutingOutcome(status="deterministic_confident", **base)
    if extra_calls < 1:
        return RoutingOutcome(status="skipped_no_call_budget", **base)
    if run.dry_run:
        return RoutingOutcome(status="dry_run_would_route", **base)

    candidates = _router_candidates(ranked, config)
    if not candidates:
        return RoutingOutcome(status="skipped_no_router_candidate", error="No eligible router model.", **{k:v for k,v in base.items() if k != "error"})
    model = candidates[0]
    estimated = estimate_call_cost(model, len(run.task) + 3000, config.max_completion_tokens)
    reservation = estimated * run.budget_safety_factor
    if run.max_estimated_cost_usd is not None:
        if reservation > run.max_estimated_cost_usd * config.max_budget_share:
            return RoutingOutcome(
                status="skipped_router_budget_share",
                model_id=model.id,
                estimated_cost_usd=estimated,
                error="Router estimate exceeds configured budget share.",
                **{k:v for k,v in base.items() if k not in {"model_id", "estimated_cost_usd", "error"}},
            )
        if reservation >= run.max_estimated_cost_usd:
            return RoutingOutcome(
                status="skipped_insufficient_total_budget",
                model_id=model.id,
                estimated_cost_usd=estimated,
                error="Router estimate leaves no budget for the fixed 3+1 team.",
                **{k:v for k,v in base.items() if k not in {"model_id", "estimated_cost_usd", "error"}},
            )

    payload = _build_payload(run, model, initial_profile, policy, config.max_completion_tokens)
    response: Dict[str, Any] = {}
    info: Dict[str, Any] = {}
    actual = 0.0
    try:
        response, _latency = call_model(run, payload)
        info = diagnostics(response)
        actual = float(info.get("cost") or 0.0)
        answer = extract_answer(response)
        semantic = _validate_semantic_profile(_json_object(answer), policy)
        refined = _refine_profile(initial_profile, semantic)
        reservation = max(estimated, actual) * run.budget_safety_factor
        return RoutingOutcome(
            profile=refined,
            deterministic_confidence=confidence,
            trigger_reasons=trigger_reasons,
            attempted=True,
            semantic_profile_used=True,
            call_consumed=True,
            model_id=model.id,
            estimated_cost_usd=estimated,
            actual_cost_usd=actual,
            budget_reservation_usd=reservation,
            status="semantic_success",
            error="",
            response_diagnostics=info,
            response=sanitized(response),
            required_capabilities=list(semantic["required_capabilities"]),
            semantic_confidence=float(semantic["confidence"]),
        )
    except Exception as exc:  # noqa: BLE001 - audited deterministic fallback
        reservation = max(estimated, actual) * run.budget_safety_factor
        return RoutingOutcome(
            profile=initial_profile,
            deterministic_confidence=confidence,
            trigger_reasons=trigger_reasons,
            attempted=True,
            semantic_profile_used=False,
            call_consumed=True,
            model_id=model.id,
            estimated_cost_usd=estimated,
            actual_cost_usd=actual,
            budget_reservation_usd=reservation,
            status="semantic_failed_deterministic_fallback",
            error=str(exc),
            response_diagnostics=info,
            response=sanitized(response) if response else {},
            required_capabilities=[],
            semantic_confidence=None,
        )


def execution_run_after_routing(
    run: RunConfig,
    outcome: RoutingOutcome,
    total_model_calls: int,
) -> RunConfig:
    replacements = max(0, min(2, total_model_calls - MIN_TOTAL_MODEL_CALLS - int(outcome.call_consumed)))
    remaining = run.max_estimated_cost_usd
    if remaining is not None:
        remaining -= outcome.budget_reservation_usd
        if remaining <= 0:
            raise ExpertTeamError("Semantic routing consumed the entire approved cost budget.")
    return replace(run, maximum_replacements=replacements, max_estimated_cost_usd=remaining)


def write_routing_artifact(output_dir: Path, outcome: RoutingOutcome) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "task-routing.json").write_text(
        json.dumps(outcome.evidence(), ensure_ascii=False, indent=2), encoding="utf-8"
    )


def annotate_selection_artifacts(
    output_dir: Path,
    outcome: RoutingOutcome,
    *,
    total_model_calls: int,
    original_cost_limit: float | None,
    remaining_team_cost_limit: float | None,
    team_estimated_cost: float,
    maximum_replacements: int,
) -> None:
    selection_path = output_dir / "model-selection.json"
    if selection_path.exists():
        data = json.loads(selection_path.read_text(encoding="utf-8"))
        data["task_routing"] = outcome.evidence()
        data["approved_total_model_calls"] = total_model_calls
        data["model_call_allocation"] = {
            "semantic_router_calls": int(outcome.call_consumed),
            "fixed_expert_calls": 3,
            "fixed_judge_calls": 1,
            "maximum_replacement_calls": maximum_replacements,
        }
        data["original_hard_cost_limit_usd"] = original_cost_limit
        data["remaining_team_cost_limit_usd"] = remaining_team_cost_limit
        data["estimated_team_cost_usd"] = round(team_estimated_cost, 6)
        data["estimated_total_cost_usd"] = round(team_estimated_cost + outcome.estimated_cost_usd, 6)
        selection_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    ranking_path = output_dir / "model-ranking.md"
    if ranking_path.exists():
        text = ranking_path.read_text(encoding="utf-8")
        marker = "- Fixed combination: `核心主研席 + 交叉验证席 + 独立反证席 -> 综合裁决席`\n"
        addition = (
            marker
            + f"- Task routing: `{outcome.status}`\n"
            + f"- Semantic router model: `{outcome.model_id or 'not-used'}`\n"
            + f"- Approved total calls: `{total_model_calls}`\n"
            + f"- Remaining replacement calls: `{maximum_replacements}`\n"
        )
        if marker in text:
            text = text.replace(marker, addition, 1)
        ranking_path.write_text(text, encoding="utf-8")

    dry_path = output_dir / "expert-team-dry-run.json"
    if dry_path.exists():
        dry = json.loads(dry_path.read_text(encoding="utf-8"))
        dry["task_routing"] = outcome.evidence()
        dry["approved_total_model_calls"] = total_model_calls
        dry["maximum_replacement_calls"] = maximum_replacements
        dry_path.write_text(json.dumps(dry, ensure_ascii=False, indent=2), encoding="utf-8")
    write_manifest(output_dir)


def finalize_run_artifacts(
    output_dir: Path,
    outcome: RoutingOutcome,
    *,
    total_model_calls: int,
    original_cost_limit: float | None,
    maximum_replacements: int,
) -> None:
    result_path = output_dir / "expert-team-result.json"
    if not result_path.exists():
        write_manifest(output_dir)
        return
    result = json.loads(result_path.read_text(encoding="utf-8"))
    team_actual = float(result.get("actual_cost_usd") or 0.0)
    team_estimated = float(result.get("estimated_cost_usd") or 0.0)
    result["task_routing"] = outcome.evidence()
    result["approved_total_model_calls"] = total_model_calls
    result["maximum_replacement_calls"] = maximum_replacements
    result["original_hard_cost_limit_usd"] = original_cost_limit
    result["team_actual_cost_usd"] = round(team_actual, 6)
    result["routing_actual_cost_usd"] = round(outcome.actual_cost_usd, 6)
    result["actual_cost_usd"] = round(team_actual + outcome.actual_cost_usd, 6)
    result["team_estimated_cost_usd"] = round(team_estimated, 6)
    result["routing_estimated_cost_usd"] = round(outcome.estimated_cost_usd, 6)
    result["estimated_cost_usd"] = round(team_estimated + outcome.estimated_cost_usd, 6)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    report_path = output_dir / "expert-team-report.md"
    if report_path.exists():
        report = report_path.read_text(encoding="utf-8")
        report = re.sub(
            r"- Estimated cost: `\$[0-9.]+`",
            f"- Estimated cost: `${result['estimated_cost_usd']:.6f}`",
            report,
            count=1,
        )
        report = re.sub(
            r"- Actual reported cost: `\$[0-9.]+`",
            f"- Actual reported cost: `${result['actual_cost_usd']:.6f}`",
            report,
            count=1,
        )
        combination = "- Combination: `核心主研席 + 交叉验证席 + 独立反证席 -> 综合裁决席`\n"
        routing_lines = (
            combination
            + f"- Task routing: `{outcome.status}`\n"
            + f"- Semantic router model: `{outcome.model_id or 'not-used'}`\n"
            + f"- Approved total model calls: `{total_model_calls}`\n"
            + f"- Maximum replacement calls after routing: `{maximum_replacements}`\n"
        )
        if combination in report:
            report = report.replace(combination, routing_lines, 1)
        report_path.write_text(report, encoding="utf-8")
    write_manifest(output_dir)
