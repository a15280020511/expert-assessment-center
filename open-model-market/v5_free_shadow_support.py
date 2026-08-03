"""Free endpoint discovery and zero-cost call boundary for shadow acceptance."""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from openrouter_api import CHAT_URL, request_json
from v5_execution_primitives import actual_cost
from v5_model_company import canonical_model_company

MODELS_URL = "https://openrouter.ai/api/v1/models"
ENDPOINTS_URL = "https://openrouter.ai/api/v1/models/{author}/{slug}/endpoints"
OUTPUT_LIMIT_PARAMETERS = frozenset({"max_tokens", "max_completion_tokens"})
STRUCTURED_OUTPUT_PARAMETERS = frozenset(
    {"response_format", "structured_outputs"}
)
REASONING_PARAMETERS = frozenset({"reasoning", "reasoning_effort"})


class FreeShadowError(RuntimeError):
    """Fail-closed free shadow validation error."""


@dataclass(frozen=True)
class FreeEndpoint:
    model: str
    company: str
    provider: str
    context_length: int
    max_completion_tokens: int
    supported_parameters: tuple[str, ...]
    official_order: int

    @property
    def provider_endpoint(self) -> str:
        return f"{self.model}@{self.provider}"

    def to_catalog_row(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "company": self.company,
            "official_intelligence_rank": self.official_order,
            "provider": self.provider,
            "provider_endpoint": self.provider_endpoint,
            "context_length": self.context_length,
            "max_completion_tokens": self.max_completion_tokens,
            "prompt_price_per_million": 0.0,
            "completion_price_per_million": 0.0,
            "supported_parameters": list(self.supported_parameters),
            "input_modalities": ["text"],
            "output_modalities": ["text"],
            "synthetic_fixture_only": False,
        }


def _auth_headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "HTTP-Referer": os.getenv(
            "OPENROUTER_SITE_URL",
            "https://github.com/a15280020511/expert-assessment-center",
        ),
        "X-Title": os.getenv(
            "OPENROUTER_APP_NAME",
            "expert-center-free-shadow-acceptance",
        ),
    }


def _get_json(
    url: str,
    api_key: str,
    timeout: int = 60,
) -> Mapping[str, Any]:
    request = urllib.request.Request(url, headers=_auth_headers(api_key))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, Mapping):
        raise FreeShadowError(f"OpenRouter JSON root is not an object: {url}")
    return value


def _endpoint_url(model_id: str) -> str:
    if "/" not in model_id:
        raise FreeShadowError(f"invalid explicit free model id: {model_id}")
    author, slug = model_id.split("/", 1)
    return ENDPOINTS_URL.format(
        author=urllib.parse.quote(author, safe=""),
        slug=urllib.parse.quote(slug, safe=""),
    )


def _endpoint_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    data = payload.get("data")
    if isinstance(data, Mapping) and isinstance(data.get("endpoints"), list):
        return [row for row in data["endpoints"] if isinstance(row, Mapping)]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, Mapping)]
    endpoints = payload.get("endpoints")
    if isinstance(endpoints, list):
        return [row for row in endpoints if isinstance(row, Mapping)]
    return []


def _provider_slug(endpoint: Mapping[str, Any]) -> str:
    for key in ("tag", "provider_slug", "provider", "name", "provider_name"):
        value = str(endpoint.get(key) or "").strip()
        if value:
            return value
    return ""


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return fallback
    return result if result >= 0 else fallback


def _zero_price(value: Any) -> bool:
    return abs(_number(value, 1.0)) <= 1e-15


def _supported(
    model: Mapping[str, Any],
    endpoint: Mapping[str, Any],
) -> tuple[str, ...]:
    values = endpoint.get("supported_parameters") or model.get(
        "supported_parameters"
    )
    return tuple(sorted({str(value).casefold() for value in (values or [])}))


def _explicit_free_endpoint(
    model: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    official_order: int,
) -> FreeEndpoint | None:
    model_id = str(model.get("id") or "").strip()
    if not model_id.endswith(":free") or model_id == "openrouter/free":
        return None
    pricing = endpoint.get("pricing")
    pricing = pricing if isinstance(pricing, Mapping) else {}
    model_pricing = model.get("pricing")
    model_pricing = model_pricing if isinstance(model_pricing, Mapping) else {}
    prompt_price = pricing.get("prompt", model_pricing.get("prompt"))
    completion_price = pricing.get(
        "completion",
        model_pricing.get("completion"),
    )
    if not _zero_price(prompt_price) or not _zero_price(completion_price):
        return None
    provider = _provider_slug(endpoint)
    if not provider:
        return None
    context_length = int(
        endpoint.get("context_length")
        or model.get("context_length")
        or 0
    )
    top = model.get("top_provider")
    top = top if isinstance(top, Mapping) else {}
    maximum_output = int(
        endpoint.get("max_completion_tokens")
        or top.get("max_completion_tokens")
        or 0
    )
    supported = _supported(model, endpoint)
    if not OUTPUT_LIMIT_PARAMETERS.intersection(supported):
        return None
    return FreeEndpoint(
        model=model_id,
        company=canonical_model_company(model_id),
        provider=provider,
        context_length=context_length,
        max_completion_tokens=maximum_output,
        supported_parameters=supported,
        official_order=max(1, int(official_order)),
    )


