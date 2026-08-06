"""Compatibility facade for the native constitutional runtime.

The active production entrypoint installs ``ProductionExpertPromptPolicy``,
which strips the Provider object after all reusable prompt/constitutional logic
has run. Keeping this module compatible avoids changing historical non-production
contracts while preserving fully unrestricted Provider routing in production.
"""
from __future__ import annotations

import v5_constitutional_runtime_legacy as _legacy

for _name in dir(_legacy):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_legacy, _name)


__all__ = [name for name in dir(_legacy) if not name.startswith("__")]
