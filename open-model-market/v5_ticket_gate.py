#!/usr/bin/env python3
"""Fail-closed immutable admission gate for V5 production execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

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
    _require(errors, _is_int(ticket_recovery), "ticket recovery calls must be an integer")
    _require(errors, _is_int(status_recovery), "status recovery calls must be an integer")
    _require(errors, ticket_recovery == expected_recovery_calls, "ticket recovery calls differ from workflow expectation")
    _require(errors, status_recovery == expected_recovery_calls, "status recovery calls differ from workflow expectation")
    governance_calls = 3
    _require(
        errors,
        expected_recovery_calls < expected_calls - governance_calls,
        "recovery pool must leave at least one expert initial call after governance",
    )
    _require(
        errors,
        status_initial == expected_calls - governance_calls - expected_recovery_calls,
        "status initial-call limit is inconsistent",
    )
    _require(
        errors,
        status.get("maximum_replacements") == expected_recovery_calls,
        "status replacement limit differs from recovery pool",
    )

    _require(
        errors,
        str(ticket.get("quality_tier") or "") == expected_quality_tier,
        "ticket quality tier differs from workflow expectation",
    )
    _require(
        errors,
        str(status.get("quality_tier") or "") == expected_quality_tier,
        "status quality tier differs from workflow expectation",
    )
    _require(
        errors,
        expected_quality_tier in {"budget", "value", "quality"},
        "unsupported workflow quality tier",
    )

    if expected_cost_anomaly_usd is None:
        _require(errors, ticket_anomaly is None, "ticket anomaly guard must be absent")
        _require(errors, status.get("cost_anomaly_usd") is None, "status anomaly guard must be absent")
    else:
        _require(
            errors,
            expected_cost_anomaly_usd > 0 and math.isfinite(expected_cost_anomaly_usd),
            "workflow anomaly guard must be finite and positive",
        )
        _require(errors, _same_number(ticket_anomaly, expected_cost_anomaly_usd), "ticket anomaly guard differs from workflow expectation")
        _require(errors, _same_number(status.get("cost_anomaly_usd"), expected_cost_anomaly_usd), "status anomaly guard differs from workflow expectation")
        _require(errors, _same_number(status.get("max_cost_usd"), expected_cost_anomaly_usd), "status max_cost_usd differs from anomaly guard")

    _require(
        errors,
        str(budget.get("cost_policy") or "") == "unbounded_with_anomaly_guard",
        "ticket cost policy mismatch",
    )
    _require(
        errors,
        str(status.get("cost_policy") or "") == "unbounded_with_anomaly_guard",
        "status cost policy mismatch",
    )
    _require(errors, ticket.get("private_output") is False, "private output is unsupported")
    _require(errors, status.get("private_output") is False, "status private output must be false")
    _require(errors, str(status.get("runtime_version") or "") == "v5-native-runtime-1", "runtime version mismatch")
    _require(errors, str(status.get("authoritative_trigger") or "") == "issue_comment.created", "authoritative trigger mismatch")
    _require(errors, str(status.get("fallback_policy") or "") == "disabled-fail-closed", "fallback policy mismatch")
    _require(errors, status.get("legacy_runtime_present") is False, "legacy runtime must be absent")
    _require(errors, status.get("cross_task_history_used") is False, "cross-task history must be disabled")

    if mode == "run":
        _require(errors, not is_retry, "run mode cannot be marked as retry")
        _require(errors, str(status.get("execution_id") or "") == task_id, "run execution_id must equal task_id")
        _require(errors, not str(status.get("retry_id") or ""), "run mode retry_id must be empty")
    elif mode == "retry":
        _require(errors, is_retry, "retry mode must be marked as retry")
        _require(errors, bool(str(status.get("retry_id") or "")), "retry mode requires retry_id")
        _require(errors, not str(status.get("execution_id") or ""), "retry mode execution_id must be empty")
    else:
        errors.append("trigger_mode must be run or retry")

    if errors:
        raise TicketGateError("; ".join(errors))

    evidence = {
        name: {
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
        for name, path in (
            ("ticket.json", ticket_path),
            ("ticket-status.json", status_path),
            ("task.txt", task_path),
        )
    }
    return {
        "schema_version": "v5-ticket-gate-1",
        "status": "PASS",
        "task_id": task_id,
        "task_fingerprint": fingerprint,
        "trigger_mode": mode,
        "runtime_version": "v5-native-runtime-1",
        "validated_budget": {
            "maximum_total_calls": expected_calls,
            "maximum_initial_calls": expected_calls - expected_recovery_calls,
            "maximum_recovery_calls": expected_recovery_calls,
            "cost_anomaly_usd": expected_cost_anomaly_usd,
            "quality_tier": expected_quality_tier,
        },
        "immutable_admission_evidence": evidence,
        "model_calls_performed": 0,
        "mutation_performed": False,
    }


def _write_result(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def run_gate(
    root: Path,
    *,
    expected_calls: int,
    expected_recovery_calls: int,
    expected_quality_tier: str,
    expected_cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    output = root / "ticket-gate.json"
    try:
        result = validate_gate(
            root,
            expected_calls=expected_calls,
            expected_recovery_calls=expected_recovery_calls,
            expected_quality_tier=expected_quality_tier,
            expected_cost_anomaly_usd=expected_cost_anomaly_usd,
        )
    except TicketGateError as exc:
        result = {
            "schema_version": "v5-ticket-gate-1",
            "status": "FAIL",
            "errors": [part.strip() for part in str(exc).split(";") if part.strip()],
            "model_calls_performed": 0,
            "mutation_performed": False,
        }
        _write_result(output, result)
        raise
    _write_result(output, result)
    return result


def _optional_float(value: str) -> float | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected cost anomaly must be numeric") from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError("expected cost anomaly must be finite")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--expected-calls", required=True, type=int)
    parser.add_argument("--expected-recovery-calls", required=True, type=int)
    parser.add_argument("--expected-quality-tier", required=True)
    parser.add_argument("--expected-cost-anomaly-usd", default="", type=_optional_float)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_gate(
            Path(args.output_dir),
            expected_calls=args.expected_calls,
            expected_recovery_calls=args.expected_recovery_calls,
            expected_quality_tier=args.expected_quality_tier,
            expected_cost_anomaly_usd=args.expected_cost_anomaly_usd,
        )
    except TicketGateError as exc:
        print(f"V5_TICKET_GATE_FAIL: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
