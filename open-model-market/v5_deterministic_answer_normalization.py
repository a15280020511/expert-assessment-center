"""Deterministic, auditable normalization before constitutional quality gates."""
from __future__ import annotations

import re
from collections import Counter
from hashlib import sha256
from typing import Any, Mapping, Sequence

from v5_task_constraints import TaskConstraints, normalized_quantities

_H2_RE = re.compile(r"^\s{0,3}##\s+(.+?)\s*#*\s*$")
_FACT_LABEL_LINE_RE = re.compile(
    r"^(?P<prefix>\s*(?:[-*+]\s*)?)"
    r"(?P<label>(?:事实|已知事实|fact)(?:[（(][^）)]*[）)])?\s*[:：])"
    r"\s*(?P<body>.+?)\s*$",
    re.IGNORECASE,
)
_NORMATIVE_TAIL_RE = re.compile(
    r"^(?P<fact>.+?)(?P<separator>[，,；;。]\s*)"
    r"(?P<norm>(?:必须|务必|应当|应该|禁止|不得|不可|不能|"
    r"需(?:要)?|建议|优先|否决|拒绝|应|可(?:以)?|"
    r"must\b|should\b|must\s+not\b|do\s+not\b|"
    r"recommend\b|reject\b|deny\b).+)$",
    re.IGNORECASE,
)


def _heading_key(value: str) -> str:
    value = re.sub(r"[`*_~]", "", str(value)).strip().casefold()
    value = re.sub(r"^\d+(?:\.\d+)*[\s.)、:：-]+", "", value)
    value = re.sub(r"[^0-9a-z_\u4e00-\u9fff]+", "_", value)
    return value.strip("_")


def _quantity_token(value: tuple[str, str, str]) -> str:
    lo, hi, unit = value
    return f"{lo}{('-' + hi) if hi else ''}:{unit}"


def _required_h2(output_contract: Mapping[str, Any]) -> list[str]:
    if output_contract.get("machine_readable_required"):
        return []
    values = output_contract.get("exact_markdown_headings")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        values = output_contract.get("required_fields")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _canonicalize_h2(
    answer: str,
    output_contract: Mapping[str, Any],
) -> tuple[str, dict[str, Any]]:
    required = _required_h2(output_contract)
    audit: dict[str, Any] = {
        "required_h2": required,
        "original_h2_order": [],
        "normalized_h2_order": [],
        "h2_reordered": False,
        "h2_reorder_blocked_reason": None,
    }
    if not required:
        return answer, audit

    lines = answer.splitlines()
    matches: list[tuple[int, str, str]] = []
    for index, line in enumerate(lines):
        match = _H2_RE.match(line)
        if match:
            matches.append((index, match.group(1).strip(), line))
    observed = [heading for _, heading, _ in matches]
    audit["original_h2_order"] = observed
    audit["normalized_h2_order"] = observed
    if not matches:
        audit["h2_reorder_blocked_reason"] = "no-h2-headings"
        return answer, audit
    if any(line.strip() for line in lines[: matches[0][0]]):
        audit["h2_reorder_blocked_reason"] = "nonempty-preamble"
        return answer, audit

    required_keys = [_heading_key(value) for value in required]
    observed_keys = [_heading_key(value) for value in observed]
    if Counter(observed_keys) != Counter(required_keys):
        audit["h2_reorder_blocked_reason"] = "heading-set-not-exact"
        return answer, audit
    if len(set(observed_keys)) != len(observed_keys):
        audit["h2_reorder_blocked_reason"] = "duplicate-heading"
        return answer, audit

    blocks: dict[str, list[str]] = {}
    for offset, (start, _, _) in enumerate(matches):
        end = matches[offset + 1][0] if offset + 1 < len(matches) else len(lines)
        block = lines[start:end]
        body = "\n".join(block[1:]).strip()
        key = observed_keys[offset]
        if not body:
            audit["h2_reorder_blocked_reason"] = "empty-required-section"
            return answer, audit
        blocks[key] = block

    if observed_keys == required_keys:
        return answer, audit
    reordered: list[str] = []
    for key in required_keys:
        if reordered and reordered[-1].strip():
            reordered.append("")
        reordered.extend(blocks[key])
    normalized = "\n".join(reordered).strip() + "\n"
    audit["h2_reordered"] = True
    audit["normalized_h2_order"] = required
    return normalized, audit


