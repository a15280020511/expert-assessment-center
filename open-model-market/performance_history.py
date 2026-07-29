"""Persistent model feedback with lossless concurrent updates and atomic writes."""
from __future__ import annotations

import json
import math
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

DEFAULT_SCORE = 0.55
MIN_WARMUP_CALLS = 3
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _read_models(path: Path) -> Dict[str, Dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    models = data.get("models") if isinstance(data, dict) else None
    return dict(models) if isinstance(models, dict) else {}


def load_history(path: Path) -> Dict[str, Dict[str, Any]]:
    """Load the latest parseable history snapshot."""
    return _read_models(path)


def history_score(stats: Mapping[str, Any]) -> float:
    """Return reliability without speed/popularity and protect new-model warmup."""
    calls = int(stats.get("calls") or 0)
    if calls <= 0:
        return DEFAULT_SCORE
    successes = int(stats.get("successes") or 0)
    empty = int(stats.get("empty_answers") or 0)
    truncated = int(stats.get("truncated") or 0)
    timeouts = int(stats.get("timeouts") or 0)
    cost_ratio = float(stats.get("avg_actual_to_estimated_cost") or 1.0)
    reasoning_share = float(stats.get("avg_reasoning_share") or 0.45)
    success_rate = successes / calls
    penalty = min(0.35, empty / calls * 0.30 + truncated / calls * 0.15 + timeouts / calls * 0.20)
    cost_score = 1.0 / (1.0 + max(0.0, cost_ratio - 1.0) * 2.0)
    reasoning_score = 1.0 - min(0.35, max(0.0, reasoning_share - 0.65))
    raw = min(1.0, max(0.0, success_rate * 0.65 + cost_score * 0.20 + reasoning_score * 0.15 - penalty))
    if calls < MIN_WARMUP_CALLS:
        prior_weight = MIN_WARMUP_CALLS
        warmed = (raw * calls + DEFAULT_SCORE * prior_weight) / (calls + prior_weight)
        return min(1.0, max(0.30, warmed))
    return raw


def _weighted_average(old: float, count: int, new: float) -> float:
    return (old * count + new) / (count + 1)


def _updated_stats(
    current: Mapping[str, Any],
    *,
    success: bool,
    latency_seconds: float,
    actual_cost: float,
    estimated_cost: float,
    finish_reason: str | None,
    error: str | None,
    reasoning_tokens: int | None,
    completion_tokens: int | None,
) -> Dict[str, Any]:
    stats = dict(current)
    calls = int(stats.get("calls") or 0)
    stats["calls"] = calls + 1
    stats["successes"] = int(stats.get("successes") or 0) + int(success)
    text = (error or "").casefold()
    stats["empty_answers"] = int(stats.get("empty_answers") or 0) + int(
        "empty" in text or "no final answer" in text
    )
    stats["timeouts"] = int(stats.get("timeouts") or 0) + int(
        "timeout" in text or "timed out" in text
    )
    stats["truncated"] = int(stats.get("truncated") or 0) + int(finish_reason == "length")
    stats["consecutive_failures"] = 0 if success else int(stats.get("consecutive_failures") or 0) + 1
    stats["last_error"] = error
    stats["last_finish_reason"] = finish_reason
    stats["updated_at"] = datetime.now(timezone.utc).isoformat()
    stats["avg_latency_seconds"] = round(
        _weighted_average(float(stats.get("avg_latency_seconds") or 0), calls, max(0.0, latency_seconds)),
        6,
    )
    ratio = actual_cost / estimated_cost if estimated_cost > 0 else 1.0
    if not math.isfinite(ratio):
        ratio = 1.0
    stats["avg_actual_to_estimated_cost"] = round(
        _weighted_average(float(stats.get("avg_actual_to_estimated_cost") or 1.0), calls, ratio),
        6,
    )
    share = (reasoning_tokens or 0) / completion_tokens if completion_tokens and completion_tokens > 0 else 0.0
    stats["avg_reasoning_share"] = round(
        _weighted_average(float(stats.get("avg_reasoning_share") or 0), calls, share),
        6,
    )
    return stats


def _atomic_write(path: Path, history: Mapping[str, Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"version": 2, "models": history}, ensure_ascii=False, indent=2)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def record(
    path: Path,
    *,
    model_id: str,
    success: bool,
    latency_seconds: float,
    actual_cost: float,
    estimated_cost: float,
    finish_reason: str | None,
    error: str | None,
    reasoning_tokens: int | None,
    completion_tokens: int | None,
) -> None:
    """Merge against the latest snapshot under a per-path lock, then atomically replace."""
    # Preserve the public read outside the lock so simultaneous completions can
    # observe a snapshot; the authoritative merge always re-reads under lock.
    load_history(path)
    with _path_lock(path):
        history = _read_models(path)
        history[model_id] = _updated_stats(
            history.get(model_id, {}),
            success=success,
            latency_seconds=latency_seconds,
            actual_cost=actual_cost,
            estimated_cost=estimated_cost,
            finish_reason=finish_reason,
            error=error,
            reasoning_tokens=reasoning_tokens,
            completion_tokens=completion_tokens,
        )
        _atomic_write(path, history)
