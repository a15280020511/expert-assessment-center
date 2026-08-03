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
        raise TicketGateError(
            f"immutable admission JSON must be an object: {path.name}"
        )
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


def _admission_paths(root: Path) -> tuple[Path, Path, Path, dict[str, Any], dict[str, Any], str]:
    ticket_path = root / "ticket.json"
    status_path = root / "ticket-status.json"
    task_path = root / "task.txt"
    ticket = _load_object(ticket_path)
    status = _load_object(status_path)
    if not task_path.is_file():
        raise TicketGateError("missing immutable admission file: task.txt")
    return ticket_path, status_path, task_path, ticket, status, task_path.read_text(encoding="utf-8")


def _ticket_parts(ticket: Mapping[str, Any]) -> tuple[Mapping[str, Any], Mapping[str, Any], str, str]:
    task = ticket.get("task") if isinstance(ticket.get("task"), Mapping) else {}
    budget = ticket.get("approved_budget") if isinstance(ticket.get("approved_budget"), Mapping) else {}
    return task, budget, str(ticket.get("task_id") or ""), str(task.get("question") or "")


def _identity_errors(
    ticket: Mapping[str, Any],
    status: Mapping[str, Any],
    task_text: str,
    task_id: str,
    question: str,
) -> tuple[list[str], str]:
    errors: list[str] = []
    checks = (
        (status.get("accepted") is True, "ticket-status.accepted must be true"),
        (str(ticket.get("route") or "") == "expert-team", "ticket.route must be expert-team"),
        (bool(task_id), "ticket.task_id is required"),
        (str(status.get("task_id") or "") == task_id, "ticket/status task_id mismatch"),
        (bool(question.strip()), "ticket.task.question is required"),
        (question in task_text, "task.txt does not contain the admitted question"),
        (bool(task_text.strip()), "task.txt must not be empty"),
    )
    for condition, message in checks:
        _require(errors, condition, message)
    fingerprint = task_fingerprint(ticket)
    _require(
        errors,
        str(status.get("task_fingerprint") or "") == fingerprint,
        "ticket/status task fingerprint mismatch",
    )
    return errors, fingerprint


def _call_budget_errors(
    budget: Mapping[str, Any],
    status: Mapping[str, Any],
    expected_calls: int,
    expected_recovery_calls: int,
) -> tuple[list[str], int]:
    errors: list[str] = []
    ticket_calls = budget.get("calls")
    ticket_recovery = budget.get("maximum_recovery_calls")
    status_calls = status.get("calls")
    status_recovery = status.get("maximum_recovery_calls")
    checks = (
        (_is_int(expected_calls) and 4 <= expected_calls <= 16, "expected calls must be between 4 and 16"),
        (_is_int(ticket_calls), "ticket approved_budget.calls must be an integer"),
        (_is_int(status_calls), "ticket-status.calls must be an integer"),
        (ticket_calls == expected_calls, "ticket calls differ from workflow expectation"),
        (status_calls == expected_calls, "status calls differ from workflow expectation"),
        (_is_int(expected_recovery_calls) and 0 <= expected_recovery_calls <= 4, "expected recovery calls must be between 0 and 4"),
        (_is_int(ticket_recovery), "ticket maximum_recovery_calls must be an integer"),
        (_is_int(status_recovery), "status maximum_recovery_calls must be an integer"),
        (ticket_recovery == expected_recovery_calls, "ticket recovery differs from workflow expectation"),
        (status_recovery == expected_recovery_calls, "status recovery differs from workflow expectation"),
        (expected_recovery_calls <= expected_calls - 4, "recovery calls must leave three governance calls and one expert call"),
    )
    for condition, message in checks:
        _require(errors, condition, message)
    expected_initial = expected_calls - 3 - expected_recovery_calls
    _require(
        errors,
        status.get("maximum_initial_calls") == expected_initial,
        "status initial calls must equal total minus three governance calls and recovery",
    )
    _require(
        errors,
        status.get("maximum_replacements") == expected_recovery_calls,
        "status replacement limit differs from recovery pool",
    )
    return errors, expected_initial


def _cost_policy_errors(
    budget: Mapping[str, Any],
    status: Mapping[str, Any],
    expected_cost_anomaly_usd: float | None,
) -> list[str]:
    errors: list[str] = []
    accepted = {
        "prompt_led_soft_governance",
        "unbounded_with_anomaly_guard",
    }
    _require(
        errors,
        str(budget.get("cost_policy") or "") in accepted,
        "ticket cost policy must be prompt-led soft governance",
    )
    _require(
        errors,
        str(status.get("cost_policy") or "") in accepted,
        "status cost policy must be prompt-led soft governance",
    )
    ticket_advisory = budget.get("cost_anomaly_usd")
    if expected_cost_anomaly_usd is None:
        _require(errors, ticket_advisory is None, "ticket cost advisory must be absent")
        _require(errors, status.get("cost_anomaly_usd") is None, "status cost advisory must be absent")
        return errors
    checks = (
        (expected_cost_anomaly_usd > 0 and math.isfinite(expected_cost_anomaly_usd), "workflow cost advisory must be finite and positive"),
        (_same_number(ticket_advisory, expected_cost_anomaly_usd), "ticket cost advisory differs from workflow expectation"),
        (_same_number(status.get("cost_anomaly_usd"), expected_cost_anomaly_usd), "status cost advisory differs from workflow expectation"),
        (status.get("cost_threshold_can_stop_execution") is not True, "cost advisory must not stop execution"),
    )
    for condition, message in checks:
        _require(errors, condition, message)
    return errors


