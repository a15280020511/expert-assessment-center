"""Shared deterministic text normalization primitives."""
from __future__ import annotations

import re


def normalize_heading_key(value: str) -> str:
    """Return the canonical key used by all Markdown contract validators."""
    value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
    value = re.sub(r"^\d+(?:\.\d+)*[\s.)、:：-]+", "", value)
    value = re.sub(r"[^0-9a-z_\u4e00-\u9fff]+", "_", value)
    return value.strip("_")
