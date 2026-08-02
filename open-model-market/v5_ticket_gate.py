#!/usr/bin/env python3
"""Fail-closed immutable admission gate for V5 production execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping

MODULE_DIR = Path(__file__).resolve().parent
if str(MODULE_DIR) not in sys.path:
    sys.path.insert(0, str(MODULE_DIR))

from v5_ticket_identity import task_fingerprint


class TicketGateError(RuntimeError):
    """Raised when admitted ticket evidence differs from execution expectations."""


def _load_object(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise TicketGateError(f"missing immutable admission file: {path.name}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise TicketGateError(f"invalid immutable admission JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise TicketGateError(f"immutable admission JSON must be an object: {path.name}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _same_number(left: Any, right: Any) -> bool:
    a = _finite_number(left)
    b = _finite_number(right)
    return a is not None and b is not None and abs(a - b) <= 1e-12


def _require(errors: list[str], condition: bool, message: str) -> None:
    if not condition:
        errors.append(message)


def validate_gate(
    root: Path,
    *,
    expected_calls: int,
    expected_recovery_calls: int,
    expected_quality_tier: str,
    expected_cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    ticket_path = root / "ticket.json"
    status_path = root / "ticket-status.json"
    task_path = root / "task.txt"
    ticket = _load_object(ticket_path)
    status = _load_object(status_path)
    if not task_path.is_file():
        raise TicketGateError("missing immutable admission file: task.txt")
    task_text = task_path.read_text(encoding="utf-8")
    errors: list[str] = []

    task = ticket.get("task") if isinstance(ticket.get("task"), Mapping) else {}
    budget = (
        ticket.get("approved_budget")
        if isinstance(ticket.get("approved_budget"), Mapping)
        else {}
    )
    task_id = str(ticket.get("task_id") or "")
    question = str(task.get("question") or "")
    ticket_calls = budget.get("calls")
    ticket_recovery = budget.get("maximum_recovery_calls")
    ticket_anomaly = budget.get("cost_anomaly_usd")
    status_calls = status.get("calls")
    status_recovery = status.get("maximum_recovery_calls")
    status_initial = status.get("maximum_initial_calls")
    mode = str(status.get("trigger_mode") or "")
    is_retry = status.get("is_retry") is True

    _require(errors, status.get("accepted") is True, "ticket-status.accepted must be true")
    _require(errors, str(ticket.get("route") or "") == "expert-team", "ticket.route must be expert-team")
    _require(errors, bool(task_id), "ticket.task_id is required")
    _require(errors, str(status.get("task_id") or "") == task_id, "ticket/status task_id mismatch")
    _require(errors, bool(question.strip()), "ticket.task.question is required")
    _require(errors, question in task_text, "task.txt does not contain the admitted question")
    _require(errors, bool(task_text.strip()), "task.txt must not be empty")

    fingerprint = task_fingerprint(ticket)
    _require(
        errors,
        str(status.get("task_fingerprint") or "") == fingerprint,
        "ticket/status task fingerprint mismatch",
    )

    _require(errors, _is_int(expected_calls) and 4 <= expected_calls <= 16, "expected calls must be between 4 and 16")
    _require(errors, _is_int(ticket_calls), "ticket approved_budget.calls must be an integer")
    _require(errors, _is_int(status_calls), "ticket-status.calls must be an integer")
    _require(errors, ticket_calls == expected_calls, "ticket calls differ from workflow expectation")
    _require(errors, status_calls == expected_calls, "status calls differ from workflow expectation")

    _require(
        errors,
        _is_int(expected_recovery_calls) and 0 <= expected_recovery_calls <= 4,
        "expected recovery calls must be between 0 and 4",
    )
    _require(errors, _is_int(ticket_recovery), "ticket maximum_recovery_calls must be an integer")
    _require(errors, _is_int(status_recovery), "status maximum_recovery_calls must be an integer")
    _require(errors, ticket_recovery == expected_recovery_calls, "ticket recovery differs from workflow expectation")
    _require(errors, status_recovery == expected_recovery_calls, "status recovery differs from workflow expectation")
    _require(
        errors,
        expected_recovery_calls <= expected_calls - 4,
        "recovery calls must leave three governance calls and one expert call",
    )
    expected_initial = expected_calls - 3 - expected_recovery_calls
    _require(
        errors,
        status_initial == expected_initial,
        "status initial calls must equal total minus three governance calls and recovery",
    )

    _require(
        errors,
        str(status.get("quality_tier") or "") == expected_quality_tier,
        "quality tier differs from workflow expectation",
    )
    _require(
        errors,
        expected_quality_tier == "value",
        "quality tier must remain value",
    )
    _require(
        errors,
        str(budget.get("cost_policy") or "") == "unbounded_with_anomaly_guard",
        "ticket cost policy must be unbounded_with_anomaly_guard",
    )
    _require(
        errors,
        str(status.get("cost_policy") or "") == "unbounded_with_anomaly_guard",
        "status cost policy must be unbounded_with_anomaly_guard",
    )

    if expected_cost_anomaly_usd is None:
        _require(errors, ticket_anomaly is None, "ticket cost anomaly must be absent")
        _require(errors, status.get("cost_anomaly_usd") is None, "status cost anomaly must be absent")
    else:
        _require(
            errors,
            _same_number(ticket_anomaly, expected_cost_anomaly_usd),
            "ticket cost anomaly differs from workflow expectation",
        )
        _require(
            errors,
            _same_number(status.get("cost_anomaly_usd"), expected_cost_anomaly_usd),
            "status cost anomaly differs from workflow expectation",
        )

    _require(errors, mode in {"run", "retry"}, "trigger mode must be run or retry")
    if mode == "run":
        _require(errors, not is_retry, "run trigger cannot be marked retry")
        _require(
            errors,
            str(status.get("execution_id") or "") == task_id,
            "run execution_id must equal task_id",
        )
    else:
        _require(errors, is_retry, "retry trigger must be marked retry")
        _require(errors, bool(str(status.get("retry_id") or "")), "retry_id is required")

    _require(
        errors,
        str(status.get("authoritative_trigger") or "") == "issue_comment.created",
        "authoritative trigger mismatch",
    )
    _require(
        errors,
        str(status.get("analysis_owner") or "") == "github-v5-gpt-claude-advisory-runtime",
        "analysis owner mismatch",
    )
    _require(
        errors,
        status.get("legacy_runtime_present") is False,
        "legacy runtime must be absent",
    )
    _require(
        errors,
        status.get("cross_task_history_used") is False,
        "cross-task history must be disabled",
    )
    _require(
        errors,
        status.get("private_output") is False,
        "private output is unsupported",
    )

    digest_path = root / "ticket-digests.json"
    digests = _load_object(digest_path)
    _require(
        errors,
        str(digests.get("ticket.json") or "") == _sha256(ticket_path),
        "ticket digest mismatch",
    )
    _require(
        errors,
        str(digests.get("ticket-status.json") or "") == _sha256(status_path),
        "ticket-status digest mismatch",
    )
    _require(
        errors,
        str(digests.get("task.txt") or "") == _sha256(task_path),
        "task digest mismatch",
    )

    if errors:
        raise TicketGateError("; ".join(errors))
    return {
        "status": "PASS",
        "task_id": task_id,
        "task_fingerprint": fingerprint,
        "maximum_total_calls": expected_calls,
        "governance_calls": 3,
        "maximum_initial_calls": expected_initial,
        "maximum_recovery_calls": expected_recovery_calls,
        "quality_tier": expected_quality_tier,
        "cost_anomaly_usd": expected_cost_anomaly_usd,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--expected-calls", type=int, required=True)
    parser.add_argument("--expected-recovery-calls", type=int, required=True)
    parser.add_argument("--expected-quality-tier", required=True)
    parser.add_argument("--expected-cost-anomaly-usd", default="")
    args = parser.parse_args()
    anomaly = (
        None
        if args.expected_cost_anomaly_usd == ""
        else float(args.expected_cost_anomaly_usd)
    )
    try:
        result = validate_gate(
            Path(args.output_dir),
            expected_calls=args.expected_calls,
            expected_recovery_calls=args.expected_recovery_calls,
            expected_quality_tier=args.expected_quality_tier,
            expected_cost_anomaly_usd=anomaly,
        )
    except TicketGateError as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
