"""Deterministically repair one-character typos in system-generated generic H2s.

Production run gov-316-expert / Expert #416 reached a complete zero-cost answer
whose only quality-gate failure was the heading typo ``不确定性与反流`` instead of
the system-generated generic heading ``不确定性与反例``.  The user had not requested
those exact H2 strings; they came from the internal generic delivery contract.

This layer keeps the exact Markdown gate strict by repairing only an unambiguous,
one-character edit in a generic system-generated heading *before* validation.  It
never applies to an explicit user Markdown contract, never invents body content,
and refuses ambiguous, multi-character or empty-section repairs.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

import v5_deterministic_answer_normalization as normalization
from text_normalization import normalize_heading_key

_H2_RE = re.compile(r"^\s{0,3}##\s+(.+?)\s*#*\s*$")
_ORIGINAL_CANONICALIZE_H2 = normalization._canonicalize_h2


def _edit_distance_at_most_one(left: str, right: str) -> bool:
    """Return true only for exact strings or one insertion/deletion/substitution."""
    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        differences = sum(a != b for a, b in zip(left, right))
        return differences == 1
    short, long = (left, right) if len(left) < len(right) else (right, left)
    i = j = differences = 0
    while i < len(short) and j < len(long):
        if short[i] == long[j]:
            i += 1
            j += 1
            continue
        differences += 1
        if differences > 1:
            return False
        j += 1
    differences += int(j < len(long))
    return differences == 1


def _generic_required_fields(output_contract: Mapping[str, Any]) -> list[str]:
    if output_contract.get("machine_readable_required"):
        return []
    if output_contract.get("explicit_markdown_contract"):
        return []
    if output_contract.get("explicit_user_contract"):
        return []
    exact = output_contract.get("exact_markdown_headings")
    if isinstance(exact, Sequence) and not isinstance(exact, (str, bytes)) and exact:
        return []
    values = output_contract.get("required_fields")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _repair_generic_h2(
    answer: str,
    output_contract: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Repair a unique one-character generic H2 typo and canonicalize order."""
    original_answer, original_audit = _ORIGINAL_CANONICALIZE_H2(
        answer,
        output_contract,
    )
    if original_audit.get("h2_reorder_blocked_reason") != "heading-set-not-exact":
        original_audit.setdefault("generic_h2_typo_corrections", [])
        original_audit.setdefault("generic_h2_typo_repair_applied", False)
        return original_answer, original_audit

    required = _generic_required_fields(output_contract)
    if not required:
        original_audit.setdefault("generic_h2_typo_corrections", [])
        original_audit.setdefault("generic_h2_typo_repair_applied", False)
        return original_answer, original_audit

    lines = str(answer or "").splitlines()
    matches: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = _H2_RE.match(line)
        if match:
            matches.append((index, match.group(1).strip()))
    if len(matches) != len(required):
        original_audit["generic_h2_typo_repair_applied"] = False
        original_audit["generic_h2_typo_corrections"] = []
        return original_answer, original_audit
    if matches and any(line.strip() for line in lines[: matches[0][0]]):
        original_audit["generic_h2_typo_repair_applied"] = False
        original_audit["generic_h2_typo_corrections"] = []
        return original_answer, original_audit

    required_keys = [normalize_heading_key(value) for value in required]
    observed = [heading for _, heading in matches]
    observed_keys = [normalize_heading_key(value) for value in observed]
    if len(set(required_keys)) != len(required_keys) or len(set(observed_keys)) != len(observed_keys):
        original_audit["generic_h2_typo_repair_applied"] = False
        original_audit["generic_h2_typo_corrections"] = []
        return original_answer, original_audit

    used_required: set[int] = set()
    mapping: dict[int, int] = {}
    corrections: list[dict[str, Any]] = []
    for observed_index, observed_key in enumerate(observed_keys):
        exact_matches = [
            index
            for index, required_key in enumerate(required_keys)
            if index not in used_required and observed_key == required_key
        ]
        if len(exact_matches) == 1:
            required_index = exact_matches[0]
        else:
            candidates = [
                index
                for index, required_key in enumerate(required_keys)
                if index not in used_required
                and min(len(observed_key), len(required_key)) >= 4
                and _edit_distance_at_most_one(observed_key, required_key)
            ]
            if len(candidates) != 1:
                original_audit["generic_h2_typo_repair_applied"] = False
                original_audit["generic_h2_typo_corrections"] = []
                return original_answer, original_audit
            required_index = candidates[0]
            corrections.append(
                {
                    "observed": observed[observed_index],
                    "canonical": required[required_index],
                    "edit_distance": 1,
                }
            )
        used_required.add(required_index)
        mapping[observed_index] = required_index

    if not corrections or len(mapping) != len(required):
        original_audit["generic_h2_typo_repair_applied"] = False
        original_audit["generic_h2_typo_corrections"] = []
        return original_answer, original_audit

    blocks: dict[int, list[str]] = {}
    for offset, (start, _heading) in enumerate(matches):
        end = matches[offset + 1][0] if offset + 1 < len(matches) else len(lines)
        body = lines[start + 1 : end]
        if not "\n".join(body).strip():
            original_audit["generic_h2_typo_repair_applied"] = False
            original_audit["generic_h2_typo_corrections"] = []
            return original_answer, original_audit
        required_index = mapping[offset]
        blocks[required_index] = [f"## {required[required_index]}", *body]

    rendered: list[str] = []
    for required_index in range(len(required)):
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.extend(blocks[required_index])
    normalized = "\n".join(rendered).strip() + "\n"
    audit = dict(original_audit)
    audit.update(
        {
            "h2_reordered": observed_keys != required_keys,
            "h2_reorder_blocked_reason": None,
            "normalized_h2_order": required,
            "generic_h2_typo_repair_applied": True,
            "generic_h2_typo_corrections": corrections,
            "generic_h2_typo_policy": (
                "system-generated-generic-only-unique-one-character-edit-"
                "nonempty-section-no-ambiguity"
            ),
            "explicit_user_markdown_contract_relaxed": False,
            "substantive_text_invented": False,
        }
    )
    return normalized, audit


def install_generic_heading_typo_normalization() -> None:
    normalization._canonicalize_h2 = _repair_generic_h2


__all__ = [
    "install_generic_heading_typo_normalization",
]
