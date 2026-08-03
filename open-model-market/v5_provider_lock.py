"""Canonical single-endpoint Provider lock validation."""
from __future__ import annotations

from typing import Any, Mapping


def canonical_provider_lock(request: Mapping[str, Any]) -> bool:
    """Return True only for an exact one-provider lock with fallback disabled."""
    provider = request.get("provider")
    if not isinstance(provider, Mapping):
        return False
    only = provider.get("only")
    order = provider.get("order")
    if not isinstance(only, list) or len(only) != 1:
        return False
    if not isinstance(order, list):
        return False
    normalized_only = [str(value).strip() for value in only]
    normalized_order = [str(value).strip() for value in order]
    return (
        bool(normalized_only[0])
        and normalized_order == normalized_only
        and provider.get("allow_fallbacks") is False
    )
