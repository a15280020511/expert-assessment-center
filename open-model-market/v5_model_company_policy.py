"""Task-level model-company diversity policy for V5 planning and recovery.

One task may select at most one model from each model developer/company. The
policy is based on the OpenRouter model namespace, with a small canonical alias
map for known namespace variants. Provider hosting remains a separate concern:
multiple providers do not make two models from the same company independent.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Mapping, Sequence


_COMPANY_ALIASES = {
    "01-ai": "01-ai",
    "ai21": "ai21-labs",
    "ai21-labs": "ai21-labs",
    "alibaba": "alibaba",
    "amazon": "amazon",
    "amazon-nova": "amazon",
    "anthropic": "anthropic",
    "baidu": "baidu",
    "bytedance": "bytedance",
    "cohere": "cohere",
    "deepmind": "google",
    "deepseek": "deepseek",
    "google": "google",
    "google-ai": "google",
    "meta": "meta",
    "meta-llama": "meta",
    "microsoft": "microsoft",
    "minimax": "minimax",
    "mistral": "mistral-ai",
    "mistralai": "mistral-ai",
    "moonshot": "moonshot-ai",
    "moonshotai": "moonshot-ai",
    "nous-research": "nous-research",
    "nousresearch": "nous-research",
    "nvidia": "nvidia",
    "openai": "openai",
    "perplexity": "perplexity",
    "qwen": "alibaba",
    "tencent": "tencent",
    "thudm": "zhipu-ai",
    "x-ai": "x-ai",
    "xai": "x-ai",
    "z-ai": "zhipu-ai",
    "zhipu": "zhipu-ai",
    "zhipu-ai": "zhipu-ai",
}


def _slug(value: Any) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", str(value or "").strip().casefold())
    return normalized.strip("-")


def model_company(model_id: Any, author: Any = None) -> str:
    """Return one stable company identifier for a model namespace."""
    model = str(model_id or "").strip()
    namespace = str(author or "").strip()
    if not namespace and "/" in model:
        namespace = model.split("/", 1)[0]
    company = _slug(namespace)
    if not company:
        raise ValueError(f"Cannot derive model company from model ID: {model!r}")
    return _COMPANY_ALIASES.get(company, company)


def row_company(row: Mapping[str, Any] | Any) -> str:
    """Resolve company from a candidate/endpoint mapping or candidate object."""
    if isinstance(row, Mapping):
        explicit = row.get("model_company")
        if explicit:
            return model_company(row.get("model") or row.get("model_id"), explicit)
        return model_company(
            row.get("model") or row.get("model_id"),
            row.get("author"),
        )
    return model_company(
        getattr(row, "model", None) or getattr(row, "model_id", None),
        getattr(row, "model_company", None) or getattr(row, "author", None),
    )


def add_task_company_constraints(
    cp_model: Any,
    variables: Sequence[Any],
    candidates: Sequence[Any],
) -> list[dict[str, Any]]:
    """Add one-company-at-most-once constraints within every interpretation."""
    grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
    for index, candidate in enumerate(candidates):
        interpretation = str(getattr(candidate, "interpretation_id", ""))
        grouped[(interpretation, row_company(candidate))].append(index)

    audit: list[dict[str, Any]] = []
    for (interpretation, company), indices in sorted(grouped.items()):
        cp_model.Add(sum(variables[index] for index in indices) <= 1)
        audit.append(
            {
                "interpretation_id": interpretation,
                "model_company": company,
                "candidate_count": len(indices),
                "maximum_selected": 1,
            }
        )
    return audit


def selected_company_audit(
    candidates: Sequence[Any],
    selected_indices: Sequence[int],
) -> dict[str, Any]:
    selected = [candidates[index] for index in selected_indices]
    rows = [
        {
            "candidate_id": str(getattr(candidate, "candidate_id", "")),
            "model": str(getattr(candidate, "model", "")),
            "model_company": row_company(candidate),
        }
        for candidate in selected
    ]
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["model_company"]] += 1
    duplicates = sorted(company for company, count in counts.items() if count > 1)
    return {
        "policy": "task-global-all-different-model-company",
        "selected_model_count": len(rows),
        "selected_company_count": len(counts),
        "unique": not duplicates,
        "duplicate_companies": duplicates,
        "selected": rows,
    }


def assert_selected_companies_unique(
    candidates: Sequence[Any],
    selected_indices: Sequence[int],
) -> dict[str, Any]:
    audit = selected_company_audit(candidates, selected_indices)
    if not audit["unique"]:
        raise ValueError(
            "Selected execution graph violates task-level model-company uniqueness: "
            + ", ".join(audit["duplicate_companies"])
        )
    return audit


def _coverage(row: Mapping[str, Any]) -> tuple[str, ...]:
    values = row.get("coverage_keys")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(sorted(str(value) for value in values))


def _provider(row: Mapping[str, Any]) -> str:
    provider = str(row.get("provider_slug") or "").strip()
    if provider:
        return provider
    endpoint = str(row.get("provider_endpoint") or "")
    return endpoint.rsplit("@", 1)[-1] if "@" in endpoint else endpoint


def _recovery_sort_key(
    row: Mapping[str, Any],
    selected_provider: str,
) -> tuple[Any, ...]:
    provider = _provider(row)
    cost = max(0.0, float(row.get("estimated_cost", 0.0) or 0.0))
    failure = max(0.0, min(1.0, float(row.get("failure_probability", 1.0) or 1.0)))
    quality = max(0.01, float(row.get("estimated_quality", 0.0) or 0.0))
    effective_cost_per_quality = cost * (1.0 + failure) / quality
    return (
        provider == selected_provider,
        effective_cost_per_quality,
        failure,
        cost,
        -quality,
        row_company(row),
        str(row.get("candidate_id") or ""),
    )


def build_disjoint_recovery_pool(
    selected_rows: Sequence[Mapping[str, Any]],
    candidate_rows: Sequence[Mapping[str, Any]],
    *,
    interpretation_id: str,
    maximum_rows_per_node: int,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Build recovery pools whose companies are disjoint across the whole task.

    Companies used by the original graph are excluded. A fallback company is
    allocated to at most one selected node, so two simultaneous replacements
    cannot introduce a duplicate company later in the same run.
    """
    maximum = max(0, int(maximum_rows_per_node))
    selected = [dict(row) for row in selected_rows if isinstance(row, Mapping)]
    candidates = [dict(row) for row in candidate_rows if isinstance(row, Mapping)]
    selected_ids = {str(row.get("candidate_id") or row.get("node_id") or "") for row in selected}
    selected_companies = {row_company(row) for row in selected}
    reserved_recovery_companies: set[str] = set()
    pool: dict[str, list[dict[str, Any]]] = {}

    for chosen in sorted(
        selected,
        key=lambda row: str(row.get("candidate_id") or row.get("node_id") or ""),
    ):
        node_id = str(chosen.get("candidate_id") or chosen.get("node_id") or "")
        selected_model = str(chosen.get("model") or "")
        selected_endpoint = str(chosen.get("provider_endpoint") or "")
        selected_provider = _provider(chosen)
        coverage = _coverage(chosen)
        alternatives = [
            row
            for row in candidates
            if str(row.get("candidate_id") or "") not in selected_ids
            and str(row.get("interpretation_id") or "") == interpretation_id
            and _coverage(row) == coverage
            and str(row.get("model") or "") != selected_model
            and str(row.get("provider_endpoint") or "") != selected_endpoint
            and row_company(row) not in selected_companies
            and row_company(row) not in reserved_recovery_companies
        ]
        alternatives.sort(key=lambda row: _recovery_sort_key(row, selected_provider))

        chosen_rows: list[dict[str, Any]] = []
        local_companies: set[str] = set()
        for alternative in alternatives:
            company = row_company(alternative)
            if company in local_companies or company in reserved_recovery_companies:
                continue
            enriched = dict(alternative)
            enriched["model_company"] = company
            chosen_rows.append(enriched)
            local_companies.add(company)
            reserved_recovery_companies.add(company)
            if len(chosen_rows) >= maximum:
                break
        pool[node_id] = chosen_rows

    audit = {
        "source": "current-run-frozen-candidate-graph",
        "same_company_model_replacement_allowed": False,
        "selected_company_reuse_allowed": False,
        "recovery_company_reuse_across_nodes_allowed": False,
        "provider_diversity_first": True,
        "maximum_candidates_per_selected_node": maximum,
        "selected_companies": sorted(selected_companies),
        "reserved_recovery_companies": sorted(reserved_recovery_companies),
        "cross_task_history_used": False,
    }
    return pool, audit
