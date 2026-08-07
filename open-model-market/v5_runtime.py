"""Compatibility facade for the native V5 runtime.

The active runtime keeps only intrinsic validity checks. Historical fixed team,
call-count and Provider-lock ceilings are removed; the current execution graph
determines how many expert and recovery calls are needed.
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
    total_call_limit: int
    recovery_call_limit: int
    cost_anomaly_usd: float | None
    tools_allowed: bool = False
    live_catalog_required: bool = False
    provider_lock_required: bool = False
    cost_risk_multiplier: float = 1.18
    max_provider_failures: int = 2

    def __post_init__(self) -> None:
        if int(self.total_call_limit) < 1:
            raise ValueError("total_call_limit must be positive")
        if not 0 <= int(self.recovery_call_limit) < int(self.total_call_limit):
            raise ValueError(
                "recovery_call_limit must be non-negative and below total_call_limit"
            )
        if self.cost_anomaly_usd is not None and not math.isfinite(
            float(self.cost_anomaly_usd)
        ):
            raise ValueError("cost_anomaly_usd must be finite when supplied")
        if self.tools_allowed:
            raise ValueError("expert runtime external tools remain disabled")
        if self.provider_lock_required:
            raise ValueError("active runtime uses unrestricted OpenRouter Provider routing")
        if not math.isfinite(float(self.cost_risk_multiplier)) or float(
            self.cost_risk_multiplier
        ) <= 0:
            raise ValueError("cost_risk_multiplier must be finite and positive")
        if int(self.max_provider_failures) < 0:
            raise ValueError("max_provider_failures must be non-negative")

    @property
    def initial_call_limit(self) -> int:
        return int(self.total_call_limit) - int(self.recovery_call_limit)

    def to_dict(self) -> dict[str, object]:
        return {
            **asdict(self),
            "initial_call_limit": self.initial_call_limit,
            "runtime_version": _legacy.RUNTIME_VERSION,
            "provider_routing_mode": "unrestricted-openrouter",
            "fixed_call_ceiling_applied": False,
            "team_size_source": "current-execution-graph",
        }


_legacy.RuntimeConfig = RuntimeConfig

__all__ = [name for name in globals() if not name.startswith("_")]
