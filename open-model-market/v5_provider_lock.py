"""Provider routing validation for unrestricted OpenRouter routing."""
from __future__ import annotations

from typing import Any, Mapping

_RESTRICTIVE_KEYS = {
    "only",
    "order",
    "ignore",
    "sort",
    "data_collection",
    "zdr",
    "quantizations",
    "max_price",
}


def canonical_provider_lock(request: Mapping[str, Any]) -> bool:
    """Validate that a request does not restrict provider choice.

    The historical function name is preserved for callers. A valid production
    request either omits ``provider`` entirely or carries only non-routing
    metadata with fallbacks enabled. Any allowlist/order/privacy/provider-price
    filter is rejected because it would reduce the server pool.
    """
    provider = request.get("provider")
    if provider is None:
        return True
    if not isinstance(provider, Mapping):
        return False
    if any(key in provider for key in _RESTRICTIVE_KEYS):
        return False
    if provider.get("allow_fallbacks") is False:
        return False
    return True


def provider_routing_is_unrestricted(request: Mapping[str, Any]) -> bool:
    return canonical_provider_lock(request)


__all__ = ["canonical_provider_lock", "provider_routing_is_unrestricted"]