def discover_free_endpoints(
    api_key: str,
    *,
    minimum_context_tokens: int,
    minimum_completion_tokens: int,
    maximum_models_inspected: int = 40,
) -> list[FreeEndpoint]:
    payload = _get_json(MODELS_URL, api_key)
    data = payload.get("data")
    if not isinstance(data, list):
        raise FreeShadowError("OpenRouter model catalog has no data array")
    free_models = [
        row
        for row in data
        if isinstance(row, Mapping)
        and str(row.get("id") or "").endswith(":free")
        and str(row.get("id") or "") != "openrouter/free"
    ]
    candidates = _inspect_free_models(
        free_models[: max(1, maximum_models_inspected)],
        api_key,
        minimum_context_tokens,
        minimum_completion_tokens,
    )
    unique: dict[tuple[str, str], FreeEndpoint] = {}
    for row in candidates:
        unique.setdefault((row.model, row.provider.casefold()), row)
    result = list(unique.values())
    result.sort(key=_endpoint_sort_key)
    if not result:
        raise FreeShadowError("no live explicit zero-price endpoint is usable")
    return result


def _inspect_free_models(
    models: Sequence[Mapping[str, Any]],
    api_key: str,
    minimum_context_tokens: int,
    minimum_completion_tokens: int,
) -> list[FreeEndpoint]:
    candidates: list[FreeEndpoint] = []
    for index, model in enumerate(models):
        model_id = str(model.get("id") or "")
        try:
            payload = _get_json(_endpoint_url(model_id), api_key)
        except Exception:
            continue
        for endpoint in _endpoint_rows(payload):
            row = _explicit_free_endpoint(model, endpoint, index + 1)
            if row is None:
                continue
            if row.context_length < minimum_context_tokens:
                continue
            if row.max_completion_tokens < minimum_completion_tokens:
                continue
            candidates.append(row)
    return candidates


def _endpoint_sort_key(row: FreeEndpoint) -> tuple[Any, ...]:
    structured = bool(
        STRUCTURED_OUTPUT_PARAMETERS.intersection(row.supported_parameters)
    )
    reasoning = bool(REASONING_PARAMETERS.intersection(row.supported_parameters))
    return (
        -int(structured),
        -int(reasoning),
        -row.max_completion_tokens,
        -row.context_length,
        row.official_order,
        row.model,
        row.provider.casefold(),
    )


def _has_governance_capabilities(endpoint: FreeEndpoint) -> bool:
    supported = set(endpoint.supported_parameters)
    return bool(
        STRUCTURED_OUTPUT_PARAMETERS.intersection(supported)
        and REASONING_PARAMETERS.intersection(supported)
        and OUTPUT_LIMIT_PARAMETERS.intersection(supported)
    )


def choose_shadow_roles(
    endpoints: Sequence[FreeEndpoint],
) -> tuple[FreeEndpoint, FreeEndpoint, list[FreeEndpoint]]:
    governance = [row for row in endpoints if _has_governance_capabilities(row)]
    proposal = next(iter(governance), None)
    if proposal is None:
        raise FreeShadowError("no free endpoint supports governance JSON protocol")
    red_team = next(
        (row for row in governance if row.company != proposal.company),
        None,
    )
    if red_team is None:
        raise FreeShadowError("free governance requires two distinct model companies")
    excluded = {
        proposal.company,
        red_team.company,
        "openai",
        "anthropic",
        "unknown",
    }
    experts = _distinct_company_endpoints(
        [row for row in endpoints if row.company not in excluded]
    )
    if not experts:
        raise FreeShadowError(
            "free shadow requires a third distinct company for expert execution"
        )
    return proposal, red_team, experts[:6]


def _distinct_company_endpoints(
    endpoints: Sequence[FreeEndpoint],
) -> list[FreeEndpoint]:
    result: list[FreeEndpoint] = []
    seen: set[str] = set()
    for row in endpoints:
        if row.company in seen:
            continue
        result.append(row)
        seen.add(row.company)
    return result


def governance_resolution(
    proposal: FreeEndpoint,
    red_team: FreeEndpoint,
    *,
    required_context_tokens: int,
    minimum_completion_tokens: int,
) -> dict[str, Any]:
    return {
        "schema_version": "v5-free-shadow-governance-model-resolution-1",
        "status": "PASS",
        "selection_basis": "live-explicit-zero-price-endpoints",
        "required_context_tokens": required_context_tokens,
        "minimum_completion_tokens": minimum_completion_tokens,
        "provider_fallback_allowed": False,
        "formal_model_identity_qualified": False,
        "gpt": _governance_role("~shadow/free-proposal", proposal),
        "claude": _governance_role("~shadow/free-red-team", red_team),
    }


