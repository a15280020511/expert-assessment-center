"""Resolve logical governance aliases to exact live model/provider endpoints."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from v5_catalog_view import _endpoint_rows, _provider_slug, stable_model_id
from v5_model_company import canonical_model_company

GPT_LOGICAL_MODEL = "~openai/gpt-latest"
CLAUDE_LOGICAL_MODEL = "~anthropic/claude-opus-latest"
GOVERNANCE_MINIMUM_COMPLETION_TOKENS = 512
GOVERNANCE_CANDIDATES_PER_COMPANY = 6
STRUCTURED_OUTPUT_PARAMETERS = frozenset(
    {"response_format", "structured_outputs"}
)
REASONING_PARAMETERS = frozenset({"reasoning", "reasoning_effort"})


class GovernanceCatalogError(RuntimeError):
    """Raised when a logical governance model cannot be resolved safely."""


def _rank(model: Any) -> int:
    return int(
        (getattr(model, "ranks", {}) or {}).get(
            "intelligence-high-to-low",
            1_000_000,
        )
    )


def _is_gpt_candidate(model_id: str) -> bool:
    folded = model_id.casefold()
    if not folded.startswith("openai/gpt-"):
        return False
    return not any(
        term in folded
        for term in (
            "gpt-oss",
            "-mini",
            "-nano",
            "-codex",
            "-audio",
            "-image",
            "-search",
        )
    )


def _is_claude_candidate(model_id: str) -> bool:
    return model_id.casefold().startswith("anthropic/claude-opus-")


def governance_candidate_models(
    models: Mapping[str, Any],
    *,
    per_company: int = GOVERNANCE_CANDIDATES_PER_COMPANY,
) -> list[Any]:
    """Return official-rank ordered latest-model candidates for endpoint lookup."""
    gpt: list[Any] = []
    claude: list[Any] = []
    for model in models.values():
        model_id = str(getattr(model, "id", "") or "")
        if not stable_model_id(model_id):
            continue
        if _is_gpt_candidate(model_id):
            gpt.append(model)
        elif _is_claude_candidate(model_id):
            claude.append(model)
    gpt.sort(key=lambda row: (_rank(row), str(row.id)))
    claude.sort(key=lambda row: (_rank(row), str(row.id)))
    maximum = max(1, int(per_company))
    selected = [*gpt[:maximum], *claude[:maximum]]
    selected.sort(key=lambda row: (_rank(row), str(row.id)))
    if not gpt:
        raise GovernanceCatalogError(
            "live catalog has no eligible OpenAI GPT latest candidate"
        )
    if not claude:
        raise GovernanceCatalogError(
            "live catalog has no eligible Anthropic Claude Opus candidate"
        )
    return selected


def _supports_required_parameters(supported: set[str]) -> bool:
    """Require protocol features, not a local output-truncation parameter."""
    return bool(
        STRUCTURED_OUTPUT_PARAMETERS.intersection(supported)
        and REASONING_PARAMETERS.intersection(supported)
    )


def _resolve_one(
    models: Sequence[Any],
    endpoint_payloads: Mapping[str, Mapping[str, Any]],
    *,
    logical_model: str,
    company: str,
    required_context_tokens: int,
    minimum_completion_tokens: int,
) -> dict[str, Any]:
    inspected: list[dict[str, Any]] = []
    for model in sorted(models, key=lambda row: (_rank(row), str(row.id))):
        model_id = str(model.id)
        if canonical_model_company(model_id) != company:
            continue
        endpoints = sorted(
            _endpoint_rows(endpoint_payloads.get(model_id, {})),
            key=lambda row: _provider_slug(row),
        )
        for endpoint in endpoints:
            provider = _provider_slug(endpoint).casefold()
            supported = {
                str(value).casefold()
                for value in endpoint.get("supported_parameters", [])
            }
            context_length = int(
                endpoint.get("context_length")
                or getattr(model, "context_length", 0)
                or 0
            )
            maximum_output = int(
                endpoint.get("max_completion_tokens")
                or getattr(model, "max_completion_tokens", 0)
                or 0
            )
            reasons: list[str] = []
            if provider != company:
                reasons.append("not-direct-provider")
            if context_length < required_context_tokens:
                reasons.append("insufficient-context")
            if maximum_output < minimum_completion_tokens:
                reasons.append("insufficient-native-completion-capacity")
            if not _supports_required_parameters(supported):
                reasons.append("missing-required-protocol-parameter-family")
            inspected.append(
                {
                    "model": model_id,
                    "provider": provider,
                    "reasons": reasons,
                }
            )
            if reasons:
                continue
            return {
                "logical_model": logical_model,
                "resolved_model": model_id,
                "company": company,
                "provider": provider,
                "official_intelligence_rank": _rank(model),
                "context_length": context_length,
                "max_completion_tokens": maximum_output,
                "supported_parameters": sorted(supported),
                "temperature_supported": "temperature" in supported,
                "local_token_ceiling_parameter_required": False,
                "native_completion_capacity_checked": True,
                "provider_fallback_allowed": False,
                "synthetic_fixture_only": False,
                "inspected_endpoint_count": len(inspected),
            }
    raise GovernanceCatalogError(
        f"no exact direct {company} endpoint can satisfy governance protocol"
    )


def resolve_live_governance_models(
    models: Mapping[str, Any],
    endpoint_payloads: Mapping[str, Mapping[str, Any]],
    *,
    required_context_tokens: int,
    minimum_completion_tokens: int = GOVERNANCE_MINIMUM_COMPLETION_TOKENS,
) -> dict[str, Any]:
    """Resolve strongest usable OpenAI GPT and Anthropic Opus endpoints."""
    candidates = governance_candidate_models(models)
    context_floor = max(1, int(required_context_tokens))
    completion_floor = max(
        GOVERNANCE_MINIMUM_COMPLETION_TOKENS,
        int(minimum_completion_tokens),
    )
    gpt = _resolve_one(
        candidates,
        endpoint_payloads,
        logical_model=GPT_LOGICAL_MODEL,
        company="openai",
        required_context_tokens=context_floor,
        minimum_completion_tokens=completion_floor,
    )
    claude = _resolve_one(
        candidates,
        endpoint_payloads,
        logical_model=CLAUDE_LOGICAL_MODEL,
        company="anthropic",
        required_context_tokens=context_floor,
        minimum_completion_tokens=completion_floor,
    )
    return {
        "schema_version": "v5-governance-model-resolution-1",
        "status": "PASS",
        "selection_basis": "official-intelligence-rank-first-exact-direct-endpoint",
        "required_context_tokens": context_floor,
        "minimum_completion_tokens": completion_floor,
        "minimum_native_completion_capacity_tokens": completion_floor,
        "local_token_ceiling_parameter_required": False,
        "provider_fallback_allowed": False,
        "gpt": gpt,
        "claude": claude,
    }


def synthetic_governance_models() -> dict[str, Any]:
    """Deterministic no-call fixture used only by unit tests and dry-runs."""
    supported = [
        "reasoning",
        "response_format",
        "structured_outputs",
    ]
    return {
        "schema_version": "v5-governance-model-resolution-1",
        "status": "PASS",
        "selection_basis": "synthetic-no-call-fixture",
        "required_context_tokens": 1,
        "minimum_completion_tokens": GOVERNANCE_MINIMUM_COMPLETION_TOKENS,
        "minimum_native_completion_capacity_tokens": (
            GOVERNANCE_MINIMUM_COMPLETION_TOKENS
        ),
        "local_token_ceiling_parameter_required": False,
        "provider_fallback_allowed": False,
        "gpt": {
            "logical_model": GPT_LOGICAL_MODEL,
            "resolved_model": GPT_LOGICAL_MODEL,
            "company": "openai",
            "provider": "openai",
            "official_intelligence_rank": 0,
            "context_length": 1_000_000,
            "max_completion_tokens": 128_000,
            "supported_parameters": supported,
            "temperature_supported": False,
            "local_token_ceiling_parameter_required": False,
            "native_completion_capacity_checked": True,
            "provider_fallback_allowed": False,
            "synthetic_fixture_only": True,
        },
        "claude": {
            "logical_model": CLAUDE_LOGICAL_MODEL,
            "resolved_model": CLAUDE_LOGICAL_MODEL,
            "company": "anthropic",
            "provider": "anthropic",
            "official_intelligence_rank": 0,
            "context_length": 1_000_000,
            "max_completion_tokens": 128_000,
            "supported_parameters": supported,
            "temperature_supported": False,
            "local_token_ceiling_parameter_required": False,
            "native_completion_capacity_checked": True,
            "provider_fallback_allowed": False,
            "synthetic_fixture_only": True,
        },
    }
