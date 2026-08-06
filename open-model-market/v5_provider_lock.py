"""Provider routing validation for unrestricted OpenRouter routing."""
from __future__ import annotations

from typing import Any, Mapping


def canonical_provider_lock(request: Mapping[str, Any]) -> bool:
    """Validate that Provider routing is completely unrestricted.

    The historical function name is preserved for callers. Production requests
    should omit ``provider`` entirely. For compatibility, an empty provider
    object or ``{"allow_fallbacks": true}`` is accepted because neither reduces
    the Provider set. Every other Provider key is rejected, including future or
    unknown keys, so new router features cannot silently reintroduce filtering.
    """
    provider = request.get("provider")
    if provider is None:
        return True
    if not isinstance(provider, Mapping):
        return False
    if not provider:
        return True
    return set(provider) == {"allow_fallbacks"} and provider.get("allow_fallbacks") is True


def provider_routing_is_unrestricted(request: Mapping[str, Any]) -> bool:
    return canonical_provider_lock(request)


__all__ = ["canonical_provider_lock", "provider_routing_is_unrestricted"]
