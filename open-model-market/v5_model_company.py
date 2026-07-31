"""Canonical model-company identity and hard diversity defaults for V5."""
from __future__ import annotations

from typing import Any, Mapping

REQUIRE_DISTINCT_MODEL_COMPANIES = True
MINIMUM_CANDIDATES_PER_WORK = 24

MODEL_COMPANY_ALIASES: Mapping[str, str] = {
    "alibaba": "alibaba",
    "qwen": "alibaba",
    "anthropic": "anthropic",
    "amazon": "amazon",
    "bytedance": "bytedance",
    "bytedance-seed": "bytedance",
    "cohere": "cohere",
    "deepseek": "deepseek",
    "google": "google",
    "meta": "meta",
    "meta-llama": "meta",
    "microsoft": "microsoft",
    "minimax": "minimax",
    "mistral": "mistral",
    "mistralai": "mistral",
    "moonshot": "moonshot",
    "moonshotai": "moonshot",
    "nvidia": "nvidia",
    "openai": "openai",
    "perplexity": "perplexity",
    "stepfun": "stepfun",
    "x-ai": "xai",
    "xai": "xai",
    "z-ai": "zhipu",
    "zhipu": "zhipu",
}


def canonical_model_company(model_id: str) -> str:
    """Return a stable company identity from one direct model ID."""
    value = str(model_id or "").strip().casefold()
    author = value.split("/", 1)[0].strip() if "/" in value else value
    if not author:
        return "unknown"
    return MODEL_COMPANY_ALIASES.get(author, author)


def candidate_company(candidate: Any) -> str:
    """Return the canonical company for a CandidateNode or mapping."""
    if isinstance(candidate, Mapping):
        return canonical_model_company(str(candidate.get("model") or ""))
    return canonical_model_company(str(getattr(candidate, "model", "")))