def _governance_role(logical: str, row: FreeEndpoint) -> dict[str, Any]:
    return {
        "logical_model": logical,
        "resolved_model": row.model,
        "company": row.company,
        "provider": row.provider,
        "official_intelligence_rank": row.official_order,
        "context_length": row.context_length,
        "max_completion_tokens": row.max_completion_tokens,
        "supported_parameters": list(row.supported_parameters),
        "temperature_supported": "temperature" in row.supported_parameters,
        "provider_fallback_allowed": False,
        "synthetic_fixture_only": False,
        "free_shadow_only": True,
    }


def expert_catalog(
    experts: Sequence[FreeEndpoint],
    *,
    required_context_tokens: int,
) -> dict[str, Any]:
    return {
        "schema_version": "v5-free-shadow-expert-catalog-1",
        "selection_authority": "production-gpt-protocol-with-free-shadow-model",
        "official_order_only": True,
        "local_score_computed": False,
        "optimizer_used": False,
        "pareto_pruning_used": False,
        "heuristic_ranking_used": False,
        "governance_companies_excluded": True,
        "required_context_tokens": required_context_tokens,
        "minimum_completion_tokens": 256,
        "endpoints": [row.to_catalog_row() for row in experts],
        "rejected": [],
    }


class FreeCallBoundary:
    """Shared zero-cost boundary for three governance calls and one expert."""

    def __init__(self, maximum_calls: int = 4) -> None:
        self.maximum_calls = int(maximum_calls)
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        run: Any,
        request: Mapping[str, Any],
    ) -> tuple[Mapping[str, Any], float]:
        if len(self.calls) >= self.maximum_calls:
            raise FreeShadowError("free shadow call ceiling exceeded")
        payload, requested_model, expected_provider = self._validated_payload(request)
        started = time.monotonic()
        response = request_json(
            CHAT_URL,
            str(getattr(run, "api_key", "")),
            int(getattr(run, "model_timeout_seconds", 180)),
            0,
            payload,
        )
        latency = time.monotonic() - started
        self._validate_response(response, requested_model, expected_provider)
        self._record(response)
        return response, latency

    @staticmethod
    def _validated_payload(
        request: Mapping[str, Any],
    ) -> tuple[dict[str, Any], str, str]:
        payload = dict(request)
        requested_model = str(payload.get("model") or "")
        if not requested_model.endswith(":free"):
            raise FreeShadowError(
                f"non-free model reached free boundary: {requested_model}"
            )
        provider = payload.get("provider")
        provider = dict(provider) if isinstance(provider, Mapping) else {}
        only = provider.get("only")
        if not isinstance(only, list) or len(only) != 1:
            raise FreeShadowError("free shadow request lacks one exact provider lock")
        expected_provider = str(only[0]).strip()
        if not expected_provider:
            raise FreeShadowError("free shadow provider lock is empty")
        if provider.get("allow_fallbacks") is not False:
            raise FreeShadowError("free shadow provider fallback is forbidden")
        provider.update({"data_collection": "allow", "zdr": False})
        payload["provider"] = provider
        return payload, requested_model, expected_provider

    @staticmethod
    def _validate_response(
        response: Mapping[str, Any],
        requested_model: str,
        expected_provider: str,
    ) -> None:
        response_model = str(response.get("model") or "")
        response_provider = str(response.get("provider") or "")
        cost = float(actual_cost(response))
        if response_model != requested_model:
            raise FreeShadowError(
                f"free shadow model mismatch: {requested_model}/{response_model}"
            )
        if response_provider.casefold() != expected_provider.casefold():
            raise FreeShadowError(
                "free shadow provider mismatch: "
                f"{expected_provider}/{response_provider}"
            )
        if abs(cost) > 1e-12:
            raise FreeShadowError(f"free shadow returned positive cost: {cost}")

    def _record(self, response: Mapping[str, Any]) -> None:
        model = str(response.get("model") or "")
        usage = response.get("usage")
        usage = dict(usage) if isinstance(usage, Mapping) else {}
        self.calls.append(
            {
                "sequence": len(self.calls) + 1,
                "model": model,
                "company": canonical_model_company(model),
                "provider": str(response.get("provider") or ""),
                "actual_cost_usd": float(actual_cost(response)),
                "response_id": str(response.get("id") or "") or None,
                "usage": usage,
            }
        )

    def receipt(self) -> dict[str, Any]:
        total = sum(float(row["actual_cost_usd"]) for row in self.calls)
        return {
            "schema_version": "v5-free-shadow-call-boundary-1",
            "status": "PASS" if len(self.calls) == self.maximum_calls else "FAIL",
            "maximum_calls": self.maximum_calls,
            "actual_calls": len(self.calls),
            "paid_model_calls": 0,
            "paid_fallback_allowed": False,
            "actual_cost_usd": round(total, 12),
            "calls": list(self.calls),
        }
