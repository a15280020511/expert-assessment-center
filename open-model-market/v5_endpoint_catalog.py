"""Bounded exact endpoint collection for the GPT-led expert catalog.

Governance model inventories remain direct and unmodified. Non-governance
expert inventories are intersected with OpenRouter's authenticated ZDR endpoint
list so GPT can only select exact routes compatible with the request policy and
account/guardrail privacy restrictions.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping, Sequence

from openrouter_api import OpenRouterRequestError, request_json
from v5_catalog_view import (
    GOVERNANCE_COMPANIES,
    CatalogViewError,
    _provider_slug as provider_slug,
    endpoint_url,
    stable_model_id,
)
from v5_model_company import canonical_model_company

DEFAULT_ENDPOINT_FETCH_WORKERS = 12
MAX_ENDPOINT_FETCH_WORKERS = 16
ZDR_ENDPOINTS_URL = "https://openrouter.ai/api/v1/endpoints/zdr"
_NON_BASELINE_SERVICE_TIERS = ("/flex", "/batch")
_SERVICE_TIER_VERIFICATION_FIELDS = (
    "service_tier_compatibility_verified",
    "model_service_tier_supported",
)


def _zdr_endpoint_keys(payload: Mapping[str, Any]) -> frozenset[tuple[str, str]]:
    rows = payload.get("data") if isinstance(payload, Mapping) else None
    if not isinstance(rows, list):
        raise CatalogViewError("OpenRouter ZDR endpoint inventory is unavailable")
    keys = {
        (
            str(row.get("model_id") or "").strip(),
            provider_slug(row),
        )
        for row in rows
        if isinstance(row, Mapping)
    }
    usable = frozenset(key for key in keys if all(key))
    if not usable:
        raise CatalogViewError("OpenRouter ZDR endpoint inventory is empty")
    return usable


def _fetch_zdr_endpoint_keys(run: Any) -> frozenset[tuple[str, str]]:
    try:
        payload = request_json(
            ZDR_ENDPOINTS_URL,
            run.api_key,
            int(getattr(run, "catalog_timeout_seconds", 30)),
            int(getattr(run, "catalog_max_retries", 1)),
        )
    except OpenRouterRequestError as exc:
        raise CatalogViewError(
            f"cannot fetch OpenRouter ZDR endpoint inventory: {exc}"
        ) from exc
    return _zdr_endpoint_keys(payload)


def _baseline_service_compatible(endpoint: Mapping[str, Any]) -> bool:
    """Reject unverified service-tier variants that are not model-compatible.

    Provider inventory presence proves routing and privacy eligibility, not that
    a particular model supports optional Flex or Batch APIs. Such routes enter
    the executable catalog only when the endpoint payload explicitly carries a
    model-level compatibility assertion.
    """
    provider = provider_slug(endpoint).casefold()
    if not provider.endswith(_NON_BASELINE_SERVICE_TIERS):
        return True
    return any(endpoint.get(field) is True for field in _SERVICE_TIER_VERIFICATION_FIELDS)


def _filter_model_payload_to_zdr(
    model_id: str,
    payload: Mapping[str, Any],
    allowed: frozenset[tuple[str, str]],
) -> Mapping[str, Any]:
    result = dict(payload)
    data = payload.get("data")
    if not isinstance(data, Mapping):
        result["data"] = {"endpoints": []}
        return result
    normalized_data = dict(data)
    endpoints = data.get("endpoints")
    if not isinstance(endpoints, list):
        normalized_data["endpoints"] = []
        result["data"] = normalized_data
        return result
    eligible = [
        dict(endpoint)
        for endpoint in endpoints
        if isinstance(endpoint, Mapping)
        and (model_id, provider_slug(endpoint)) in allowed
        and _baseline_service_compatible(endpoint)
    ]
    privacy_eligible = [
        endpoint
        for endpoint in endpoints
        if isinstance(endpoint, Mapping)
        and (model_id, provider_slug(endpoint)) in allowed
    ]
    normalized_data["endpoints"] = eligible
    result["data"] = normalized_data
    result["zdr_endpoint_filter"] = {
        "required": True,
        "source": ZDR_ENDPOINTS_URL,
        "unfiltered_endpoint_count": len(endpoints),
        "privacy_eligible_endpoint_count": len(privacy_eligible),
        "service_tier_rejected_count": len(privacy_eligible) - len(eligible),
        "eligible_endpoint_count": len(eligible),
        "service_tier_policy": "standard-routes-or-explicit-model-level-verification",
    }
    return result


def fetch_live_endpoint_payloads(
    ranked: Sequence[Any],
    run: Any,
    *,
    maximum_models: int | None = None,
    maximum_workers: int = DEFAULT_ENDPOINT_FETCH_WORKERS,
) -> dict[str, Mapping[str, Any]]:
    """Fetch exact endpoint inventories; never score or reorder candidates."""
    if not getattr(run, "api_key", None):
        raise CatalogViewError(
            "OPENROUTER_API_KEY is required to fetch provider endpoints"
        )
    eligible = [
        model
        for model in ranked
        if stable_model_id(str(getattr(model, "id", "")))
    ]
    if maximum_models is not None:
        eligible = eligible[: max(1, int(maximum_models))]
    if not eligible:
        return {}

    expert_model_ids = {
        str(model.id)
        for model in eligible
        if canonical_model_company(str(model.id)) not in GOVERNANCE_COMPANIES
    }
    zdr_keys = _fetch_zdr_endpoint_keys(run) if expert_model_ids else frozenset()

    timeout = int(getattr(run, "catalog_timeout_seconds", 30))
    retries = int(getattr(run, "catalog_max_retries", 1))
    workers = max(
        1,
        min(
            int(maximum_workers),
            MAX_ENDPOINT_FETCH_WORKERS,
            len(eligible),
        ),
    )

    def fetch_one(model: Any) -> tuple[str, Mapping[str, Any]]:
        model_id = str(model.id)
        try:
            payload = request_json(
                endpoint_url(model_id),
                run.api_key,
                timeout,
                retries,
            )
        except OpenRouterRequestError as exc:
            payload = {
                "error": str(exc),
                "data": {"endpoints": []},
            }
        if model_id in expert_model_ids:
            payload = _filter_model_payload_to_zdr(model_id, payload, zdr_keys)
        return model_id, payload

    unordered: dict[str, Mapping[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_one, model) for model in eligible]
        for future in as_completed(futures):
            model_id, payload = future.result()
            unordered[model_id] = payload

    return {
        str(model.id): unordered[str(model.id)]
        for model in eligible
        if str(model.id) in unordered
    }
