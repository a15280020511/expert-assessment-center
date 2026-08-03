"""Fail-closed authorization for one exact paid acceptance attempt.

A paid acceptance can be authorized only after zero-call validation, one
zero-cost free canary, and an independently revalidated four-call free shadow
attestation for the same immutable target SHA. This policy never authorizes a
merge, a production ref move, or formal model identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "v5-free-first-preflight-2"
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


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise FreeFirstPreflightError(f"{field} must be boolean")
    return value


def _false_value(value: Any, field: str) -> bool:
    return _boolean(value, field) is False


def _true_value(value: Any, field: str) -> bool:
    return _boolean(value, field) is True


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
    if not _false_value(
        row.get("provider_fallback_allowed"),
        "free_canary.provider_fallback_allowed",
    ):
        reasons.append("free-canary-fallback-allowed")
    return reasons


def _check_independent_revalidation(value: Any) -> list[str]:
    reasons: list[str] = []
    row = _mapping(value, "shadow_attestation.independent_revalidation")
    if row.get("status") != "PASS":
        reasons.append("shadow-independent-revalidation-not-pass")
    if not _true_value(
        row.get("recomputed_from_primitive_evidence"),
        "shadow.independent.recomputed_from_primitive_evidence",
    ):
        reasons.append("shadow-not-recomputed-from-primitive-evidence")
    if not _false_value(
        row.get("paid_acceptance_verdict_used_as_source"),
        "shadow.independent.paid_acceptance_verdict_used_as_source",
    ):
        reasons.append("shadow-revalidation-used-paid-verdict")
    return reasons


def _check_shadow(value: Any, target_sha: str) -> list[str]:
    reasons: list[str] = []
    row = _mapping(value, "shadow_attestation")
    if row.get("status") != "PASS":
        reasons.append("shadow-attestation-not-pass")
    if str(row.get("target_sha") or "") != target_sha:
        reasons.append("shadow-target-sha-mismatch")
    if _integer(
        row.get("successful_free_model_calls"),
        "shadow.successful_free_model_calls",
    ) != 4:
        reasons.append("shadow-success-count-invalid")
    if _integer(row.get("paid_model_calls"), "shadow.paid_model_calls") != 0:
        reasons.append("shadow-used-paid-call")
    if not _zero_cost(row.get("actual_cost_usd"), "shadow.actual_cost_usd"):
        reasons.append("shadow-positive-cost")
    if not _true_value(
        row.get("expert_company_uniqueness_required"),
        "shadow.expert_company_uniqueness_required",
    ):
        reasons.append("shadow-expert-company-uniqueness-not-required")
    if not _true_value(
        row.get("independently_recomputed_from_primitive_evidence"),
        "shadow.independently_recomputed_from_primitive_evidence",
    ):
        reasons.append("shadow-independent-recomputation-missing")
    if not _false_value(
        row.get("formal_model_identity_qualified"),
        "shadow.formal_model_identity_qualified",
    ):
        reasons.append("shadow-claimed-formal-model-identity")
    if not _false_value(
        row.get("production_promotion_authorized"),
        "shadow.production_promotion_authorized",
    ):
        reasons.append("shadow-authorized-production-promotion")
    if not _false_value(
        row.get("production_ref_moved"),
        "shadow.production_ref_moved",
    ):
        reasons.append("shadow-moved-production-ref")
    reasons.extend(_check_independent_revalidation(row.get("independent_revalidation")))
    return reasons


def _category_passed(reasons: list[str], prefix: str) -> bool:
    return not any(reason.startswith(prefix) for reason in reasons)


def evaluate_free_first_preflight(
    receipt: Mapping[str, Any],
    *,
    expected_sha: str | None = None,
) -> dict[str, Any]:
    """Return a deterministic paid-acceptance authorization verdict."""
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
    shadow = row.get("shadow_attestation")
    if shadow is None:
        reasons.append("shadow-attestation-required")
    else:
        reasons.extend(_check_shadow(shadow, target_sha))

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
        "schema_version": "v5-free-first-preflight-verdict-2",
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
        "independently_revalidated_free_shadow_passed": _category_passed(
            reasons,
            "shadow-",
        ),
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
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    verdict = evaluate_free_first_preflight(
        _load(args.receipt),
        expected_sha=args.expected_sha,
    )
    rendered = json.dumps(verdict, ensure_ascii=False, indent=2) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if verdict["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
