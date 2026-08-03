"""Minimal deterministic task envelope for GPT-led expert decomposition.

This module does not classify domains, score complexity, invent atomic work,
assign capabilities, or choose models. It preserves only hard user contracts
and conservative capacity facts needed before GPT performs task decomposition.
"""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Sequence

import v5_task_delivery_contract as delivery_contract
from v5_task_constraints import compile_task_constraints

TASK_ENVELOPE_VERSION = "v5-minimal-task-envelope-1"
MAX_REQUIRED_OUTPUT_FIELDS = 128


def _required_context_tokens(
    task: str,
    *,
    minimum_context_length: int,
    maximum_completion_tokens: int,
) -> int:
    """Return a conservative capacity floor, not a task-complexity score."""
    task_tokens_upper_bound = max(1, len(str(task or "")))
    fixed_protocol_reserve = 8_192
    requested_output = max(256, int(maximum_completion_tokens))
    required = task_tokens_upper_bound + fixed_protocol_reserve + requested_output
    return max(int(minimum_context_length), required)


def explicit_delivery_contract(task: str) -> dict[str, Any]:
    """Extract only user-explicit output-format requirements."""
    base = {
        "required_fields": [],
        "machine_readable_required": False,
        "must_separate_fact_assumption_inference": True,
    }
    result = delivery_contract.apply_explicit_contract(
        str(task or ""),
        ("synthesis",),
        base,
    )
    required = [
        str(value).strip()
        for value in result.get("required_fields", [])
        if str(value).strip()
    ]
    if len(required) > MAX_REQUIRED_OUTPUT_FIELDS:
        raise ValueError("explicit output contract exceeds hard field limit")
    result["required_fields"] = required
    return result


def build_task_envelope(
    task: str,
    *,
    minimum_context_length: int,
    maximum_completion_tokens: int,
) -> dict[str, Any]:
    text = str(task or "").strip()
    if not text:
        raise ValueError("task is empty")
    constraints = compile_task_constraints(text)
    return {
        "schema_version": TASK_ENVELOPE_VERSION,
        "task_sha256": sha256(text.encode("utf-8")).hexdigest(),
        "task_characters": len(text),
        "required_context_tokens": _required_context_tokens(
            text,
            minimum_context_length=minimum_context_length,
            maximum_completion_tokens=maximum_completion_tokens,
        ),
        "explicit_delivery_contract": explicit_delivery_contract(text),
        "task_constraints": constraints.to_dict(),
        "decomposition_authority": "~openai/gpt-latest",
        "local_task_classification_used": False,
        "local_complexity_scoring_used": False,
        "local_atomic_work_generation_used": False,
        "local_resource_matrix_used": False,
    }


def work_output_contract(
    task: str,
    required_outputs: Sequence[str],
    *,
    final_node: bool,
) -> dict[str, Any]:
    """Build a hard delivery contract from GPT-declared outputs and user text."""
    fields: list[str] = []
    for raw in required_outputs:
        value = str(raw).strip()
        if value and value not in fields:
            fields.append(value)
    if len(fields) > MAX_REQUIRED_OUTPUT_FIELDS:
        raise ValueError("work output contract exceeds hard field limit")
    base: Mapping[str, Any] = {
        "required_fields": fields,
        "machine_readable_required": False,
        "must_separate_fact_assumption_inference": True,
        "final_delivery_node": bool(final_node),
    }
    operations = ("synthesis",) if final_node else ()
    result = delivery_contract.apply_explicit_contract(task, operations, base)
    result["final_delivery_node"] = bool(final_node)
    if not final_node:
        result["required_fields"] = fields
    return dict(result)
