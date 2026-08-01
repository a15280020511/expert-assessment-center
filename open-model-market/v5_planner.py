"""V5 market compiler, candidate graph factory, Pareto pruning, and CP-SAT optimizer."""
from __future__ import annotations

import json
import math
import statistics
import urllib.parse
from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Any, Iterable, Mapping, Sequence

import networkx as nx
from ortools.sat.python import cp_model

from execution_graph import ExecutionGraph, GraphLimits, SelectedEdge, SelectedNode
from execution_graph_validator import validate_execution_graph
from openrouter_api import OpenRouterRequestError, request_json
import v5_task_delivery_contract as task_delivery_contract

ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints"
FORBIDDEN_MODEL_TERMS = ("openrouter/", ":online", ":batch", ":free", "preview")
FORBIDDEN_REQUEST_FIELDS = {
    "tools", "tool_choice", "plugins", "web_search", "web_search_options",
    "file_search", "browser", "code_interpreter", "models",
}
CAPABILITY_TERMS: Mapping[str, tuple[str, ...]] = {
    "general_analysis": ("analysis", "reasoning", "research", "general"),
    "complex_reasoning": ("reasoning", "complex", "logic", "research"),
    "quantitative_reasoning": ("math", "mathematics", "quantitative", "calculation"),
    "statistics": ("statistics", "probability", "data science", "forecast"),
    "causal_reasoning": ("causal", "mechanism", "counterfactual", "reasoning"),
    "forecasting": ("forecast", "prediction", "scenario", "time series"),
    "evidence_validation": ("evidence", "research", "verification", "citation"),
    "counterfactual_analysis": ("counterfactual", "scenario", "causal"),
    "decision_comparison": ("decision", "strategy", "comparison", "business"),
    "adversarial_reasoning": ("adversarial", "security", "risk", "audit"),
    "risk_discovery": ("risk", "security", "safety", "audit", "legal"),
    "implementation": ("coding", "software", "implementation", "engineering"),
    "creative_generation": ("creative", "writing", "design", "story"),
    "long_context": ("long context", "documents", "research"),
    "structured_output": ("structured", "json", "schema"),
    "synthesis": ("synthesis", "judge", "reasoning", "research"),
    "delivery": ("instruction", "assistant", "writing", "analysis"),
}
DOMAIN_TERMS: Mapping[str, tuple[str, ...]] = {
    "business": ("business", "finance", "economics", "investment", "market"),
    "legal": ("legal", "law", "compliance", "regulation"),
    "public_policy": ("policy", "government", "governance", "public"),
    "coding": ("coding", "software", "repository", "engineering"),
    "math": ("math", "mathematics", "quantitative", "statistics"),
    "research": ("research", "evidence", "science", "documents"),
    "security": ("security", "cyber", "risk", "adversarial"),
    "medical": ("medical", "clinical", "health", "medicine"),
    "international_relations": ("geopolit", "diplomacy", "war", "sanction"),
    "supply_chain": ("supply chain", "logistics", "operations", "procurement"),
    "creative": ("creative", "writing", "design", "story"),
    "general": ("general", "assistant", "analysis", "reasoning"),
}


class V5PlanningError(RuntimeError):
    pass


