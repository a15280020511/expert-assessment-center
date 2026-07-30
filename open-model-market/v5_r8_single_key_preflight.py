"""Single-key, zero-inference funding preflight for future R8 paid runs."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Mapping

from openrouter_api import request_json

CURRENT_KEY_URL = "https://openrouter.ai/api/v1/key"


class R8FundingPreflightError(RuntimeError):
    pass


def _number(value: Any) -> float | None:
    try:
        return None if value in {None, ""} else float(value)
    except (TypeError, ValueError):
        return None


def check_single_api_key(
    api_key: str,
    required_reserve_usd: float,
    *,
    request_fn: Callable[[str, str, int, int], Mapping[str, Any]] = request_json,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    """Check only the ordinary inference key; this function makes zero model calls."""
    key = str(api_key or "").strip()
    reserve = max(0.0, float(required_reserve_usd))
    if not key:
        raise R8FundingPreflightError("OPENROUTER_API_KEY is not set")

    payload = request_fn(CURRENT_KEY_URL, key, 30, 0)
    data = payload.get("data") if isinstance(payload, Mapping) else None
    data = data if isinstance(data, Mapping) else {}
    limit = _number(data.get("limit"))
    remaining = _number(data.get("limit_remaining"))
    blockers: list[str] = []
    if remaining is not None and remaining + 1e-12 < reserve:
        blockers.append("api-key-limit-remaining-below-required-reserve")

    report = {
        "version": 1,
        "policy": "ordinary-openrouter-api-key-only",
        "required_reserve_usd": round(reserve, 8),
        "current_key": {
            "label": data.get("label"),
            "limit_usd": limit,
            "limit_remaining_usd": remaining,
            "usage_usd": _number(data.get("usage")),
            "is_free_tier": data.get("is_free_tier"),
            "limit_mode": (
                "finite" if limit is not None and remaining is not None
                else "not-reported-or-unbounded"
            ),
        },
        "status": "insufficient" if blockers else (
            "verified-finite-key-limit" if remaining is not None
            else "ordinary-key-accepted-with-runtime-hard-cap"
        ),
        "blockers": blockers,
        "model_inference_calls": 0,
        "production_entrypoint_changed": False,
        "v3_deleted": False,
    }
    if output_path is not None:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return report
