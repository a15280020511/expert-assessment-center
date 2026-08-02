from __future__ import annotations

from pathlib import Path

TARGET = Path("open-model-market/v5_execution_auditor.py")
START = "def _audit_requests("
END = "\ndef _audit_ledger("
REPLACEMENT = '''def _request_audit_values(
    evidence: AuditEvidence,
    calls: CallState,
) -> tuple[
    str,
    int,
    int,
    int,
    int,
    int,
    list[Any],
    Any,
]:
    audit = evidence.request_audit if isinstance(evidence.request_audit, Mapping) else {}
    status = str(audit.get("status") or "missing")
    expected = calls.total_calls
    captured = int(
        audit.get("request_count")
        or audit.get("captured_request_count")
        or 0
    )
    ceiling = int(audit.get("approved_total_call_ceiling") or 0)
    governance_count = int(audit.get("governance_request_count") or 0)
    expert_count = int(audit.get("expert_request_count") or 0)
    requests = audit.get("requests", [])
    if not isinstance(requests, list):
        requests = []
    return (
        status,
        expected,
        captured,
        ceiling,
        governance_count,
        expert_count,
        requests,
        audit.get("external_tools_allowed"),
    )


def _record_request_checks(
    checks: dict[str, Any],
    *,
    status: str,
    expected: int,
    captured: int,
    ceiling: int,
    governance_count: int,
    expert_count: int,
    requests: list[Any],
    tools_allowed: Any,
) -> None:
    checks.update(
        {
            "request_audit_status": status,
            "expected_request_count": expected,
            "captured_request_count": captured,
            "governance_request_count": governance_count,
            "expert_request_count": expert_count,
            "request_row_count": len(requests),
            "request_approved_total_call_ceiling": ceiling,
            "external_tools_allowed": tools_allowed,
        }
    )


def _append_request_failures(
    failures: list[str],
    *,
    status: str,
    expected: int,
    captured: int,
    ceiling: int,
    governance_count: int,
    expert_count: int,
    requests: list[Any],
    tools_allowed: Any,
    budget: BudgetState,
    calls: CallState,
) -> None:
    if status != "PASS":
        failures.append(f"V5 request audit status is {status}")
    if captured != expected:
        failures.append(
            "V5 request evidence is incomplete: "
            f"captured={captured}, expected={expected}"
        )
    if governance_count != budget.governance:
        failures.append(
            "governance request count differs from approved governance calls"
        )
    if expert_count != calls.expert_calls:
        failures.append("expert request count differs from expert attempts")
    if requests and len(requests) != captured:
        failures.append("request detail rows differ from captured request count")
    if captured > budget.total or ceiling != budget.total:
        failures.append(
            "request audit does not prove compliance with the approved total-call ceiling"
        )
    if tools_allowed is not False:
        failures.append("external-tool prohibition evidence is missing")


def _audit_requests(
    evidence: AuditEvidence,
    budget: BudgetState,
    calls: CallState,
    checks: dict[str, Any],
    failures: list[str],
) -> RequestState:
    (
        status,
        expected,
        captured,
        ceiling,
        governance_count,
        expert_count,
        requests,
        tools_allowed,
    ) = _request_audit_values(evidence, calls)
    _record_request_checks(
        checks,
        status=status,
        expected=expected,
        captured=captured,
        ceiling=ceiling,
        governance_count=governance_count,
        expert_count=expert_count,
        requests=requests,
        tools_allowed=tools_allowed,
    )
    _append_request_failures(
        failures,
        status=status,
        expected=expected,
        captured=captured,
        ceiling=ceiling,
        governance_count=governance_count,
        expert_count=expert_count,
        requests=requests,
        tools_allowed=tools_allowed,
        budget=budget,
        calls=calls,
    )
    return RequestState(status, expected, captured, ceiling)

'''

text = TARGET.read_text(encoding="utf-8")
if "def _request_audit_values(" in text:
    raise SystemExit("request audit stages already split")
start = text.find(START)
end = text.find(END, start)
if start < 0 or end < 0:
    raise SystemExit("expected request audit region is missing")
region = text[start:end]
required_markers = (
    "governance_request_count",
    "expert_request_count",
    "calls.total_calls",
    "external-tool prohibition evidence is missing",
)
if any(marker not in region for marker in required_markers):
    raise SystemExit("request audit region does not match the approved source")
TARGET.write_text(text[:start] + REPLACEMENT + text[end:], encoding="utf-8")
