"""Canonical model-company identity and platform diversity boundaries for V5."""
from __future__ import annotations

from typing import Any, Mapping

REQUIRE_DISTINCT_MODEL_COMPANIES = True
# Two is the mathematical lower bound needed to exercise company diversity.
# It is not a normal production pool width; production breadth is task-adaptive.
MINIMUM_CANDIDATES_PER_WORK = 2
ABSOLUTE_INTELLIGENCE_RANKING_CEILING = 150
# Compatibility alias for callers that treat this value as a catalog ceiling.
DEFAULT_INTELLIGENCE_RANKING_LIMIT = ABSOLUTE_INTELLIGENCE_RANKING_CEILING

MODEL_COMPANY_ALIASES: Mapping[str, str] = {
    "01-ai": "01-ai",
    "ai21": "ai21-labs",
    "ai21-labs": "ai21-labs",
    "alibaba": "alibaba",
    "qwen": "alibaba",
    "anthropic": "anthropic",
    "amazon": "amazon",
    "amazon-nova": "amazon",
    "bytedance": "bytedance",
    "bytedance-seed": "bytedance",
    "cohere": "cohere",
    "deepmind": "google",
    "google": "google",
    "google-ai": "google",
    "deepseek": "deepseek",
    "meta": "meta",
    "meta-llama": "meta",
    "microsoft": "microsoft",
    "minimax": "minimax",
    "mistral": "mistral",
    "mistralai": "mistral",
    "moonshot": "moonshot",
    "moonshotai": "moonshot",
    "nous-research": "nous-research",
    "nousresearch": "nous-research",
    "nvidia": "nvidia",
    "openai": "openai",
    "perplexity": "perplexity",
    "stepfun": "stepfun",
    "tencent": "tencent",
    "thudm": "zhipu",
    "x-ai": "xai",
    "xai": "xai",
    "z-ai": "zhipu",
    "zhipu": "zhipu",
    "zhipu-ai": "zhipu",
}


def canonical_model_company(model_id: str) -> str:
    """Return a stable company identity from one direct model ID."""
    value = str(model_id or "").strip().casefold()
    author = value.split("/", 1)[0].strip() if "/" in value else value
    if not author:
        return "unknown"
    return MODEL_COMPANY_ALIASES.get(author, author)


def candidate_company(candidate: Any) -> str:
    """Return the canonical company for candidates, market rows, or models."""
    if isinstance(candidate, Mapping):
        model_id = (
            candidate.get("model")
            or candidate.get("model_id")
            or candidate.get("id")
            or ""
        )
    else:
        model_id = (
            getattr(candidate, "model", None)
            or getattr(candidate, "model_id", None)
            or getattr(candidate, "id", None)
            or ""
        )
    return canonical_model_company(str(model_id))
