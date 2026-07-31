"""Bounded concurrent endpoint-catalog collection for the V5 model market.

Expanding the intelligence pool to 150 models must not multiply catalog latency
linearly. This module keeps one request per direct model, caps concurrency, and
restores deterministic model order before the catalog snapshot is frozen.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Mapping, Sequence

from openrouter_api import OpenRouterRequestError, request_json
from v5_planner import V5PlanningError, _endpoint_url, _stable_model_id

DEFAULT_ENDPOINT_FETCH_WORKERS = 12
MAX_ENDPOINT_FETCH_WORKERS = 16


def fetch_live_endpoint_payloads(
    ranked: Sequence[Any],
    run: Any,
    *,
    maximum_models: int | None = None,
    maximum_workers: int = DEFAULT_ENDPOINT_FETCH_WORKERS,
) -> dict[str, Mapping[str, Any]]:
    """Fetch endpoint inventories concurrently and return deterministic order."""
    if not getattr(run, "api_key", None):
        raise V5PlanningError(
            "OPENROUTER_API_KEY is required to compile real provider endpoints."
        )
    eligible = [
        model
        for model in ranked
        if _stable_model_id(str(getattr(model, "id", "")))
    ]
    if maximum_models is not None:
        eligible = eligible[: max(1, int(maximum_models))]
    if not eligible:
        return {}

    timeout = int(getattr(run, "catalog_timeout_seconds", 30))
    retries = int(getattr(run, "catalog_max_retries", 1))
    worker_count = max(
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
                _endpoint_url(model_id),
                run.api_key,
                timeout,
                retries,
            )
        except OpenRouterRequestError as exc:
            payload = {"error": str(exc), "data": {"endpoints": []}}
        return model_id, payload

    unordered: dict[str, Mapping[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = {
            pool.submit(fetch_one, model): str(model.id)
            for model in eligible
        }
        for future in as_completed(futures):
            model_id, payload = future.result()
            unordered[model_id] = payload

    return {
        str(model.id): unordered[str(model.id)]
        for model in eligible
        if str(model.id) in unordered
    }
