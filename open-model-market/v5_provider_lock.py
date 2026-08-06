"""Provider routing validation for exact locks and safe same-model fallback."""
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
    """Accept a legacy exact lock or a deterministic safe-fallback route.

    Safe-fallback mode keeps one or more preferred providers in ``order``,
    requires parameter compatibility, and permits OpenRouter to try another
    qualified endpoint for the same model when the preferred endpoint fails.
    """
    provider = request.get("provider")
    if not isinstance(provider, Mapping):
        return False
    order = _normalized_list(provider.get("order"), required=True)
    only = _normalized_list(provider.get("only"), required=False)
    if order is None or only is None or provider.get("require_parameters") is not True:
        return False

    allow_fallbacks = provider.get("allow_fallbacks")
    if allow_fallbacks is False:
        return len(order) == 1 and only == order
    if allow_fallbacks is True:
        return not only or order[0] in only
    return False