@dataclass(frozen=True)
class MarketEndpoint:
    endpoint_id: str
    model_id: str
    provider_slug: str
    provider_endpoint: str
    author: str
    context_length: int
    max_completion_tokens: int
    prompt_price_per_million: float
    completion_price_per_million: float
    supported_parameters: tuple[str, ...]
    input_modalities: tuple[str, ...]
    output_modalities: tuple[str, ...]
    capability_scores: Mapping[str, float]
    benchmark_score: float
    benchmark_confidence: float
    reliability: float
    synthetic_fixture_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class CandidateNode:
    candidate_id: str
    interpretation_id: str
    coverage_keys: tuple[str, ...]
    assigned_work: tuple[str, ...]
    copy_indices: tuple[int, ...]
    professional_capabilities: Mapping[str, float]
    functions: tuple[str, ...]
    prompt_profile: Mapping[str, Any]
    reasoning_profile: Mapping[str, Any]
    parameter_profile: Mapping[str, Any]
    model: str
    provider_endpoint: str
    provider_slug: str
    output_contract: Mapping[str, Any]
    estimated_quality: float
    quality_uncertainty: float
    estimated_cost: float
    failure_probability: float
    request_config: Mapping[str, Any]
    independence_groups: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _digest(prefix: str, value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return f"{prefix}-{sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _ppm(value: Any, fallback: float = 0.0) -> float:
    number = _float(value, fallback)
    if number < 0:
        return fallback
    return number * 1_000_000 if number < 0.1 else number


def _stable_model_id(model_id: str) -> bool:
    folded = model_id.casefold()
    return bool(model_id and "/" in model_id and not any(term in folded for term in FORBIDDEN_MODEL_TERMS))


def _endpoint_url(model_id: str) -> str:
    author, slug = model_id.split("/", 1)
    return ENDPOINTS_URL.format(author=urllib.parse.quote(author, safe=""), slug=urllib.parse.quote(slug, safe=""))


def fetch_live_endpoint_payloads(
    ranked: Sequence[Any],
    run: Any,
    *,
    maximum_models: int | None = None,
) -> dict[str, Mapping[str, Any]]:
    """Fetch actual model endpoint inventories after phase A has completed."""
    if not getattr(run, "api_key", None):
        raise V5PlanningError("OPENROUTER_API_KEY is required to compile real provider endpoints.")
    payloads: dict[str, Mapping[str, Any]] = {}
    eligible = [model for model in ranked if _stable_model_id(str(getattr(model, "id", "")))]
    if maximum_models is not None:
        eligible = eligible[: max(1, int(maximum_models))]
    for model in eligible:
        model_id = str(model.id)
        try:
            payloads[model_id] = request_json(
                _endpoint_url(model_id),
                run.api_key,
                int(getattr(run, "catalog_timeout_seconds", 30)),
                int(getattr(run, "catalog_max_retries", 1)),
            )
        except OpenRouterRequestError as exc:
            payloads[model_id] = {"error": str(exc), "data": {"endpoints": []}}
    return payloads


def _rank_quality(model: Any, ranking_limit: int) -> float:
    ranks = dict(getattr(model, "ranks", {}) or {})
    intelligence = int(ranks.get("intelligence-high-to-low", ranking_limit) or ranking_limit)
    return _clamp(1.0 - max(0, intelligence - 1) / max(1, ranking_limit))


def _term_score(text: str, terms: Iterable[str]) -> float:
    terms = tuple(dict.fromkeys(str(term) for term in terms if str(term)))
    if not terms:
        return 0.5
    hits = sum(1 for term in terms if term in text)
    return _clamp((hits + 1.0) / (len(terms) + 2.0))


def _geometric_mean(values: Sequence[float]) -> float:
    bounded = [max(1e-9, _clamp(value)) for value in values]
    if not bounded:
        return 0.0
    return math.exp(sum(math.log(value) for value in bounded) / len(bounded))


def _capability_vector(model: Any, labels: Sequence[str], ranking_limit: int) -> dict[str, float]:
    description = " ".join(
        str(value or "")
        for value in (getattr(model, "name", ""), getattr(model, "description", ""), getattr(model, "id", ""))
    ).casefold()
    supported = {str(x).casefold() for x in (getattr(model, "supported_parameters", []) or [])}
    quality = _rank_quality(model, ranking_limit)
    context = int(getattr(model, "context_length", 0) or 0)
    output = int(getattr(model, "max_completion_tokens", 0) or 0)
    scores: dict[str, float] = {}
    for label in labels:
        if label.startswith("domain:"):
            domain = label.split(":", 1)[1]
            base = _term_score(description, DOMAIN_TERMS.get(domain, (domain.replace("_", " "),)))
        else:
            base = _term_score(description, CAPABILITY_TERMS.get(label, (label.replace("_", " "),)))
        score = _geometric_mean((quality, base))
        if label in {"complex_reasoning", "causal_reasoning", "counterfactual_analysis", "synthesis"}:
            if "reasoning" in supported or bool(getattr(model, "reasoning", {})):
                score += 0.13
        if label == "structured_output" and supported & {"structured_outputs", "response_format", "json_schema"}:
            score = max(score, 0.90)
        if label == "long_context":
            score = max(score, _clamp(math.log2(max(4096, context) / 4096) / 7.0))
        if label == "delivery" and output >= 8000:
            score += 0.08
        scores[label] = round(_clamp(score), 6)
    return scores


def _provider_slug(endpoint: Mapping[str, Any]) -> str:
    for key in ("tag", "provider_slug", "provider", "name", "provider_name"):
        value = str(endpoint.get(key) or "").strip()
        if value:
            return value
    return ""


def _endpoint_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if isinstance(data, Mapping) and isinstance(data.get("endpoints"), list):
        return [row for row in data["endpoints"] if isinstance(row, Mapping)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    if isinstance(payload.get("endpoints"), list):
        return [row for row in payload["endpoints"] if isinstance(row, Mapping)]
    return []


def compile_model_endpoint_market(
    ranked: Sequence[Any],
    resource_bundle: Mapping[str, Any],
    *,
    endpoint_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    ranking_limit: int = 50,
    allow_synthetic_fixture: bool = False,
) -> dict[str, Any]:
    """Compile model x real provider endpoint rows required by the task matrices."""
    matrices = resource_bundle.get("resource_matrices", {}).get("matrices", [])
    labels = sorted({str(label) for matrix in matrices for label in matrix.get("capability_labels", [])})
    payloads = dict(endpoint_payloads or {})
    rows: list[MarketEndpoint] = []
    rejected: list[dict[str, str]] = []
    for model in ranked[: max(1, int(ranking_limit))]:
        model_id = str(getattr(model, "id", ""))
        if not _stable_model_id(model_id):
            rejected.append({"model": model_id, "reason": "unstable-or-routed-model"})
            continue
        capabilities = _capability_vector(model, labels, ranking_limit)
        endpoints = _endpoint_rows(payloads.get(model_id, {}))
        synthetic = False
        if not endpoints and allow_synthetic_fixture:
            endpoints = [{
                "tag": f"fixture/{str(getattr(model, 'author', '') or model_id.split('/', 1)[0])}",
                "context_length": getattr(model, "context_length", 0),
                "max_completion_tokens": getattr(model, "max_completion_tokens", 0),
                "pricing": {
                    "prompt": getattr(model, "prompt_price_per_million", 0.0),
                    "completion": getattr(model, "completion_price_per_million", 0.0),
                },
                "supported_parameters": list(getattr(model, "supported_parameters", []) or []),
                "synthetic_fixture_only": True,
            }]
            synthetic = True
        if not endpoints:
            rejected.append({"model": model_id, "reason": "no-real-provider-endpoint"})
            continue
        for endpoint in endpoints:
            slug = _provider_slug(endpoint)
            if not slug:
                rejected.append({"model": model_id, "reason": "endpoint-missing-provider-slug"})
                continue
            pricing = endpoint.get("pricing") if isinstance(endpoint.get("pricing"), Mapping) else {}
            prompt = _ppm(pricing.get("prompt"), _float(getattr(model, "prompt_price_per_million", 0.0)))
            completion = _ppm(pricing.get("completion"), _float(getattr(model, "completion_price_per_million", 0.0)))
            supported = endpoint.get("supported_parameters") or getattr(model, "supported_parameters", []) or []
            context = int(endpoint.get("context_length") or getattr(model, "context_length", 0) or 0)
            max_output = int(endpoint.get("max_completion_tokens") or getattr(model, "max_completion_tokens", 0) or 0)
            reliability = _clamp(_float(endpoint.get("uptime_last_30m"), _float(endpoint.get("uptime"), 0.97)))
            benchmark = _rank_quality(model, ranking_limit)
            confidence = _clamp(0.70 + 0.25 * reliability)
            endpoint_id = _digest("endpoint", [model_id, slug, prompt, completion, context, max_output])
            rows.append(MarketEndpoint(
                endpoint_id=endpoint_id,
                model_id=model_id,
                provider_slug=slug,
                provider_endpoint=f"{model_id}@{slug}",
                author=str(getattr(model, "author", "") or model_id.split("/", 1)[0]),
                context_length=context,
                max_completion_tokens=max_output,
                prompt_price_per_million=round(prompt, 8),
                completion_price_per_million=round(completion, 8),
                supported_parameters=tuple(sorted({str(x) for x in supported})),
                input_modalities=tuple(str(x) for x in (getattr(model, "input_modalities", []) or ["text"])),
                output_modalities=tuple(str(x) for x in (getattr(model, "output_modalities", []) or ["text"])),
                capability_scores=capabilities,
                benchmark_score=round(benchmark, 6),
                benchmark_confidence=round(confidence, 6),
                reliability=round(reliability, 6),
                synthetic_fixture_only=bool(endpoint.get("synthetic_fixture_only") or synthetic),
            ))
    rows.sort(key=lambda row: (-row.benchmark_score, row.prompt_price_per_million + row.completion_price_per_million, row.endpoint_id))
    if not rows:
        raise V5PlanningError("No usable model x provider endpoint rows were compiled.")
    return {
        "version": 5,
        "architecture": "model-id-times-real-provider-endpoint-market",
        "task_digest": resource_bundle.get("task_semantics", {}).get("task_digest"),
        "capability_labels": labels,
        "endpoints": [row.to_dict() for row in rows],
        "endpoint_count": len(rows),
        "real_endpoint_count": sum(not row.synthetic_fixture_only for row in rows),
        "synthetic_fixture_count": sum(row.synthetic_fixture_only for row in rows),
        "rejected": rejected,
        "phase_b_invariants": {
            "router_models_used": False,
            "online_models_used": False,
            "batch_models_used": False,
            "provider_endpoint_is_explicit": True,
        },
    }


def _work_map(resource_bundle: Mapping[str, Any], interpretation_id: str) -> dict[str, Mapping[str, Any]]:
    for interpretation in resource_bundle.get("task_semantics", {}).get("interpretations", []):
        if interpretation.get("interpretation_id") == interpretation_id:
            return {str(work["work_id"]): work for work in interpretation.get("atomic_work", [])}
    return {}


def _matrix_map(resource_bundle: Mapping[str, Any], interpretation_id: str) -> Mapping[str, Any]:
    for matrix in resource_bundle.get("resource_matrices", {}).get("matrices", []):
        if matrix.get("interpretation_id") == interpretation_id:
            return matrix
    raise V5PlanningError(f"Missing resource matrix for interpretation {interpretation_id}.")


def _graph_map(resource_bundle: Mapping[str, Any], interpretation_id: str) -> Mapping[str, Any]:
    for graph in resource_bundle.get("atomic_work_graphs", {}).get("graphs", []):
        if graph.get("interpretation_id") == interpretation_id:
            return graph
    raise V5PlanningError(f"Missing atomic graph for interpretation {interpretation_id}.")


def _capability_fit(demand: Mapping[str, float], capabilities: Mapping[str, float]) -> float:
    total = sum(max(0.0, float(value)) for value in demand.values())
    if total <= 0:
        return 0.5
    return _clamp(sum(float(weight) * float(capabilities.get(label, 0.0)) for label, weight in demand.items()) / total)


def _estimated_cost(endpoint: Mapping[str, Any], works: Sequence[Mapping[str, Any]], bundle_discount: float = 1.0) -> float:
    prompt_tokens = 0
    completion_tokens = 0
    for work in works:
        context = work.get("context_requirements", {})
        prompt_tokens += int(context.get("system_prompt_tokens", 0)) + int(context.get("original_task_tokens", 0)) + int(context.get("visible_upstream_tokens", 0))
        completion_tokens += int(context.get("expected_output_tokens", 0))
    prompt_tokens = int(prompt_tokens * bundle_discount)
    completion_tokens = int(completion_tokens * bundle_discount)
    return (
        prompt_tokens * float(endpoint["prompt_price_per_million"])
        + completion_tokens * float(endpoint["completion_price_per_million"])
    ) / 1_000_000


def _prompt_profile(works: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    names = sorted({name for work in works for name, value in work.get("prompt_requirements", {}).items() if float(value) >= 0.35})
    strengths = {name: max(float(work.get("prompt_requirements", {}).get(name, 0.0)) for work in works) for name in names}
    return {"profile_id": _digest("prompt", strengths), "modules": names, "strengths": {k: round(v, 6) for k, v in strengths.items()}}


def _reasoning_profile(works: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    enabled = any(bool(work.get("reasoning_requirements", {}).get("reasoning_enabled", True)) for work in works)
    numeric = sorted({name for work in works for name, value in work.get("reasoning_requirements", {}).items() if name != "reasoning_enabled" and isinstance(value, (int, float))})
    values = {name: max(float(work.get("reasoning_requirements", {}).get(name, 0.0)) for work in works) for name in numeric}
    depth = values.get("depth", 0.5)
    effort = "high" if depth >= 0.78 else "medium" if depth >= 0.52 else "low"
    return {"reasoning_enabled": enabled, "effort": effort, **{k: round(v, 6) for k, v in values.items()}}


def _parameter_profile(endpoint: Mapping[str, Any], works: Sequence[Mapping[str, Any]], reasoning: Mapping[str, Any]) -> dict[str, Any]:
    supported = {str(x).casefold() for x in endpoint.get("supported_parameters", [])}
    parameters: dict[str, Any] = {}
    if reasoning.get("reasoning_enabled") and "reasoning" in supported:
        parameters["reasoning"] = {"effort": reasoning.get("effort", "medium"), "exclude": True}
    machine = any(bool(work.get("output_contract", {}).get("machine_readable_required")) for work in works)
    if machine and supported & {"structured_outputs", "response_format", "json_schema"}:
        parameters["response_format"] = {"type": "json_object"}
    return {
        "profile_id": _digest("params", [endpoint.get("endpoint_id"), parameters]),
        "parameters": parameters,
        "supported_parameters": sorted(supported),
        "request_token_ceiling_sent": False,
        "reasoning_token_ceiling_sent": False,
    }


def _merge_output_contract(works: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    contracts = [
        dict(work.get("output_contract", {}))
        for work in works
        if isinstance(work.get("output_contract", {}), Mapping)
    ]
    explicit = [
        contract
        for contract in contracts
        if task_delivery_contract.explicit_contract_kind(contract) != "generic"
    ]
    if explicit:
        reference = explicit[0]
        reference_digest = task_delivery_contract.contract_digest(reference)
        for contract in explicit[1:]:
            if task_delivery_contract.contract_digest(contract) != reference_digest:
                raise V5PlanningError(
                    "Conflicting explicit user output contracts cannot be bundled."
                )
        merged = json.loads(json.dumps(reference, ensure_ascii=False))
        kind = task_delivery_contract.explicit_contract_kind(merged)
        if kind == "exact-json":
            merged["required_fields"] = list(merged.get("exact_top_level_fields", []))
            merged["machine_readable_required"] = True
        elif kind == "exact-markdown":
            merged["required_fields"] = list(merged.get("exact_markdown_headings", []))
            merged["machine_readable_required"] = False
        merged["must_separate_fact_assumption_inference"] = any(
            bool(contract.get("must_separate_fact_assumption_inference"))
            for contract in contracts
        )
        return merged

    fields: list[str] = []
    for contract in contracts:
        for field in contract.get("required_fields", []):
            value = str(field)
            if value not in fields:
                fields.append(value)
    return {
        "required_fields": fields,
        "machine_readable_required": any(
            bool(contract.get("machine_readable_required"))
            for contract in contracts
        ),
        "must_separate_fact_assumption_inference": any(
            bool(contract.get("must_separate_fact_assumption_inference"))
            for contract in contracts
        ),
    }


def _candidate_for(
    interpretation_id: str,
    coverage_keys: Sequence[str],
    works: Sequence[Mapping[str, Any]],
    copy_indices: Sequence[int],
    endpoint: Mapping[str, Any],
    demand_by_work: Mapping[str, Mapping[str, float]],
    hard_by_work: Mapping[str, set[str]],
    independence_groups: Sequence[str],
    *,
    bundle_discount: float = 1.0,
) -> CandidateNode | None:
    required_context = max(int(work.get("context_requirements", {}).get("required_context_tokens", 0)) for work in works)
    required_output = max(int(work.get("context_requirements", {}).get("expected_output_tokens", 0)) for work in works)
    if required_context > int(endpoint.get("context_length", 0)) or required_output > int(endpoint.get("max_completion_tokens", 0)):
        return None
    capabilities = dict(endpoint.get("capability_scores", {}))
    for work in works:
        work_id = str(work["work_id"])
        for label in hard_by_work[work_id]:
            minimum = max(0.48, 0.62 * float(demand_by_work[work_id].get(label, 0.0)))
            if float(capabilities.get(label, 0.0)) + 1e-12 < minimum:
                return None
    fits = [_capability_fit(demand_by_work[str(work["work_id"])], capabilities) for work in works]
    importance = [float(work.get("importance", 0.5)) for work in works]
    weighted_fit = sum(fit * weight for fit, weight in zip(fits, importance)) / max(0.001, sum(importance))
    benchmark = float(endpoint.get("benchmark_score", 0.5))
    reliability = float(endpoint.get("reliability", 0.95))
    quality_components = [weighted_fit, benchmark, reliability]
    if len(works) > 1:
        quality_components.append(min(fits))
    quality = _clamp(statistics.fmean(quality_components))
    confidence = _clamp(
        _geometric_mean(
            (float(endpoint.get("benchmark_confidence", 0.75)), min(fits))
        )
    )
    fit_dispersion = (max(fits) - min(fits)) if len(fits) > 1 else 0.0
    failure = _clamp(
        1.0 - _geometric_mean((reliability, confidence)) + fit_dispersion
    )
    prompt = _prompt_profile(works)
    reasoning = _reasoning_profile(works)
    parameters = _parameter_profile(endpoint, works, reasoning)
    output_contract = _merge_output_contract(works)
    parameters = {
        **parameters,
        **task_delivery_contract.contract_integrity_profile(
            output_contract,
            [str(work["work_id"]) for work in works],
        ),
    }
    assigned = tuple(sorted(str(work["work_id"]) for work in works))
    functions = tuple(sorted({str(name) for work in works for name in work.get("operation_requirements", {})}))
    professional = {label: round(float(value), 6) for label, value in capabilities.items() if any(label in demand_by_work[str(work["work_id"])] for work in works)}
    request_config = {
        "provider": {
            "order": [str(endpoint["provider_slug"])],
            "only": [str(endpoint["provider_slug"])],
            "allow_fallbacks": False,
            "require_parameters": True,
        },
        **dict(parameters["parameters"]),
    }
    forbidden = FORBIDDEN_REQUEST_FIELDS.intersection(request_config)
    if forbidden:
        raise V5PlanningError(f"Forbidden request fields generated: {sorted(forbidden)}")
    identity = [interpretation_id, sorted(coverage_keys), endpoint["endpoint_id"], prompt["profile_id"], parameters["profile_id"]]
    return CandidateNode(
        candidate_id=_digest("node", identity),
        interpretation_id=interpretation_id,
        coverage_keys=tuple(sorted(coverage_keys)),
        assigned_work=assigned,
        copy_indices=tuple(copy_indices),
        professional_capabilities=professional,
        functions=functions or ("analysis",),
        prompt_profile=prompt,
        reasoning_profile=reasoning,
        parameter_profile=parameters,
        model=str(endpoint["model_id"]),
        provider_endpoint=str(endpoint["provider_endpoint"]),
        provider_slug=str(endpoint["provider_slug"]),
        output_contract=output_contract,
        estimated_quality=round(quality, 6),
        quality_uncertainty=round(1.0 - confidence, 6),
        estimated_cost=round(_estimated_cost(endpoint, works, bundle_discount), 8),
        failure_probability=round(failure, 6),
        request_config=request_config,
        independence_groups=tuple(sorted(set(independence_groups))),
    )


def _dominates(left: CandidateNode, right: CandidateNode) -> bool:
    no_worse = (
        left.estimated_quality >= right.estimated_quality
        and left.estimated_cost <= right.estimated_cost
        and left.failure_probability <= right.failure_probability
    )
    strict = (
        left.estimated_quality > right.estimated_quality
        or left.estimated_cost < right.estimated_cost
        or left.failure_probability < right.failure_probability
    )
    return no_worse and strict


def pareto_prune(candidates: Sequence[CandidateNode], maximum_per_group: int = 12) -> list[CandidateNode]:
    groups: dict[tuple[str, tuple[str, ...]], list[CandidateNode]] = {}
    for candidate in candidates:
        groups.setdefault((candidate.interpretation_id, candidate.coverage_keys), []).append(candidate)
    kept: list[CandidateNode] = []
    for rows in groups.values():
        frontier = [row for row in rows if not any(_dominates(other, row) for other in rows if other is not row)]
        frontier.sort(key=lambda row: (-row.estimated_quality, row.estimated_cost, row.failure_probability, row.candidate_id))
        diverse: list[CandidateNode] = []
        seen_models: set[str] = set()
        for row in frontier:
            if row.model not in seen_models or len(diverse) < max(3, maximum_per_group // 2):
                diverse.append(row)
                seen_models.add(row.model)
            if len(diverse) >= maximum_per_group:
                break
        kept.extend(diverse)
    kept.sort(key=lambda row: (row.interpretation_id, row.coverage_keys, -row.estimated_quality, row.estimated_cost, row.candidate_id))
    return kept


def generate_candidate_graph(
    resource_bundle: Mapping[str, Any],
    market: Mapping[str, Any],
    *,
    maximum_per_group: int = 12,
) -> dict[str, Any]:
    endpoints = list(market.get("endpoints", []))
    all_candidates: list[CandidateNode] = []
    interpretation_meta: dict[str, Any] = {}
    for interpretation in resource_bundle.get("task_semantics", {}).get("interpretations", []):
        interpretation_id = str(interpretation["interpretation_id"])
        matrix = _matrix_map(resource_bundle, interpretation_id)
        graph = _graph_map(resource_bundle, interpretation_id)
        works = _work_map(resource_bundle, interpretation_id)
        labels = list(matrix["capability_labels"])
        demand_by_work: dict[str, dict[str, float]] = {}
        hard_by_work: dict[str, set[str]] = {}
        copies_by_work: dict[str, int] = {}
        stage_by_work = {work_id: index for index, stage in enumerate(graph.get("execution_stages", [])) for work_id in stage}
        for row_index, row in enumerate(matrix["work_index"]):
            work_id = str(row["work_id"])
            demand_by_work[work_id] = {label: float(matrix["task_resource_matrix"][row_index][col]) for col, label in enumerate(labels)}
            hard_by_work[work_id] = {label for col, label in enumerate(labels) if int(matrix["hard_requirement_matrix"][row_index][col]) == 1}
            copies_by_work[work_id] = max(1, int(row.get("minimum_independent_copies", 1)))
        for work_id, work in works.items():
            groups = [work_id] if bool(work.get("independence_requirements", {}).get("independent_execution_preferred")) else []
            for copy_index in range(copies_by_work[work_id]):
                key = f"{work_id}#{copy_index}"
                for endpoint in endpoints:
                    candidate = _candidate_for(
                        interpretation_id, [key], [work], [copy_index], endpoint,
                        demand_by_work, hard_by_work, groups,
                    )
                    if candidate is not None:
                        all_candidates.append(candidate)
        bundle_work = [work_id for work_id, copies in copies_by_work.items() if copies == 1 and not works[work_id].get("independence_requirements", {}).get("independent_execution_preferred")]
        for left_index, left_id in enumerate(sorted(bundle_work)):
            for right_id in sorted(bundle_work)[left_index + 1:]:
                if stage_by_work.get(left_id) != stage_by_work.get(right_id):
                    continue
                if works[left_id].get("dependencies", []) != works[right_id].get("dependencies", []):
                    continue
                for endpoint in endpoints:
                    candidate = _candidate_for(
                        interpretation_id,
                        [f"{left_id}#0", f"{right_id}#0"],
                        [works[left_id], works[right_id]],
                        [0, 0], endpoint,
                        demand_by_work, hard_by_work, [], bundle_discount=0.84,
                    )
                    if candidate is not None:
                        all_candidates.append(candidate)
        interpretation_meta[interpretation_id] = {
            "metrics": dict(interpretation.get("metrics", {})),
            "work_ids": sorted(works),
            "copies_by_work": copies_by_work,
            "atomic_edges": list(graph.get("edges", [])),
        }
    pruned = pareto_prune(all_candidates, maximum_per_group=maximum_per_group)
    if not pruned:
        raise V5PlanningError("Candidate generation produced no feasible nodes.")
    return {
        "version": 5,
        "architecture": "candidate-nodes-and-information-edges-before-joint-solve",
        "candidates": [row.to_dict() for row in pruned],
        "candidate_count_before_pareto": len(all_candidates),
        "candidate_count_after_pareto": len(pruned),
        "pareto_pruned_count": len(all_candidates) - len(pruned),
        "interpretations": interpretation_meta,
    }


def _candidate_objects(candidate_bundle: Mapping[str, Any]) -> list[CandidateNode]:
    return [CandidateNode(**row) for row in candidate_bundle.get("candidates", [])]


def _selected_graph(
    candidates: Sequence[CandidateNode],
    selected_indices: Sequence[int],
    candidate_bundle: Mapping[str, Any],
    interpretation_id: str,
    quality_floor: float,
    objective_quality: float,
    limits: GraphLimits,
) -> ExecutionGraph:
    selected = [candidates[index] for index in selected_indices]
    meta = candidate_bundle["interpretations"][interpretation_id]
    work_to_nodes: dict[str, list[str]] = {}
    nodes: list[SelectedNode] = []
    for candidate in selected:
        group = candidate.independence_groups[0] if len(candidate.independence_groups) == 1 else None
        node = SelectedNode(
            node_id=candidate.candidate_id,
            assigned_work=candidate.assigned_work,
            professional_capabilities=candidate.professional_capabilities,
            functions=candidate.functions,
            prompt_profile=candidate.prompt_profile,
            reasoning_profile=candidate.reasoning_profile,
            parameter_profile=candidate.parameter_profile,
            model=candidate.model,
            provider_endpoint=candidate.provider_endpoint,
            output_contract=candidate.output_contract,
            estimated_quality=candidate.estimated_quality,
            quality_uncertainty=candidate.quality_uncertainty,
            estimated_cost=candidate.estimated_cost,
            failure_probability=candidate.failure_probability,
            request_config=candidate.request_config,
            independence_group=group,
        )
        nodes.append(node)
        for work_id in candidate.assigned_work:
            work_to_nodes.setdefault(work_id, []).append(candidate.candidate_id)
    edge_map: dict[tuple[str, str], SelectedEdge] = {}
    for row in meta["atomic_edges"]:
        source_work = str(row["source"])
        target_work = str(row["target"])
        for source in work_to_nodes.get(source_work, []):
            for target in work_to_nodes.get(target_work, []):
                if source == target:
                    continue
                edge_map[(source, target)] = SelectedEdge(
                    source=source,
                    target=target,
                    relation_type="synthesis" if "synthesis" in next(node.functions for node in nodes if node.node_id == target) else "dependency",
                    payload_type="validated-node-output",
                    visibility_policy="declared-upstream-only",
                )
    dag = nx.DiGraph()
    dag.add_nodes_from(node.node_id for node in nodes)
    dag.add_edges_from(edge_map)
    if not nx.is_directed_acyclic_graph(dag):
        raise V5PlanningError("Selected candidate topology is not a DAG.")
    stages = tuple(tuple(sorted(stage)) for stage in nx.topological_generations(dag))
    entries = tuple(sorted(node for node in dag.nodes if dag.in_degree(node) == 0))
    finals = tuple(sorted(node for node in dag.nodes if dag.out_degree(node) == 0))
    recovery_pool: dict[str, list[dict[str, Any]]] = {}
    selected_ids = {row.candidate_id for row in selected}
    for chosen in selected:
        alternatives = [
            row for row in candidates
            if row.interpretation_id == interpretation_id
            and row.coverage_keys == chosen.coverage_keys
            and row.candidate_id not in selected_ids
            and row.model != chosen.model
        ]
        alternatives.sort(key=lambda row: (-row.estimated_quality, row.estimated_cost, row.failure_probability))
        recovery_pool[chosen.candidate_id] = [row.to_dict() for row in alternatives[: limits.max_replacements + 1]]
    graph = ExecutionGraph(
        nodes=tuple(sorted(nodes, key=lambda row: row.node_id)),
        edges=tuple(edge_map[key] for key in sorted(edge_map)),
        execution_stages=stages,
        entry_nodes=entries,
        final_nodes=finals,
        required_work=tuple(meta["work_ids"]),
        estimated_quality=round(objective_quality, 6),
        quality_floor=round(min(objective_quality, quality_floor), 6),
        estimated_total_cost=round(sum(node.estimated_cost for node in nodes), 8),
        metadata={
            "version": 5,
            "interpretation_id": interpretation_id,
            "optimizer": "google-or-tools-cp-sat",
            "objective_order": [
                "hard_constraints",
                "maximum-conservative-quality",
                "minimum-cost-inside-dynamic-quality-band",
                "minimum-failure-risk",
                "minimum-necessary-node-count",
            ],
            "recovery_pool": recovery_pool,
            "selected_coverage_keys": sorted(key for row in selected for key in row.coverage_keys),
        },
    )
    issues = validate_execution_graph(graph, limits)
    if issues:
        raise V5PlanningError("Selected execution graph failed validation: " + "; ".join(f"{x.code}:{x.message}" for x in issues))
    return graph


def optimize_execution_graph(
    candidate_bundle: Mapping[str, Any],
    *,
    limits: GraphLimits | None = None,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    limits = limits or GraphLimits()
    candidates = _candidate_objects(candidate_bundle)
    interpretations = dict(candidate_bundle.get("interpretations", {}))
    if not candidates or not interpretations:
        raise V5PlanningError("Candidate bundle is empty.")
    model = cp_model.CpModel()
    y = {iid: model.NewBoolVar(f"interpretation_{index}") for index, iid in enumerate(sorted(interpretations))}
    x = [model.NewBoolVar(f"candidate_{index}") for index in range(len(candidates))]
    model.Add(sum(y.values()) == 1)
    for index, candidate in enumerate(candidates):
        model.Add(x[index] <= y[candidate.interpretation_id])
    for interpretation_id, meta in interpretations.items():
        coverage_keys = [f"{work_id}#{copy_index}" for work_id, copies in meta["copies_by_work"].items() for copy_index in range(int(copies))]
        for key in coverage_keys:
            terms = [x[index] for index, candidate in enumerate(candidates) if candidate.interpretation_id == interpretation_id and key in candidate.coverage_keys]
            if not terms:
                model.Add(y[interpretation_id] == 0)
            else:
                model.Add(sum(terms) == y[interpretation_id])
    model.Add(sum(x) <= limits.max_nodes)
    cost_scale = 1_000_000
    quality_scale = 100_000
    cost_terms = [int(round(candidate.estimated_cost * cost_scale)) * x[index] for index, candidate in enumerate(candidates)]
    if limits.max_budget_usd is not None:
        model.Add(sum(cost_terms) <= int(round(limits.max_budget_usd * cost_scale)))
    for interpretation_id, meta in interpretations.items():
        for work_id, copies in meta["copies_by_work"].items():
            if int(copies) < 2:
                continue
            copy_candidates: dict[int, list[int]] = {}
            for copy_index in range(int(copies)):
                key = f"{work_id}#{copy_index}"
                copy_candidates[copy_index] = [index for index, candidate in enumerate(candidates) if candidate.interpretation_id == interpretation_id and key in candidate.coverage_keys]
            for left_copy in range(int(copies)):
                for right_copy in range(left_copy + 1, int(copies)):
                    for left in copy_candidates[left_copy]:
                        for right in copy_candidates[right_copy]:
                            if candidates[left].model == candidates[right].model or candidates[left].provider_endpoint == candidates[right].provider_endpoint:
                                model.Add(x[left] + x[right] <= 1)
    quality_terms = []
    for index, candidate in enumerate(candidates):
        conservative_quality = max(
            0.0,
            candidate.estimated_quality - candidate.quality_uncertainty,
        ) * (1.0 - candidate.failure_probability)
        quality_terms.append(
            int(round(conservative_quality * quality_scale)) * x[index]
        )
    interpretation_divisor = max(1, limits.max_nodes)
    for interpretation_id, variable in y.items():
        interpretation_score = float(
            interpretations[interpretation_id]
            .get("metrics", {})
            .get("interpretation_score", 0.5)
        )
        quality_terms.append(
            int(
                round(
                    interpretation_score
                    * quality_scale
                    / interpretation_divisor
                )
            )
            * variable
        )
    quality_expr = sum(quality_terms)
    solver = cp_model.CpSolver()
    stage_timeout = max(1.0, float(solver_timeout_seconds) / 4.0)
    solver.parameters.max_time_in_seconds = stage_timeout
    solver.parameters.num_search_workers = 8

    model.Maximize(quality_expr)
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise V5PlanningError(
            "No feasible V5 execution graph; "
            f"solver status={solver.StatusName(status)}"
        )
    best_quality_int = int(round(solver.ObjectiveValue()))
    observed_uncertainty = statistics.fmean(
        candidate.quality_uncertainty for candidate in candidates
    )
    requested_tolerance = _clamp(float(quality_tolerance_pct) / 100.0)
    tolerance = min(requested_tolerance, _clamp(observed_uncertainty))
    floor_int = int(math.floor(best_quality_int * (1.0 - tolerance)))
    model.Add(quality_expr >= floor_int)

    cost_expr = sum(cost_terms)
    model.Minimize(cost_expr)
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise V5PlanningError(
            "Quality-band cost solve failed; "
            f"solver status={solver.StatusName(status)}"
        )
    best_cost_int = int(round(solver.ObjectiveValue()))
    model.Add(cost_expr <= best_cost_int)

    failure_terms = [
        int(round(candidate.failure_probability * quality_scale)) * x[index]
        for index, candidate in enumerate(candidates)
    ]
    failure_expr = sum(failure_terms)
    model.Minimize(failure_expr)
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise V5PlanningError(
            "Quality-cost reliability solve failed; "
            f"solver status={solver.StatusName(status)}"
        )
    best_failure_int = int(round(solver.ObjectiveValue()))
    model.Add(failure_expr <= best_failure_int)

    model.Minimize(sum(x))
    status = solver.Solve(model)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        raise V5PlanningError(
            "Quality-cost-reliability compactness solve failed; "
            f"solver status={solver.StatusName(status)}"
        )
    selected_indices = [index for index, variable in enumerate(x) if solver.Value(variable)]
    selected_interpretations = [iid for iid, variable in y.items() if solver.Value(variable)]
    if len(selected_interpretations) != 1:
        raise V5PlanningError("Solver did not select exactly one interpretation.")
    selected_interpretation = selected_interpretations[0]
    normalized_quality = _clamp(sum(candidates[index].estimated_quality for index in selected_indices) / max(1, len(selected_indices)))
    normalized_floor = _clamp(normalized_quality * (1.0 - tolerance))
    graph = _selected_graph(
        candidates, selected_indices, candidate_bundle, selected_interpretation,
        normalized_floor, normalized_quality, limits,
    )
    return {
        "version": 5,
        "optimizer": "google-or-tools-cp-sat",
        "solver_status": solver.StatusName(status),
        "selected_interpretation": selected_interpretation,
        "quality_tolerance_pct": round(tolerance * 100.0, 6),
        "quality_tolerance_ceiling_pct": float(quality_tolerance_pct),
        "best_quality_objective_scaled": best_quality_int,
        "quality_floor_objective_scaled": floor_int,
        "best_cost_objective_scaled": best_cost_int,
        "best_failure_objective_scaled": best_failure_int,
        "optimization_policy": (
            "lexicographic-quality-cost-reliability-compactness"
        ),
        "selected_candidate_ids": [candidates[index].candidate_id for index in selected_indices],
        "execution_graph": graph.to_dict(),
        "fallback_used": False,
    }


def compile_and_optimize_v5(
    ranked: Sequence[Any],
    resource_bundle: Mapping[str, Any],
    *,
    endpoint_payloads: Mapping[str, Mapping[str, Any]] | None = None,
    allow_synthetic_fixture: bool = False,
    ranking_limit: int = 50,
    limits: GraphLimits | None = None,
    maximum_per_group: int = 12,
    quality_tolerance_pct: float = 2.0,
    solver_timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    market = compile_model_endpoint_market(
        ranked, resource_bundle,
        endpoint_payloads=endpoint_payloads,
        ranking_limit=ranking_limit,
        allow_synthetic_fixture=allow_synthetic_fixture,
    )
    candidates = generate_candidate_graph(resource_bundle, market, maximum_per_group=maximum_per_group)
    optimization = optimize_execution_graph(
        candidates,
        limits=limits,
        quality_tolerance_pct=quality_tolerance_pct,
        solver_timeout_seconds=solver_timeout_seconds,
    )
    return {"version": 5, "market": market, "candidate_graph": candidates, "optimization": optimization}
