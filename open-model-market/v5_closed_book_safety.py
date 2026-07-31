"""Deterministic safety checks for high-stakes closed-book deliverables.

The model may reason from the task, but it may not invent resource endurance,
claim unknown people are safe, or turn an explicitly unknown danger into a
solo inspection instruction. These checks are intentionally narrow and only
activate when the planner marks a node as a strict closed-book safety node.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

_RESOURCE_ENDURANCE_RE = re.compile(
    r"(?P<resource>手机|手电筒|荧光棒|应急照明|备用照明|电池|电源)"
    r"[^。；;\n]{0,36}?"
    r"(?P<duration>\d+(?:\.\d+)?\s*(?:分钟|小时|天))",
    re.IGNORECASE,
)
_UNSAFE_INVESTIGATION_RE = re.compile(
    r"(?:保安|值班人员|单人|独自)"
    r"[^。；;\n]{0,30}?"
    r"(?:检查|搜查|进入|前往|靠近|查看)"
    r"[^。；;\n]{0,36}?"
    r"(?:不明|未知|设备区|设备区域|地下|配电|积水|撞击声|报警声|危险区)",
    re.IGNORECASE,
)
_UNSUPPORTED_SAFETY_CONFIRMATION_RE = re.compile(
    r"(?:确认|确保|判定)(?:校内)?所有人员(?:均已)?安全",
    re.IGNORECASE,
)
_UNSPECIFIED_SUBSTITUTION_RE = re.compile(
    r"(?:灭火器|急救包|警戒带|通讯录|照明|手电筒|荧光棒)"
    r"[^。；;\n]{0,18}?(?:耗尽|失效|不可用)"
    r"[^。；;\n]{0,18}?使用其他物资代替",
    re.IGNORECASE,
)
_NEGATION_RE = re.compile(r"禁止|不得|不可|不应|避免|不要|严禁|仅可远距离|仅能远距离")


def _claim_key(resource: str, duration: str) -> str:
    return re.sub(r"\s+", "", f"{resource}:{duration}").casefold()


def allowed_resource_endurance_claims(task: str) -> list[str]:
    """Extract only resource-duration pairs explicitly present in the task."""
    return sorted(
        {
            _claim_key(match.group("resource"), match.group("duration"))
            for match in _RESOURCE_ENDURANCE_RE.finditer(str(task or ""))
        }
    )


def strict_contract_metadata(task: str) -> dict[str, Any]:
    """Return auditable metadata attached to every high-stakes tabletop node."""
    return {
        "closed_book_safety_strict": True,
        "fail_closed_on_quality_gate": True,
        "allowed_resource_endurance_claims": allowed_resource_endurance_claims(task),
        "forbid_unsupported_personnel_safety_confirmation": True,
        "forbid_solo_unknown_hazard_investigation": True,
        "forbid_unspecified_safety_equipment_substitution": True,
    }


def _line_is_negated(answer: str, start: int, end: int) -> bool:
    line_start = answer.rfind("\n", 0, start) + 1
    line_end = answer.find("\n", end)
    if line_end < 0:
        line_end = len(answer)
    return bool(_NEGATION_RE.search(answer[line_start:line_end]))


def validate_answer(answer: str, contract: Mapping[str, Any]) -> list[str]:
    """Return deterministic safety violations for a strict closed-book answer."""
    if not contract.get("closed_book_safety_strict"):
        return []
    text = str(answer or "")
    allowed = {
        str(value).casefold()
        for value in contract.get("allowed_resource_endurance_claims", [])
    }
    violations: list[str] = []

    for match in _RESOURCE_ENDURANCE_RE.finditer(text):
        key = _claim_key(match.group("resource"), match.group("duration"))
        if key not in allowed:
            violations.append("unsupported-resource-endurance-claim:" + key)

    if contract.get("forbid_solo_unknown_hazard_investigation"):
        for match in _UNSAFE_INVESTIGATION_RE.finditer(text):
            if not _line_is_negated(text, match.start(), match.end()):
                violations.append("unsafe-solo-unknown-hazard-investigation")
                break

    if (
        contract.get("forbid_unsupported_personnel_safety_confirmation")
        and _UNSUPPORTED_SAFETY_CONFIRMATION_RE.search(text)
    ):
        violations.append("unsupported-all-personnel-safe-claim")

    if (
        contract.get("forbid_unspecified_safety_equipment_substitution")
        and _UNSPECIFIED_SUBSTITUTION_RE.search(text)
    ):
        violations.append("unspecified-safety-equipment-substitution")

    return list(dict.fromkeys(violations))
