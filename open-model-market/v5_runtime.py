"""Compatibility facade for the native V5 runtime.

The active architecture fixes model identity in the governance/OR-Tools plan but
keeps Provider routing unrestricted. ProductionExpertPromptPolicy removes any
Provider object before a model call. This facade preserves the native
PromptPolicy, ExpertExecutionEngine, and normalize_heading_key implementation
while replacing only the obsolete RuntimeConfig Provider-lock requirement.
"""
from __future__ import annotations

from dataclasses import dataclass

import v5_runtime_legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


PromptPolicy = _legacy.PromptPolicy
ExpertExecutionEngine = _legacy.ExpertExecutionEngine
normalize_heading_key = _legacy.normalize_heading_key


@dataclass(frozen=True)
class RuntimeConfig:
    openrouter_api_key: str
    openrouter_api_url: str
    application_name: str
    application_url: str
    timeout_seconds: float
    maximum_recovery_calls: int
    cost_anomaly_usd: float | None = None
    provider_lock_required: bool = False

    def __post_init__(self) -> None:
        if not self.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is required")
        if not self.openrouter_api_url.startswith("https://"):
            raise ValueError("OPENROUTER_API_URL must use https")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.maximum_recovery_calls < 0:
            raise ValueError("maximum_recovery_calls cannot be negative")
        if self.cost_anomaly_usd is not None and self.cost_anomaly_usd < 0:
            raise ValueError("cost_anomaly_usd cannot be negative")
        if self.provider_lock_required is not False:
            raise ValueError(
                "V5 active runtime requires unrestricted Provider routing"
            )


_legacy.RuntimeConfig = RuntimeConfig


__all__ = [name for name in globals() if not name.startswith("_")]