def _split_mixed_fact_labels(answer: str) -> tuple[str, list[dict[str, Any]]]:
    """Separate factual propositions from normative tails without rewriting text."""
    rows: list[str] = []
    evidence: list[dict[str, Any]] = []
    for line_number, line in enumerate(str(answer or "").splitlines(), start=1):
        match = _FACT_LABEL_LINE_RE.match(line)
        if not match:
            rows.append(line)
            continue
        body = match.group("body").strip()
        tail = _NORMATIVE_TAIL_RE.match(body)
        if not tail:
            rows.append(line)
            continue
        fact = tail.group("fact").strip().rstrip("，,；;。 ")
        normative = tail.group("norm").strip()
        if not fact or not normative:
            rows.append(line)
            continue
        prefix = match.group("prefix")
        label = match.group("label")
        fact_line = f"{prefix}{label}{fact}。"
        conclusion_line = f"{prefix}结论：{normative}"
        rows.extend((fact_line, conclusion_line))
        evidence.append(
            {
                "line_number": line_number,
                "original": line,
                "fact_line": fact_line,
                "conclusion_line": conclusion_line,
            }
        )
    suffix = "\n" if str(answer or "").endswith("\n") else ""
    return "\n".join(rows) + suffix, evidence


def normalize_answer(
    task: str,
    answer: str,
    output_contract: Mapping[str, Any],
    constraints: TaskConstraints,
) -> tuple[str, dict[str, Any]]:
    """Deterministically normalize label purity, quantities, and H2 order.

    This function never invents substantive text. It may insert an audited
    structural ``结论：`` label when a fact-labelled line already contains a
    normative tail, delete the smallest physical lines containing unsupported
    exact quantities, and move complete uniquely named H2 sections into the
    compiled contract order.
    """
    original = str(answer or "")
    audit: dict[str, Any] = {
        "schema_version": "v5-deterministic-answer-normalization-2",
        "policy": (
            "split-mixed-fact-normative-labels-delete-unsupported-quantity-lines-"
            "and-reorder-complete-h2-only"
        ),
        "applied": False,
        "original_answer_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "normalized_answer_sha256": sha256(original.encode("utf-8")).hexdigest(),
        "allowed_quantities": [],
        "removed_lines": [],
        "unsupported_quantities_removed": [],
        "mixed_fact_labels_split": [],
        "structural_labels_inserted": 0,
        "substantive_text_invented": False,
        "h2_reordered": False,
        "original_h2_order": [],
        "normalized_h2_order": [],
        "h2_reorder_blocked_reason": None,
        "text_invented": False,
    }
    working, mixed_fact_labels = _split_mixed_fact_labels(original)
    audit["mixed_fact_labels_split"] = mixed_fact_labels
    audit["structural_labels_inserted"] = len(mixed_fact_labels)
    if not constraints.unsupported_precise_quantities_allowed:
        allowed = normalized_quantities(task)
        audit["allowed_quantities"] = sorted(_quantity_token(value) for value in allowed)
        kept: list[str] = []
        removed_tokens: set[str] = set()
        for line_number, line in enumerate(working.splitlines(), start=1):
            unsupported = normalized_quantities(line) - allowed
            if unsupported and not _H2_RE.match(line):
                tokens = sorted(_quantity_token(value) for value in unsupported)
                removed_tokens.update(tokens)
                audit["removed_lines"].append(
                    {
                        "line_number": line_number,
                        "text": line,
                        "unsupported_quantities": tokens,
                    }
                )
                continue
            kept.append(line)
        collapsed: list[str] = []
        blank_run = 0
        for line in kept:
            if line.strip():
                blank_run = 0
                collapsed.append(line.rstrip())
            else:
                blank_run += 1
                if blank_run <= 1:
                    collapsed.append("")
        working = "\n".join(collapsed).strip() + ("\n" if collapsed else "")
        audit["unsupported_quantities_removed"] = sorted(removed_tokens)

    working, h2_audit = _canonicalize_h2(working, output_contract)
    audit.update(h2_audit)
    audit["applied"] = working != original
    audit["normalized_answer_sha256"] = sha256(working.encode("utf-8")).hexdigest()
    return working, audit
