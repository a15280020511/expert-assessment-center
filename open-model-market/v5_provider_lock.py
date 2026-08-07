"""Validate that OpenRouter Provider routing remains unrestricted."""
from __future__ import annotations

from typing import Any, Mapping


def provider_routing_is_unrestricted(request: Mapping[str, Any]) -> bool:
    """True only when a request does not narrow OpenRouter Provider choice."""
    provider = request.get("provider")
    if provider is None:
        return True
    if not isinstance(provider, Mapping):
        return False
    if not provider:
        return True
    return set(provider) == {"allow_fallbacks"} and provider.get("allow_fallbacks") is True


def canonical_provider_lock(request: Mapping[str, Any]) -> bool:
    """Backward-compatible name for the unrestricted-routing contract.

    Historical exact Provider locks are intentionally rejected. Keeping the old
    function name avoids breaking evidence/audit imports while making it
    impossible for a legacy caller to re-enable Provider pinning.
    """
    return provider_routing_is_unrestricted(request)


__all__ = ["canonical_provider_lock", "provider_routing_is_unrestricted"]
