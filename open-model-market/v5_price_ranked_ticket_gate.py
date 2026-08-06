#!/usr/bin/env python3
"""Fail-closed contract gate for admitted price-ranked tickets."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping


def _load(path: Path) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise RuntimeError(f"expected JSON object: {path}")
    return value


def _optional_float(value: str) -> float | None:
    text = str(value or "").strip()
    if text.lower() in {"", "none", "null"}:
        return None
    number = float(text)
    if not math.isfinite(number) or number < 0:
        raise ValueError("cost advisory must be finite and nonnegative")
    return number


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
    expected_cost = _optional_float(args.expected_cost_anomaly_usd)
    observed_cost = status.get("cost_anomaly_usd")
    if status.get("accepted") is not True:
        raise RuntimeError("ticket was not accepted")
    if status.get("runtime_version") != "v5-price-ranked-runtime-1":
        raise RuntimeError("ticket was not admitted for the price-ranked runtime")
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
        raise RuntimeError("governance model calls are not zero")
    if expected_cost is None:
        if observed_cost is not None:
            raise RuntimeError("unexpected cost advisory appeared after admission")
    elif not math.isclose(
        float(observed_cost), expected_cost, rel_tol=0, abs_tol=1e-12
    ):
        raise RuntimeError("cost advisory changed after admission")
    route = ticket.get("route")
    if route is not None and str(route) != "expert-team":
        raise RuntimeError("ticket route is not expert-team")
    if not (root / "task.txt").is_file():
        raise RuntimeError("canonical task projection is missing")
    print(json.dumps({"status": "PASS", "calls": args.expected_calls}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
