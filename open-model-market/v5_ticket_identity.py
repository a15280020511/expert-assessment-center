"""Canonical task identity shared by admission and immutable execution gate."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any, Mapping


def normalize_semantic_text(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = re.sub(r"[\s\u3000]+", " ", text)
    text = re.sub(r"[，。！？；：、,.!?;:]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def task_fingerprint(packet: Mapping[str, Any]) -> str:
    task = packet.get("task") if isinstance(packet.get("task"), Mapping) else {}
    requirements = (
        task.get("requirements") if isinstance(task.get("requirements"), list) else []
    )
    canonical = {
        "question": normalize_semantic_text(task.get("question")),
        "requirements": sorted(
            {
                normalized
                for item in requirements
                if isinstance(item, str)
                for normalized in [normalize_semantic_text(item)]
                if normalized
            }
        ),
    }
    raw = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
