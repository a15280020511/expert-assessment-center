"""Fail-closed V5 cutover classification and benchmark allowance preservation."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import v5_executor as executor

_INSTALLED = False


def full_success_for_cutover(result: Mapping[str, Any], answer: str) -> bool:
    """Only complete, non-degraded delivery may count toward V3 replacement."""
    degradation = result.get("degradation") if isinstance(result.get("degradation"), Mapping) else {}
    return bool(
        result.get("status") == "success"
        and result.get("quality_status") == "full_success"
        and result.get("completion_mode") == "full"
        and not bool(degradation.get("used"))
        and len(str(answer or "").strip()) >= 160
    )


def _install_strict_v5_benchmark_classification() -> None:
    base = sys.modules.get("v5_live_benchmark")
    if base is None or getattr(base, "_v5_cutover_readiness_installed", False):
        return
    original = base._v5_strategy

    def classified_v5_strategy(*args: Any, **kwargs: Any) -> Any:
        outcome, market = original(*args, **kwargs)
        root = args[1] if len(args) > 1 else kwargs.get("root")
        summary: Mapping[str, Any] = {}
        if root is not None:
            path = Path(root) / "v5-execution-summary.json"
            if path.exists():
                try:
                    loaded = json.loads(path.read_text(encoding="utf-8"))
                    summary = loaded if isinstance(loaded, Mapping) else {}
                except (OSError, json.JSONDecodeError):
                    summary = {}
        answer = str(outcome.answer or "")
        complete = full_success_for_cutover(summary, answer)
        artifacts = dict(outcome.artifacts or {})
        artifacts.update({
            "completion_mode": summary.get("completion_mode"),
            "quality_status": summary.get("quality_status"),
            "degradation": dict(summary.get("degradation") or {})
            if isinstance(summary.get("degradation"), Mapping)
            else {},
            "production_cutover_complete_success": complete,
        })
        outcome.artifacts = artifacts
        if outcome.status == "success" and not complete:
            outcome.status = "failed"
            outcome.error = "V5 produced a usable degraded delivery, but production cutover requires full_success."
        return outcome, market

    base._v5_strategy = classified_v5_strategy
    base._v5_cutover_readiness_installed = True


def _allowance_field(supported_parameters: Sequence[Any]) -> str:
    supported = {str(value).casefold() for value in supported_parameters}
    return "max_completion_tokens" if "max_completion_tokens" in supported else "max_tokens"


def _existing_output_limit(payload: Mapping[str, Any]) -> int | None:
    values: list[int] = []
    for key in ("max_tokens", "max_completion_tokens"):
        try:
            if payload.get(key) not in {None, ""}:
                values.append(max(1, int(payload[key])))
        except (TypeError, ValueError):
            continue
    return min(values) if values else None


def install_benchmark_output_allowance() -> None:
    """Make benchmark allowance a ceiling without erasing lower node limits."""
    hardened = sys.modules.get("v5_live_benchmark_hardened")
    base = sys.modules.get("v5_live_benchmark")
    if hardened is None or base is None:
        return

    original_safe = base._safe_payload

    def allowed_safe(endpoint: Mapping[str, Any], system: str, user: str) -> dict[str, Any]:
        payload = original_safe(endpoint, system, user)
        existing = _existing_output_limit(payload)
        field = _allowance_field(endpoint.get("supported_parameters", []))
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
        payload[field] = min(int(hardened.ALLOWANCE), existing or int(hardened.ALLOWANCE))
        return payload

    base._safe_payload = allowed_safe
    original_node_payload = executor.build_node_payload

    def allowed_node_payload(
        node: Any,
        original_task: str,
        upstream: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        payload = original_node_payload(node, original_task, upstream)
        existing = _existing_output_limit(payload)
        supported = (
            node.parameter_profile.get("supported_parameters", [])
            if isinstance(node.parameter_profile, Mapping)
            else []
        )
        field = _allowance_field(supported)
        payload.pop("max_tokens", None)
        payload.pop("max_completion_tokens", None)
        payload[field] = min(int(hardened.ALLOWANCE), existing or int(hardened.ALLOWANCE))
        return payload

    executor.build_node_payload = allowed_node_payload
    original_execute = base.execute_v5_graph

    def annotate(output_dir: str | Path | None) -> None:
        if output_dir is None:
            return
        audit_path = Path(output_dir) / "v5-request-audit.json"
        if not audit_path.exists():
            return
        try:
            audit = json.loads(audit_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        requests = audit.get("requests") if isinstance(audit.get("requests"), list) else []
        fields: list[str] = []
        limits: list[int] = []
        valid = bool(requests)
        for row in requests:
            if not isinstance(row, Mapping):
                valid = False
                continue
            field = (
                "max_completion_tokens"
                if row.get("max_completion_tokens") not in {None, ""}
                else "max_tokens"
                if row.get("max_tokens") not in {None, ""}
                else ""
            )
            if not field:
                valid = False
                continue
            try:
                value = int(row[field])
            except (TypeError, ValueError):
                valid = False
                continue
            valid = valid and 0 < value <= int(hardened.ALLOWANCE)
            fields.append(field)
            limits.append(value)
        audit.update({
            "benchmark_output_allowance_tokens": int(hardened.ALLOWANCE),
            "benchmark_output_allowance_parameters": sorted(set(fields)),
            "benchmark_output_allowance_policy": (
                "maximum-permitted-not-required; lower-dynamic-node-limit-preserved"
            ),
            "benchmark_output_allowance_consistent": valid,
            "benchmark_dynamic_output_limits": limits,
            "artificial_token_ceiling_sent": False,
            "production_policy_changed": False,
        })
        audit_path.write_text(
            json.dumps(audit, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    hardened._annotate_v5_audit = annotate

    def allowed_execute(*args: Any, **kwargs: Any) -> Any:
        output_dir = kwargs.get("output_dir")
        try:
            return original_execute(*args, **kwargs)
        finally:
            annotate(output_dir)

    base.execute_v5_graph = allowed_execute


def _patch_loaded_benchmark_hardening() -> None:
    hardened = sys.modules.get("v5_live_benchmark_hardened")
    if hardened is not None:
        hardened._install_output_allowance = install_benchmark_output_allowance


def install() -> None:
    global _INSTALLED
    _install_strict_v5_benchmark_classification()
    _patch_loaded_benchmark_hardening()
    _INSTALLED = True
