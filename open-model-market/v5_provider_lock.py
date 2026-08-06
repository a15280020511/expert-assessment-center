"""Provider routing validation for legacy locks and unrestricted production routing."""
from __future__ import annotations

from typing import Any, Mapping


def _normalized_list(value: Any) -> list[str] | None:
    if not isinstance(value, list) or not value:
        return None
    normalized = [str(item).strip() for item in value]
    if any(not item for item in normalized) or len(normalized) != len(set(normalized)):
        return None
    return normalized


def provider_routing_is_unrestricted(request: Mapping[str, Any]) -> bool:
    """Return True only when the request does not reduce OpenRouter Provider choice.

    Production requests should omit ``provider`` entirely. For compatibility, an
    empty provider object or ``{"allow_fallbacks": true}`` is also unrestricted.
    Every other key is treated as a routing restriction, including unknown future
    keys, so new router features cannot silently reintroduce filtering.
    """
    provider = request.get("provider")
    if provider is None:
        return True
    if not isinstance(provider, Mapping):
        return False
    if not provider:
        return True
    return set(provider) == {"allow_fallbacks"} and provider.get("allow_fallbacks") is True


def canonical_provider_lock(request: Mapping[str, Any]) -> bool:
    """Compatibility validator used by legacy evidence paths.

    It accepts either the new unrestricted contract or the historical exact
    single-Provider lock. New production gates MUST additionally call
    ``provider_routing_is_unrestricted`` (and normally require no provider
    object), so keeping this rollback compatibility cannot re-enable Provider
    restrictions in the active production path.
    """
    if provider_routing_is_unrestricted(request):
        return True
    provider = request.get("provider")
    if not isinstance(provider, Mapping):
        return False
    only = _normalized_list(provider.get("only"))
    order = _normalized_list(provider.get("order"))
    return bool(
        only is not None
        and order is not None
        and len(only) == 1
        and only == order
        and provider.get("allow_fallbacks") is False
        and provider.get("require_parameters") is True
    )


__all__ = ["canonical_provider_lock", "provider_routing_is_unrestricted"]
