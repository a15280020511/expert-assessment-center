"""Deterministic catalog normalization for GPT-led expert selection.

This module contains no scoring, optimization, Pareto pruning, or heuristic
candidate selection. It only applies constitutional eligibility filters,
preserves OpenRouter's official intelligence ordering, and exposes exact
model/provider rows to GPT.
"""
from __future__ import annotations

import json
import urllib.parse
from hashlib import sha256
from typing import Any, Mapping, Sequence

from v5_execution_primitives import finite_number
from v5_model_company import canonical_model_company

ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints"
MAX_VISIBLE_MODELS = 150
MINIMUM_EXPERT_COMPLETION_TOKENS = 256
FORBIDDEN_MODEL_TERMS = (
    "openrouter/",
    ":online",
    ":batch",
    ":free",
    "preview",
)
GOVERNANCE_COMPANIES = frozenset({"openai", "anthropic"})
OUTPUT_LIMIT_PARAMETERS = frozenset({"max_tokens", "max_completion_tokens"})


class CatalogViewError(RuntimeError):
    """Raised when no constitutionally usable catalog view can be built."""


def stable_model_id(model_id: str) -> bool:
    folded = str(model_id or "").strip().casefold()
    return bool(
        folded
        and "/" in folded
        and not any(term in folded for term in FORBIDDEN_MODEL_TERMS)
    )


def endpoint_url(model_id: str) -> str:
    if not stable_model_id(model_id):
        raise CatalogViewError(f"unstable or routed model id: {model_id!r}")
    author, slug = model_id.split("/", 1)
    return ENDPOINTS_URL.format(
        author=urllib.parse.quote(author, safe=""),
        slug=urllib.parse.quote(slug, safe=""),
    )


def eligible_models(
    models: Mapping[str, Any],
    *,
    requested_context: int,
    maximum_models: int = MAX_VISIBLE_MODELS,
    exclude_governance_companies: bool = True,
) -> list[Any]:
    """Return official-rank-ordered model candidates for endpoint inspection.

    Model-level ``max_completion_tokens`` is deliberately not a hard filter.
    OpenRouter may omit it at the aggregate model layer while exact provider
    endpoints expose a valid limit. Completion capacity is enforced only after
    exact endpoint inventories are fetched.
    """
    rows: list[Any] = []
    context_floor = max(1, int(requested_context))
    rank_ceiling = max(1, min(MAX_VISIBLE_MODELS, int(maximum_models)))
    for model in models.values():
        model_id = str(getattr(model, "id", "") or "")
        if not stable_model_id(model_id):
            continue
        company = canonical_model_company(model_id)
        if exclude_governance_companies and company in GOVERNANCE_COMPANIES:
            continue
        if int(getattr(model, "context_length", 0) or 0) < context_floor:
            continue
        if (
            getattr(model, "input_modalities", None)
            and "text" not in model.input_modalities
        ):
            continue
        if (
            getattr(model, "output_modalities", None)
            and "text" not in model.output_modalities
        ):
            continue
        prompt = getattr(model, "prompt_price_per_million", None)
        completion = getattr(model, "completion_price_per_million", None)
        if prompt is None or completion is None:
            continue
        rank = int(
            (getattr(model, "ranks", {}) or {}).get(
                "intelligence-high-to-low",
                MAX_VISIBLE_MODELS + 1,
            )
        )
        if rank > rank_ceiling:
            continue
        rows.append(model)
    rows.sort(
        key=lambda model: (
            int(
                (getattr(model, "ranks", {}) or {}).get(
                    "intelligence-high-to-low",
                    MAX_VISIBLE_MODELS + 1,
                )
            ),
            str(getattr(model, "id", "")),
        )
    )
    if not rows:
        raise CatalogViewError(
            "no constitutionally eligible model candidates within official "
            f"intelligence rank ceiling {rank_ceiling}"
        )
    return rows


