"""Provider routing validation for exact locks and audited fallback pools."""
from __future__ import annotations

from typing import Any, Mapping


def _normalized_list(value: Any, *, required: bool) -> list[str] | None:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        return None
    normalized = [str(item).strip() for item in value]
    if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
        return None
    return normalized


def canonical_provider_lock(request: Mapping[str, Any]) -> bool:
    """Accept a legacy exact lock or an explicit audited provider pool.

    Safe fallback never means unrestricted routing. ``only`` and ``order`` must
    contain the same deterministic list of provider endpoints for one model;
    OpenRouter may fail over only inside that previously qualified whitelist.
    """
    provider = request.get("provider")
    if not isinstance(provider, Mapping):
        return False
    order = _normalized_list(provider.get("order"), required=True)
    only = _normalized_list(provider.get("only"), required=True)
    if order is None or only is None or provider.get("require_parameters") is not True:
        return False

    allow_fallbacks = provider.get("allow_fallbacks")
    if allow_fallbacks is False:
        return len(order) == 1 and only == order
    if allow_fallbacks is True:
        return only == order
    return False
