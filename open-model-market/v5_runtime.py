"""Compatibility facade for the native V5 runtime.

The active architecture fixes model identity in the signed Top-50/OR-Tools plan
while leaving OpenRouter Provider routing unrestricted.  The native runtime is
kept intact except for one obsolete invariant: production no longer requires an
exact Provider lock.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import v5_runtime_legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


@dataclass(frozen=True)
class RuntimeConfig:
    """Native runtime configuration with unrestricted Provider routing.

    Field names and semantics intentionally match ``v5_runtime_legacy`` so all
    existing runtime, recovery, budget, and evidence code remains compatible.
    Only the historical exact-Provider-lock requirement is removed.
    """

    total_call_limit: int
    recovery_call_limit: int
    cost_anomaly_usd: float | None
    tools_allowed: bool = False
    live_catalog_required: bool = False
    provider_lock_required: bool = False
    cost_risk_multiplier: float = 1.18
    max_provider_failures: int = 2

    def __post_init__(self) -> None:
        if not 1 <= int(self.total_call_limit) <= 16:
            raise ValueError("total_call_limit must be between 1 and 16")
        if not 0 <= int(self.recovery_call_limit) < int(self.total_call_limit):
            raise ValueError(
                "recovery_call_limit must be non-negative and below total_call_limit"
            )
        if self.cost_anomaly_usd is not None and (
            not math.isfinite(float(self.cost_anomaly_usd))
            or float(self.cost_anomaly_usd) <= 0
        ):
            raise ValueError("cost_anomaly_usd must be finite and positive")
        if self.tools_allowed:
            raise ValueError("V5 expert runtime forbids external tools")
        if self.provider_lock_required:
            raise ValueError(
                "V5 active runtime requires unrestricted Provider routing"
            )
        if not math.isfinite(float(self.cost_risk_multiplier)) or float(
            self.cost_risk_multiplier
        ) < 1.0:
            raise ValueError("cost_risk_multiplier must be finite and at least 1")
        if int(self.max_provider_failures) < 1:
            raise ValueError("max_provider_failures must be positive")

    @property
    def initial_call_limit(self) -> int:
        return int(self.total_call_limit) - int(self.recovery_call_limit)

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "initial_call_limit": self.initial_call_limit,
            "runtime_version": _legacy.RUNTIME_VERSION,
            "provider_routing_mode": "unrestricted-openrouter",
        }


# The preserved native classes resolve RuntimeConfig from their module globals at
# execution time.  Replace only that symbol so production and rollback facades
# share one field-compatible configuration contract.
_legacy.RuntimeConfig = RuntimeConfig


__all__ = [name for name in globals() if not name.startswith("_")]