def _runtime_policy_errors(ticket: Mapping[str, Any], status: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    checks = (
        (ticket.get("private_output") is False, "private output is unsupported"),
        (status.get("private_output") is False, "status private output must be false"),
        (str(status.get("analysis_owner") or "") == "github-v5-gpt-claude-expert-graph", "analysis owner mismatch"),
        (str(status.get("runtime_version") or "") == "v5-native-runtime-1", "runtime version mismatch"),
        (str(status.get("authoritative_trigger") or "") == "issue_comment.created", "authoritative trigger mismatch"),
        (str(status.get("fallback_policy") or "") == "disabled-fail-closed", "fallback policy mismatch"),
        (status.get("legacy_runtime_present") is False, "legacy runtime must be absent"),
        (status.get("cross_task_history_used") is False, "cross-task history must be disabled"),
    )
    for condition, message in checks:
        _require(errors, condition, message)
    return errors


def _trigger_errors(status: Mapping[str, Any], task_id: str) -> tuple[list[str], str]:
    errors: list[str] = []
    mode = str(status.get("trigger_mode") or "")
    is_retry = status.get("is_retry") is True
    if mode == "run":
        checks = (
            (not is_retry, "run mode cannot be marked as retry"),
            (str(status.get("execution_id") or "") == task_id, "run execution_id must equal task_id"),
            (not str(status.get("retry_id") or ""), "run mode retry_id must be empty"),
        )
    elif mode == "retry":
        checks = (
            (is_retry, "retry mode must be marked as retry"),
            (bool(str(status.get("retry_id") or "")), "retry mode requires retry_id"),
            (not str(status.get("execution_id") or ""), "retry mode execution_id must be empty"),
        )
    else:
        return ["trigger_mode must be run or retry"], mode
    for condition, message in checks:
        _require(errors, condition, message)
    return errors, mode


def _admission_evidence(paths: tuple[Path, Path, Path]) -> dict[str, Any]:
    return {
        path.name: {"sha256": _sha256(path), "size_bytes": path.stat().st_size}
        for path in paths
    }


def _gate_result(
    *,
    task_id: str,
    fingerprint: str,
    mode: str,
    expected_calls: int,
    expected_initial: int,
    expected_recovery_calls: int,
    expected_cost_anomaly_usd: float | None,
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "v5-ticket-gate-2",
        "status": "PASS",
        "task_id": task_id,
        "task_fingerprint": fingerprint,
        "trigger_mode": mode,
        "runtime_version": "v5-native-runtime-1",
        "validated_budget": {
            "maximum_total_calls": expected_calls,
            "governance_calls": 3,
            "maximum_initial_calls": expected_initial,
            "maximum_recovery_calls": expected_recovery_calls,
            "cost_anomaly_usd": expected_cost_anomaly_usd,
        },
        "immutable_admission_evidence": dict(evidence),
        "model_calls_performed": 0,
        "mutation_performed": False,
    }


def validate_gate(
    root: Path,
    *,
    expected_calls: int,
    expected_recovery_calls: int,
    expected_cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    ticket_path, status_path, task_path, ticket, status, task_text = _admission_paths(root)
    _, budget, task_id, question = _ticket_parts(ticket)
    errors, fingerprint = _identity_errors(ticket, status, task_text, task_id, question)
    budget_errors, expected_initial = _call_budget_errors(
        budget, status, expected_calls, expected_recovery_calls
    )
    errors.extend(budget_errors)
    errors.extend(_cost_policy_errors(budget, status, expected_cost_anomaly_usd))
    errors.extend(_runtime_policy_errors(ticket, status))
    trigger_errors, mode = _trigger_errors(status, task_id)
    errors.extend(trigger_errors)
    if errors:
        raise TicketGateError("; ".join(errors))
    return _gate_result(
        task_id=task_id,
        fingerprint=fingerprint,
        mode=mode,
        expected_calls=expected_calls,
        expected_initial=expected_initial,
        expected_recovery_calls=expected_recovery_calls,
        expected_cost_anomaly_usd=expected_cost_anomaly_usd,
        evidence=_admission_evidence((ticket_path, status_path, task_path)),
    )


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
    expected_cost_anomaly_usd: float | None,
) -> dict[str, Any]:
    output = root / "ticket-gate.json"
    try:
        result = validate_gate(
            root,
            expected_calls=expected_calls,
            expected_recovery_calls=expected_recovery_calls,
            expected_cost_anomaly_usd=expected_cost_anomaly_usd,
        )
    except TicketGateError as exc:
        result = {
            "schema_version": "v5-ticket-gate-2",
            "status": "FAIL",
            "errors": [
                part.strip()
                for part in str(exc).split(";")
                if part.strip()
            ],
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
        raise argparse.ArgumentTypeError(
            "expected cost anomaly must be numeric"
        ) from exc
    if not math.isfinite(number):
        raise argparse.ArgumentTypeError(
            "expected cost anomaly must be finite"
        )
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="ticket-artifacts")
    parser.add_argument("--expected-calls", required=True, type=int)
    parser.add_argument("--expected-recovery-calls", required=True, type=int)
    parser.add_argument(
        "--expected-cost-anomaly-usd",
        default="",
        type=_optional_float,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_gate(
            Path(args.output_dir),
            expected_calls=args.expected_calls,
            expected_recovery_calls=args.expected_recovery_calls,
            expected_cost_anomaly_usd=args.expected_cost_anomaly_usd,
        )
    except TicketGateError as exc:
        print(f"V5_TICKET_GATE_FAIL: {exc}")
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
