"""Compatibility facade for the native V5 runtime.

Provider opening is intentionally enforced by the production expert policy and
production materializer, not by mutating the reusable base runtime. This keeps
legacy rollback/unit contracts isolated while the active production entrypoint
still removes every Provider routing field before a model call.
"""
from __future__ import annotations

import v5_runtime_legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


__all__ = [name for name in dir(_legacy) if not name.startswith("__")]
