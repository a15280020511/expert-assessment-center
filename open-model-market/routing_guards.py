"""Safety guards applied around conditional semantic task routing."""
from __future__ import annotations

import math
import re
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Mapping

from model_market import ExpertTeamError, POLICY_FILE, TaskProfile, load_json
from task_router import RoutingOutcome, _json_object, _refine_profile

EVIDENCE_SECTION_RE = re.compile(
    r"\n\n用户提供的(?:证据目录|执行说明)[^\n]*：.*$",
    re.DOTALL,
)
EXPLICIT_MODEL_FAMILY_RE = re.compile(
    r"\b(?:gpt(?:[-\s]?\d[\w.-]*)?|claude(?:[-\s][\w.-]+)?|gemini(?:[-\s][\w.-]+)?|"
    r"deepseek(?:[-\s][\w.-]+)?|qwen(?:[-\s][\w.-]+)?|glm(?:[-\s][\w.-]+)?|"
    r"llama(?:[-\s][\w.-]+)?|mistral(?:[-\s][\w.-]+)?|grok(?:[-\s][\w.-]+)?|"
    r"kimi(?:[-\s][\w.-]+)?|mimo(?:[-\s][\w.-]+)?)\b",
    re.IGNORECASE,
)


def strip_evidence_for_classification(task: str) -> str:
    """Keep evidence available to experts while excluding it from task routing."""
    return EVIDENCE_SECTION_RE.sub("", task)


def minimum_semantic_confidence(config_path: Path) -> float:
    cfg = load_json(config_path)
    routing = cfg.get("routing", {})
    if not isinstance(routing, Mapping):
        raise ExpertTeamError("routing must be a JSON object.")
    try:
        value = float(routing.get("minimum_semantic_confidence", 0.65))
    except (TypeError, ValueError) as exc:
        raise ExpertTeamError("routing.minimum_semantic_confidence must be numeric.") from exc
    if not 0 <= value <= 1:
        raise ExpertTeamError("routing.minimum_semantic_confidence must be between 0 and 1.")
    return value


def _response_content(outcome: RoutingOutcome) -> str:
    choices = outcome.response.get("choices") if isinstance(outcome.response, Mapping) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        raise ExpertTeamError("Semantic router response has no usable choice.")
    message = choices[0].get("message")
    if not isinstance(message, Mapping):
        raise ExpertTeamError("Semantic router response has no usable message.")
    content = str(message.get("content") or "").strip()
    if not content:
        raise ExpertTeamError("Semantic router response content is empty.")
    return content


def _contains_explicit_model_reference(text: str) -> bool:
    """Detect actual model names/IDs, not ordinary words such as modeling."""
    return "/" in text or bool(EXPLICIT_MODEL_FAMILY_RE.search(text))


def _validate_recoverable_profile(data: Mapping[str, Any]) -> Dict[str, Any]:
    allowed_fields = {
        "primary_domain",
        "secondary_domains",
        "complexity",
        "high_stakes",
        "required_capabilities",
        "confidence",
        "reason",
    }
    unknown = sorted(set(data) - allowed_fields)
    if unknown:
        raise ExpertTeamError(f"Semantic router returned forbidden fields: {unknown}")

    policy = load_json(POLICY_FILE)
    allowed_domains = set((policy.get("keywords") or {}).keys()) | {"general"}
    primary = str(data.get("primary_domain") or "").strip()
    if primary not in allowed_domains:
        raise ExpertTeamError("Semantic router primary_domain is invalid.")

    secondary_raw = data.get("secondary_domains") or []
    if not isinstance(secondary_raw, list) or len(secondary_raw) > 3:
        raise ExpertTeamError("Semantic router secondary_domains must contain at most 3 entries.")
    secondaries = []
    for value in secondary_raw:
        domain = str(value or "").strip()
        if domain not in allowed_domains:
            raise ExpertTeamError("Semantic router secondary domain is invalid.")
        if domain != primary and domain not in secondaries:
            secondaries.append(domain)

    complexity = str(data.get("complexity") or "")
    if complexity not in {"simple", "medium", "complex"}:
        raise ExpertTeamError("Semantic router complexity is invalid.")
    if not isinstance(data.get("high_stakes"), bool):
        raise ExpertTeamError("Semantic router high_stakes must be boolean.")

    capabilities_raw = data.get("required_capabilities") or []
    if not isinstance(capabilities_raw, list) or not 1 <= len(capabilities_raw) <= 8:
        raise ExpertTeamError("Semantic router required_capabilities must contain 1-8 entries.")
    capabilities = []
    for value in capabilities_raw:
        capability = str(value or "").strip()
        if not capability or len(capability) > 120:
            raise ExpertTeamError("Semantic router capability labels must be 1-120 characters.")
        if _contains_explicit_model_reference(capability):
            raise ExpertTeamError("Semantic router must not choose or name models.")
        capabilities.append(capability)

    try:
        confidence = float(data.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise ExpertTeamError("Semantic router confidence must be numeric.") from exc
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ExpertTeamError("Semantic router confidence must be between 0 and 1.")

    reason = str(data.get("reason") or "").strip()
    if len(reason) > 600:
        raise ExpertTeamError("Semantic router reason exceeds 600 characters.")
    return {
        "primary_domain": primary,
        "secondary_domains": secondaries,
        "complexity": complexity,
        "high_stakes": bool(data["high_stakes"]),
        "required_capabilities": capabilities,
        "confidence": confidence,
        "reason": reason,
    }


def _recover_generic_modeling_false_positive(
    outcome: RoutingOutcome,
    deterministic_profile: TaskProfile,
) -> RoutingOutcome:
    """Recover a valid profile rejected only because 'modeling' contains 'model'."""
    if outcome.semantic_profile_used:
        return outcome
    if outcome.status != "semantic_failed_deterministic_fallback":
        return outcome
    if outcome.error != "Semantic router must not choose or name models.":
        return outcome
    try:
        semantic = _validate_recoverable_profile(_json_object(_response_content(outcome)))
    except (ExpertTeamError, ValueError, TypeError):
        return outcome
    return replace(
        outcome,
        profile=_refine_profile(deterministic_profile, semantic),
        semantic_profile_used=True,
        status="semantic_success_validation_recovered",
        error="",
        required_capabilities=list(semantic["required_capabilities"]),
        semantic_confidence=float(semantic["confidence"]),
    )


def enforce_semantic_confidence(
    outcome: RoutingOutcome,
    deterministic_profile: TaskProfile,
    minimum: float,
) -> RoutingOutcome:
    """Reject weak semantic profiles while preserving paid-call audit evidence.

    A narrow compatibility recovery corrects the former substring bug where
    ordinary capabilities such as ``financial modeling`` were mistaken for a
    concrete model selection. Explicit model families and exact ``vendor/id``
    references remain forbidden.
    """
    outcome = _recover_generic_modeling_false_positive(outcome, deterministic_profile)
    if not outcome.semantic_profile_used:
        return outcome
    confidence = outcome.semantic_confidence
    if confidence is not None and confidence >= minimum:
        return outcome
    shown = "missing" if confidence is None else f"{confidence:.3f}"
    message = f"Semantic router confidence {shown} is below minimum {minimum:.3f}."
    return replace(
        outcome,
        profile=deterministic_profile,
        semantic_profile_used=False,
        status="semantic_low_confidence_deterministic_fallback",
        error=message,
        required_capabilities=[],
    )
