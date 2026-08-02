"""Removed local planner compatibility sentinel.

Production selection is performed by GPT latest, red-teamed once by Claude
latest, then checked by the deterministic execution-graph validator.
"""
from __future__ import annotations

from typing import Any


class PlannerPolicy:
    """Fail immediately if obsolete local planning is invoked."""

    def __init__(self, runtime_config: Any) -> None:
        self.config = runtime_config

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(
            "Local planner was removed; GPT direct selection is authoritative"
        )
