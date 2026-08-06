#!/usr/bin/env python3
"""Finite retry ledger for governance-selected expert execution.

Business retries are charged only when a retry has an in-flight/unknown outcome
or its terminal evidence records at least one model call. Explicit zero-call
failures are charged to a separate finite system-repair reserve.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

BUSINESS_RETRY_LIMIT = 2
SYSTEM_REPAIR_RETRY_LIMIT = 6

FORMAL_STATE_PREFIXES = (
    "EXECUTION_ACCEPTED",
    "EXECUTION_RETRY_ACCEPTED",
    "EXECUTION_COMPLETED",
    "EXECUTION_FAILED",
    "EXECUTION_REJECTED",
)
TERMINAL_STATES = {
    "EXECUTION_COMPLETED",
    "EXECUTION_FAILED",
    "EXECUTION_REJECTED",
}
MODEL_CALL_PATTERN = re.compile(
    r"(?:模型调用(?:次数|总数)?|model[_\s-]*calls?)\s*[:：=]\s*`?\s*(\d+)",
    re.IGNORECASE,
)
RETRY_ID_PATTERN = re.compile(r"RETRY_ID:\s*`?([A-Za-z0-9._-]{1,64})`?")


def _formal_state(body: str) -> str:
    stripped = str(body or "").lstrip()
    if not stripped.startswith("## "):
        return ""
    first_line = stripped.splitlines()[0][3:].strip()
    return first_line if first_line in FORMAL_STATE_PREFIXES else ""


def _retry_id(body: str) -> str:
    match = RETRY_ID_PATTERN.search(str(body or ""))
    return match.group(1) if match else ""


def _model_calls(body: str) -> int | None:
    match = MODEL_CALL_PATTERN.search(str(body or ""))
    return int(match.group(1)) if match else None


def execution_state(comments: Iterable[str]) -> dict[str, Any]:
    bodies = [str(body or "") for body in comments]
    attempts: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for body in bodies:
        formal = _formal_state(body)
        if formal == "EXECUTION_RETRY_ACCEPTED":
            current = {
                "retry_id": _retry_id(body),
                "terminal_state": "",
                "model_calls": None,
            }
            attempts.append(current)
            continue
        if formal in TERMINAL_STATES and current is not None:
            current["terminal_state"] = formal
            current["model_calls"] = _model_calls(body)
            current = None

    business_retry_count = 0
    system_repair_retry_count = 0
    in_flight_retry_count = 0
    for attempt in attempts:
        terminal = str(attempt.get("terminal_state") or "")
        calls = attempt.get("model_calls")
        if not terminal:
            in_flight_retry_count += 1
            business_retry_count += 1
        elif calls == 0:
            system_repair_retry_count += 1
        else:
            # Unknown terminal call counts are charged conservatively as business use.
            business_retry_count += 1

    return {
        "accepted": any(
            _formal_state(body) in {"EXECUTION_ACCEPTED", "EXECUTION_RETRY_ACCEPTED"}
            for body in bodies
        ),
        "completed": any(
            _formal_state(body) == "EXECUTION_COMPLETED" for body in bodies
        ),
        "failed": any(_formal_state(body) == "EXECUTION_FAILED" for body in bodies),
        "rejected": any(
            _formal_state(body) == "EXECUTION_REJECTED" for body in bodies
        ),
        "retry_count": business_retry_count,
        "business_retry_count": business_retry_count,
        "system_repair_retry_count": system_repair_retry_count,
        "in_flight_retry_count": in_flight_retry_count,
        "total_accepted_retry_count": len(attempts),
        "retry_ids": {
            str(attempt.get("retry_id") or "")
            for attempt in attempts
            if str(attempt.get("retry_id") or "")
        },
    }


def current_issue_submission_reason(
    *,
    is_retry: bool,
    issue_state: str,
    execution: Mapping[str, Any],
    retry_id: str,
) -> str:
    if not is_retry:
        if issue_state == "closed":
            return "Issue is closed; reopen it before first execution"
        if execution.get("accepted"):
            return "an execution already exists for this Issue; use controlled retry"
        return ""

    if execution.get("completed"):
        return "completed executions cannot be retried"
    if issue_state != "open":
        return "Issue must be reopened before a controlled retry"
    if int(execution.get("in_flight_retry_count") or 0) > 0:
        return "a controlled retry is already in progress"
    if not execution.get("failed") and not execution.get("rejected"):
        return "retry requires a prior formal failed or rejected execution state"
    if int(execution.get("business_retry_count") or 0) >= BUSINESS_RETRY_LIMIT:
        return f"maximum {BUSINESS_RETRY_LIMIT} business retries are allowed per Issue"
    if int(execution.get("system_repair_retry_count") or 0) >= SYSTEM_REPAIR_RETRY_LIMIT:
        return (
            f"maximum {SYSTEM_REPAIR_RETRY_LIMIT} zero-call system repair retries "
            "are allowed per Issue"
        )
    if retry_id in set(execution.get("retry_ids") or set()):
        return "retry_id has already been used for this Issue"
    return ""


def patch(legacy: Any) -> None:
    """Install the ledger into the legacy admission module exactly once."""
    if getattr(legacy, "_governance_retry_state_patched", False):
        return
    legacy._execution_state = execution_state
    legacy._current_issue_submission_reason = current_issue_submission_reason
    legacy.MAXIMUM_RETRIES_PER_ISSUE = BUSINESS_RETRY_LIMIT
    legacy.GOVERNANCE_SYSTEM_REPAIR_RETRY_LIMIT = SYSTEM_REPAIR_RETRY_LIMIT
    legacy._governance_retry_state_patched = True