def _endpoint_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data") if isinstance(payload, Mapping) else None
    if isinstance(data, Mapping) and isinstance(data.get("endpoints"), list):
        return [row for row in data["endpoints"] if isinstance(row, Mapping)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    if isinstance(payload.get("endpoints"), list):
        return [row for row in payload["endpoints"] if isinstance(row, Mapping)]
    return []


def _provider_slug(endpoint: Mapping[str, Any]) -> str:
    for key in ("tag", "provider_slug", "provider", "name", "provider_name"):
        value = str(endpoint.get(key) or "").strip()
        if value:
            return value
    return ""


def _ppm(value: Any, fallback: float = 0.0) -> float:
    number = finite_number(value, fallback)
    if number < 0:
        return fallback
    return number * 1_000_000 if number < 0.1 else number


def _synthetic_endpoint(model: Any, model_id: str) -> Mapping[str, Any]:
    return {
        "tag": f"fixture/{model_id.split('/', 1)[0]}",
        "context_length": getattr(model, "context_length", 0),
        "max_completion_tokens": getattr(model, "max_completion_tokens", 0),
        "pricing": {
            "prompt": getattr(model, "prompt_price_per_million", 0.0),
            "completion": getattr(model, "completion_price_per_million", 0.0),
        },
        "supported_parameters": list(
            getattr(model, "supported_parameters", []) or []
        ),
        "synthetic_fixture_only": True,
    }


def _model_endpoints(
    model: Any,
    model_id: str,
    endpoint_payloads: Mapping[str, Mapping[str, Any]],
    allow_synthetic_fixture: bool,
) -> list[Mapping[str, Any]]:
    endpoints = _endpoint_rows(endpoint_payloads.get(model_id, {}))
    if endpoints or not allow_synthetic_fixture:
        return endpoints
    return [_synthetic_endpoint(model, model_id)]


def _endpoint_numbers(
    model: Any,
    endpoint: Mapping[str, Any],
) -> tuple[int, int, float, float]:
    context_length = int(
        endpoint.get("context_length")
        or getattr(model, "context_length", 0)
        or 0
    )
    completion_tokens = int(
        endpoint.get("max_completion_tokens")
        or getattr(model, "max_completion_tokens", 0)
        or 0
    )
    pricing = (
        endpoint.get("pricing")
        if isinstance(endpoint.get("pricing"), Mapping)
        else {}
    )
    prompt = _ppm(
        pricing.get("prompt"),
        finite_number(getattr(model, "prompt_price_per_million", 0.0)),
    )
    completion = _ppm(
        pricing.get("completion"),
        finite_number(getattr(model, "completion_price_per_million", 0.0)),
    )
    return context_length, completion_tokens, prompt, completion


def _supported_parameters(model: Any, endpoint: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(value)
            for value in (
                endpoint.get("supported_parameters")
                or getattr(model, "supported_parameters", [])
                or []
            )
        }
    )


def _endpoint_rejection_reason(
    provider: str,
    context_length: int,
    completion_tokens: int,
    supported: Sequence[str],
    prompt: float,
    completion: float,
    *,
    context_floor: int,
    completion_floor: int,
) -> str:
    if not provider:
        return "endpoint-missing-provider"
    if context_length < context_floor:
        return "endpoint-insufficient-context"
    if completion_tokens < completion_floor:
        return "endpoint-missing-completion-limit"
    supported_folded = {value.casefold() for value in supported}
    if not OUTPUT_LIMIT_PARAMETERS.intersection(supported_folded):
        return "endpoint-cannot-enforce-output-limit"
    if prompt < 0 or completion < 0:
        return "invalid-pricing"
    return ""


def _catalog_endpoint_row(
    model: Any,
    model_id: str,
    endpoint: Mapping[str, Any],
    *,
    context_floor: int,
    completion_floor: int,
) -> tuple[dict[str, Any] | None, str]:
    provider = _provider_slug(endpoint)
    context_length, completion_tokens, prompt, completion = _endpoint_numbers(
        model, endpoint
    )
    supported = _supported_parameters(model, endpoint)
    reason = _endpoint_rejection_reason(
        provider,
        context_length,
        completion_tokens,
        supported,
        prompt,
        completion,
        context_floor=context_floor,
        completion_floor=completion_floor,
    )
    if reason:
        return None, reason
    return {
        "model": model_id,
        "company": canonical_model_company(model_id),
        "official_intelligence_rank": int(
            (getattr(model, "ranks", {}) or {}).get(
                "intelligence-high-to-low", MAX_VISIBLE_MODELS + 1
            )
        ),
        "provider": provider,
        "provider_endpoint": f"{model_id}@{provider}",
        "context_length": context_length,
        "max_completion_tokens": completion_tokens,
        "prompt_price_per_million": round(prompt, 8),
        "completion_price_per_million": round(completion, 8),
        "supported_parameters": supported,
        "input_modalities": list(
            getattr(model, "input_modalities", []) or ["text"]
        ),
        "output_modalities": list(
            getattr(model, "output_modalities", []) or ["text"]
        ),
        "synthetic_fixture_only": bool(endpoint.get("synthetic_fixture_only")),
    }, ""


