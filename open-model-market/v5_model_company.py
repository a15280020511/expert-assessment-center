"""Canonical model-company identity and hard diversity defaults for V5."""
from __future__ import annotations

from typing import Any, Mapping

REQUIRE_DISTINCT_MODEL_COMPANIES = True
MINIMUM_CANDIDATES_PER_WORK = 24
DEFAULT_INTELLIGENCE_RANKING_LIMIT = 150

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
    """Return the canonical company for a CandidateNode or mapping."""
    if isinstance(candidate, Mapping):
        return canonical_model_company(str(candidate.get("model") or ""))
    return canonical_model_company(str(getattr(candidate, "model", "")))
