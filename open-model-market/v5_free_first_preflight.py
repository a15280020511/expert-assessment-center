"""Fail-closed authorization for a paid acceptance attempt.

This policy can authorize one bounded paid acceptance only after deterministic
zero-call simulation and a zero-cost free-model Canary have passed. It can
never authorize a merge, a production ref move, or formal model identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "v5-free-first-preflight-1"
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class FreeFirstPreflightError(ValueError):
    """Raised for malformed free-first authorization evidence."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FreeFirstPreflightError(f"{field} must be an object")
    return value


def _integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FreeFirstPreflightError(f"{field} must be an integer")
    return value


def _zero_cost(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        raise FreeFirstPreflightError(f"{field} must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FreeFirstPreflightError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise FreeFirstPreflightError(f"{field} must be non-negative")
    return number == 0.0


def _false_value(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise FreeFirstPreflightError(f"{field} must be boolean")
    return value is False


def _check_simulation(value: Any) -> list[str]:
    reasons: list[str] = []
    row = _mapping(value, "simulation")
    if row.get("status") != "PASS":
        reasons.append("simulation-not-pass")
    if _integer(row.get("model_calls"), "simulation.model_calls") != 0:
        reasons.append("simulation-used-model-calls")
    if _integer(
        row.get("paid_model_calls"),
        "simulation.paid_model_calls",
    ) != 0:
        reasons.append("simulation-used-paid-calls")
    return reasons


def _check_free_canary(value: Any) -> list[str]:
    reasons: list[str] = []
    row = _mapping(value, "free_canary")
    if row.get("status") != "PASS":
        reasons.append("free-canary-not-pass")
    if _integer(row.get("model_requests"), "free_canary.model_requests") != 1:
        reasons.append("free-canary-request-count-invalid")
    if _integer(
        row.get("successful_model_calls"),
        "free_canary.successful_model_calls",
    ) != 1:
        reasons.append("free-canary-success-count-invalid")
    if _integer(
        row.get("paid_model_calls"),
        "free_canary.paid_model_calls",
    ) != 0:
        reasons.append("free-canary-used-paid-call")
    if not _zero_cost(
        row.get("actual_cost_usd"),
        "free_canary.actual_cost_usd",
    ):
        reasons.append("free-canary-positive-cost")
    requested = str(row.get("requested_model") or "")
    if requested != "openrouter/free" and not requested.endswith(":free"):
        reasons.append("free-canary-model-not-free")
    return reasons


def _check_shadow(value: Any) -> list[str]:
    reasons: list[str] = []
    row = _mapping(value, "shadow_governance")
    if row.get("status") != "PASS":
        reasons.append("shadow-governance-not-pass")
    if _integer(row.get("model_requests"), "shadow.model_requests") != 3:
        reasons.append("shadow-request-count-invalid")
    if _integer(
        row.get("successful_model_calls"),
        "shadow.successful_model_calls",
    ) != 3:
        reasons.append("shadow-success-count-invalid")
    if _integer(row.get("paid_model_calls"), "shadow.paid_model_calls") != 0:
        reasons.append("shadow-used-paid-call")
    if not _zero_cost(row.get("total_cost_usd"), "shadow.total_cost_usd"):
        reasons.append("shadow-positive-cost")
    if not _false_value(
        row.get("formal_model_identity_qualified"),
        "shadow.formal_model_identity_qualified",
    ):
        reasons.append("shadow-claimed-formal-model-identity")
    return reasons


def _category_passed(reasons: list[str], prefix: str) -> bool:
    return not any(reason.startswith(prefix) for reason in reasons)


def evaluate_free_first_preflight(
    receipt: Mapping[str, Any],
    *,
    expected_sha: str | None = None,
    require_shadow: bool = False,
) -> dict[str, Any]:
    """Return a deterministic authorization verdict without side effects."""
    row = _mapping(receipt, "receipt")
    reasons: list[str] = []
    if row.get("schema_version") != SCHEMA_VERSION:
        reasons.append("schema-version-invalid")

    target_sha = str(row.get("target_sha") or "")
    if _COMMIT_SHA_RE.fullmatch(target_sha) is None:
        reasons.append("target-sha-invalid")
    if expected_sha is not None and target_sha != expected_sha:
        reasons.append("target-sha-mismatch")

    reasons.extend(_check_simulation(row.get("simulation")))
    reasons.extend(_check_free_canary(row.get("free_canary")))

    shadow = row.get("shadow_governance")
    if require_shadow:
        if shadow is None:
            reasons.append("shadow-governance-required")
        else:
            reasons.extend(_check_shadow(shadow))
    elif shadow is not None:
        reasons.extend(_check_shadow(shadow))

    if not _false_value(
        row.get("paid_acceptance_triggered"),
        "paid_acceptance_triggered",
    ):
        reasons.append("paid-acceptance-already-triggered")
    if not _false_value(
        row.get("production_ref_moved"),
        "production_ref_moved",
    ):
        reasons.append("production-ref-already-moved")

    canonical = json.dumps(
        row,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=str,
    )
    authorized = not reasons
    return {
        "schema_version": "v5-free-first-preflight-verdict-1",
        "status": "PASS" if authorized else "FAIL",
        "target_sha": target_sha,
        "receipt_sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "zero_call_simulation_passed": _category_passed(
            reasons,
            "simulation-",
        ),
        "zero_cost_free_canary_passed": _category_passed(
            reasons,
            "free-canary-",
        ),
        "shadow_governance_required": bool(require_shadow),
        "paid_acceptance_allowed": authorized,
        "formal_model_identity_qualified": False,
        "merge_allowed": False,
        "production_promotion_allowed": False,
        "reasons": sorted(set(reasons)),
    }


def _load(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FreeFirstPreflightError(f"cannot read receipt: {exc}") from exc
    return _mapping(value, "receipt")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--expected-sha")
    parser.add_argument("--require-shadow", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verdict = evaluate_free_first_preflight(
        _load(args.receipt),
        expected_sha=args.expected_sha,
        require_shadow=args.require_shadow,
    )
    rendered = json.dumps(verdict, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if verdict["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