def _canonical_endpoint_row(row: Mapping[str, Any]) -> str:
    return json.dumps(
        dict(row),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )


def _deduplicate_exact_endpoint_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    unique: list[Mapping[str, Any]] = []
    observed: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows:
        key = (
            str(row.get("model") or ""),
            str(row.get("provider") or ""),
        )
        if not all(key):
            continue
        existing = observed.get(key)
        if existing is None:
            observed[key] = row
            unique.append(row)
            continue
        if _canonical_endpoint_row(existing) == _canonical_endpoint_row(row):
            continue
        raise CatalogViewError(
            f"conflicting duplicate exact catalog endpoint: {key}"
        )
    return unique


def compact_endpoint_catalog(
    ranked: Sequence[Any],
    endpoint_payloads: Mapping[str, Mapping[str, Any]],
    *,
    allow_synthetic_fixture: bool = False,
    required_context_tokens: int = 1,
    minimum_completion_tokens: int = MINIMUM_EXPERT_COMPLETION_TOKENS,
) -> dict[str, Any]:
    """Expose exact model/provider rows without inferred capability scores."""
    rows: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []
    context_floor = max(1, int(required_context_tokens))
    completion_floor = max(
        MINIMUM_EXPERT_COMPLETION_TOKENS,
        int(minimum_completion_tokens),
    )
    for model in ranked:
        model_id = str(getattr(model, "id", "") or "")
        endpoints = _model_endpoints(
            model,
            model_id,
            endpoint_payloads,
            allow_synthetic_fixture,
        )
        if not endpoints:
            rejected.append({"model": model_id, "reason": "no-provider-endpoint"})
            continue
        for endpoint in endpoints:
            row, reason = _catalog_endpoint_row(
                model,
                model_id,
                endpoint,
                context_floor=context_floor,
                completion_floor=completion_floor,
            )
            if reason:
                rejected.append({"model": model_id, "reason": reason})
            elif row is not None:
                rows.append(row)
    rows = list(_deduplicate_exact_endpoint_rows(rows))
    rows.sort(
        key=lambda row: (
            int(row["official_intelligence_rank"]),
            str(row["model"]),
            str(row["provider"]),
        )
    )
    if not rows:
        raise CatalogViewError("no exact constitutionally usable endpoint rows")
    return {
        "schema_version": "v5-gpt-catalog-view-2",
        "selection_authority": "gpt-direct-no-local-scoring",
        "official_order_only": True,
        "local_score_computed": False,
        "optimizer_used": False,
        "pareto_pruning_used": False,
        "heuristic_ranking_used": False,
        "governance_companies_excluded": sorted(GOVERNANCE_COMPANIES),
        "required_context_tokens": context_floor,
        "minimum_completion_tokens": completion_floor,
        "endpoints": rows,
        "rejected": rejected,
    }


def catalog_index(
    catalog: Mapping[str, Any],
) -> dict[tuple[str, str], Mapping[str, Any]]:
    result: dict[tuple[str, str], Mapping[str, Any]] = {}
    rows = [
        row
        for row in catalog.get("endpoints", [])
        if isinstance(row, Mapping)
    ]
    for row in _deduplicate_exact_endpoint_rows(rows):
        key = (
            str(row.get("model") or ""),
            str(row.get("provider") or ""),
        )
        result[key] = row
    return result


def catalog_sha256(catalog: Mapping[str, Any]) -> str:
    rendered = json.dumps(
        catalog,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    return sha256(rendered.encode("utf-8")).hexdigest()
