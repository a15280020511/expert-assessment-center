"""Control-plane guards for complete delivery and no-limit cost accounting."""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from model_market import ExpertTeamError, RunConfig
from response_audit import diagnostics


def _finite_nonnegative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _diagnostic_cost(attempt: Mapping[str, Any]) -> float | None:
    info = attempt.get("response_diagnostics")
    if not isinstance(info, Mapping):
        return None
    return _finite_nonnegative(info.get("cost"))


def expert_attempt_cost_evidence(results: Sequence[Any]) -> Dict[str, Any]:
    actual_total = 0.0
    conservative_total = 0.0
    actual_known = True
    entries = []
    for result in results:
        attempts = getattr(result, "attempts", []) or []
        real_index = 0
        for attempt in attempts:
            if not isinstance(attempt, Mapping) or attempt.get("replacement") is True:
                continue
            real_index += 1
            actual = _diagnostic_cost(attempt)
            estimated = _finite_nonnegative(attempt.get("estimated_cost"))
            if actual is not None:
                actual_total += actual
                charged = actual
                evidence = "provider_actual"
            elif estimated is not None:
                actual_known = False
                charged = estimated
                evidence = "estimated_only"
            else:
                actual_known = False
                charged = 0.0
                evidence = "unknown"
            conservative_total += charged
            entries.append(
                {
                    "seat": getattr(result, "seat_key", None),
                    "attempt_index": real_index,
                    "model": attempt.get("model"),
                    "actual_cost_usd": actual,
                    "estimated_cost_usd": estimated,
                    "conservative_cost_usd": charged,
                    "cost_evidence": evidence,
                    "error": attempt.get("error"),
                }
            )
    return {
        "entries": entries,
        "provider_actual_cost_usd": round(actual_total, 9),
        "conservative_cost_usd": round(conservative_total, 9),
        "all_actual_costs_known": actual_known and all(item["cost_evidence"] != "unknown" for item in entries),
    }


def _judge_attempts(run: RunConfig, judge_response: Mapping[str, Any]) -> list[dict[str, Any]]:
    path = Path(run.output_dir) / "judge-attempts.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        rows = []
    if isinstance(rows, list) and rows:
        return [dict(item) for item in rows if isinstance(item, Mapping)]
    info = diagnostics(judge_response)
    return [
        {
            "attempt_index": 1,
            "model": judge_response.get("model"),
            "response_diagnostics": info,
            "estimated_cost": None,
            "status": "success" if info.get("content_present") else "unknown",
        }
    ]


def judge_attempt_cost_evidence(run: RunConfig, judge_response: Mapping[str, Any]) -> Dict[str, Any]:
    entries = []
    actual_total = 0.0
    conservative_total = 0.0
    all_actual = True
    for index, attempt in enumerate(_judge_attempts(run, judge_response), 1):
        info = attempt.get("response_diagnostics") if isinstance(attempt.get("response_diagnostics"), Mapping) else {}
        actual = _finite_nonnegative(info.get("cost"))
        estimated = _finite_nonnegative(attempt.get("estimated_cost"))
        if actual is not None:
            charged = actual
            evidence = "provider_actual"
            actual_total += actual
        elif estimated is not None:
            charged = estimated
            evidence = "estimated_only"
            all_actual = False
        else:
            charged = 0.0
            evidence = "unknown"
            all_actual = False
        conservative_total += charged
        entries.append(
            {
                "attempt_index": int(attempt.get("attempt_index") or index),
                "model": attempt.get("model"),
                "provider": info.get("provider"),
                "actual_cost_usd": actual,
                "estimated_cost_usd": estimated,
                "conservative_cost_usd": charged,
                "cost_evidence": evidence,
                "status": attempt.get("status"),
                "error_code": attempt.get("error_code"),
                "error": attempt.get("error"),
            }
        )
    return {
        "entries": entries,
        "provider_actual_cost_usd": round(actual_total, 9),
        "conservative_cost_usd": round(conservative_total, 9),
        "all_actual_costs_known": all_actual and all(item["cost_evidence"] != "unknown" for item in entries),
    }


def apply_judge_output_contract(payload: Dict[str, Any], max_chinese_chars: int | None = None) -> Dict[str, Any]:
    """Require complete but compact delivery without a character or token ceiling."""
    del max_chinese_chars
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages or not isinstance(messages[0], dict):
        raise ExpertTeamError("Judge payload is missing the system message.")
    messages[0]["content"] = str(messages[0].get("content") or "") + (
        "最终报告必须完整覆盖原始任务的每项实质要求，但不要复述题目或逐篇总结专家。"
        "合并重复依据，优先使用紧凑表格、短段落、数字、条件、风险和否决原因；没有新增信息时立即停止。"
        "不得设置固定字符或Token上限，也不要求用满模型能力。"
    )
    return payload


def enforce_post_judge_actual_budget(
    run: RunConfig,
    results: Sequence[Any],
    judge_response: Mapping[str, Any],
) -> float:
    """Record every paid attempt; no monetary ceiling is enforced."""
    expert = expert_attempt_cost_evidence(results)
    judge = judge_attempt_cost_evidence(run, judge_response)
    provider_actual = float(expert["provider_actual_cost_usd"]) + float(judge["provider_actual_cost_usd"])
    conservative = float(expert["conservative_cost_usd"]) + float(judge["conservative_cost_usd"])
    statuses = [item["cost_evidence"] for item in expert["entries"] + judge["entries"]]
    status = "unknown" if "unknown" in statuses else "estimated_only" if "estimated_only" in statuses else "known"
    evidence = {
        "version": 2,
        "policy": "no-hard-monetary-ceiling",
        "status": status,
        "expert_attempts": expert,
        "judge_attempts": judge,
        "provider_actual_team_cost_usd": round(provider_actual, 9),
        "conservative_team_cost_usd": round(conservative, 9),
        "hard_cost_limit_usd": None,
        "message": "All paid calls are recorded; cost evidence affects disclosure/degradation only, not execution acceptance.",
    }
    run.output_dir.mkdir(parents=True, exist_ok=True)
    (run.output_dir / "cost-evidence.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return conservative
