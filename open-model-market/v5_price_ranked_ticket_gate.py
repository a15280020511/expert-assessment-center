#!/usr/bin/env python3
"""Fail-closed contract gate for governance-selected admitted tickets."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

from v5_governance_selection import SELECTION_AUTHORITY, validate_governance_selection


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _optional_float(value: str) -> float | None:
    text = str(value or "").strip()
    return float(text) if text else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--expected-calls", required=True, type=int)
    parser.add_argument("--expected-recovery-calls", required=True, type=int)
    parser.add_argument("--expected-cost-anomaly-usd", default="")
    args = parser.parse_args()
    root = Path(args.output_dir)
    status = _load(root / "ticket-status.json")
    ticket = _load(root / "ticket.json")
    plan = _load(root / "governance-selection.json")
    expected_cost = _optional_float(args.expected_cost_anomaly_usd)
    observed_cost = status.get("cost_anomaly_usd")
    if status.get("accepted") is not True:
        raise RuntimeError("ticket was not accepted")
    if status.get("runtime_version") != "v5-price-ranked-runtime-1":
        raise RuntimeError("ticket was not admitted for the governance-selected runtime")
    if status.get("selection_authority") != SELECTION_AUTHORITY:
        raise RuntimeError("admission did not bind governance selection authority")
    if status.get("governance_selection_validated") is not True:
        raise RuntimeError("governance selection was not validated during admission")
    if status.get("expert_center_model_selection") is not False:
        raise RuntimeError("expert-center model selection is not disabled")
    if status.get("expert_center_catalog_fetch") is not False:
        raise RuntimeError("expert-center catalog access is not disabled")
    if status.get("local_selection_fallback_allowed") is not False:
        raise RuntimeError("local model-selection fallback is not disabled")
    if int(status.get("calls") or 0) != args.expected_calls:
        raise RuntimeError("admitted total-call ceiling changed")
    if int(status.get("maximum_recovery_calls") or 0) != args.expected_recovery_calls:
        raise RuntimeError("admitted recovery reserve changed")
    if int(status.get("maximum_initial_calls") or 0) != (
        args.expected_calls - args.expected_recovery_calls
    ):
        raise RuntimeError("initial expert capacity is inconsistent")
    if args.expected_calls - args.expected_recovery_calls < 3:
        raise RuntimeError("ticket does not leave three initial experts")
    if status.get("claude_mechanism_enabled") is not False:
        raise RuntimeError("Claude mechanism is not disabled in admission evidence")
    if int(status.get("governance_model_calls") or 0) != 0:
        raise RuntimeError("governance inference calls are not zero")
    if expected_cost is None:
        if observed_cost is not None:
            raise RuntimeError("unexpected cost advisory appeared after admission")
    elif not math.isclose(
        float(observed_cost), expected_cost, rel_tol=0, abs_tol=1e-12
    ):
        raise RuntimeError("cost advisory changed after admission")
    if str(ticket.get("route") or "") != "expert-team":
        raise RuntimeError("ticket route is not expert-team")
    embedded = ticket.get("governance_selection")
    if not isinstance(embedded, Mapping) or dict(embedded) != dict(plan):
        raise RuntimeError("projected governance plan differs from admitted ticket")
    validation = validate_governance_selection(
        plan,
        approved_total_calls=args.expected_calls,
        approved_recovery_calls=args.expected_recovery_calls,
    )
    if str(status.get("selection_plan_sha256") or "") != str(plan.get("plan_sha256") or ""):
        raise RuntimeError("admission selection plan digest changed")
    task_path = root / "task.txt"
    if not task_path.is_file():
        raise RuntimeError("canonical task projection is missing")
    if task_path.read_text(encoding="utf-8").strip() != str(plan.get("task_text") or "").strip():
        raise RuntimeError("canonical task projection differs from governance plan")
    print(
        json.dumps(
            {
                "status": "PASS",
                "calls": args.expected_calls,
                "selection_authority": SELECTION_AUTHORITY,
                "selection_plan_sha256": plan["plan_sha256"],
                "selected_expert_count": validation["selected_expert_count"],
                "expert_center_selection_performed": False,
                "expert_center_catalog_fetch_performed": False,
                "local_fallback_used": False,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
