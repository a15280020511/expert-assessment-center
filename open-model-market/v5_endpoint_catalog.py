"""Bounded exact endpoint collection for the GPT-led expert catalog."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping, Sequence

from openrouter_api import OpenRouterRequestError, request_json
from v5_catalog_view import CatalogViewError, endpoint_url, stable_model_id

DEFAULT_ENDPOINT_FETCH_WORKERS = 12
MAX_ENDPOINT_FETCH_WORKERS = 16


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
