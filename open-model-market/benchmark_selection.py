"""Benchmark-aware selection wrapper with conservative model alias resolution.

OpenRouter's models catalog exposes request ``id`` and permanent
``canonical_slug`` values, while the benchmarks endpoint identifies rows by
``model_permaslug``. The public endpoints can therefore describe the same model
with different slugs. This module augments benchmark rows with audited aliases
before delegating to the existing deterministic seat selector.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from collections import defaultdict
from typing import Any, Dict, Mapping, Sequence, Tuple

import seat_scoring as base
from model_market import ModelInfo, RunConfig, SelectedExpert, SelectedJudge, TaskProfile

DATE_SUFFIX_RE = re.compile(r"-(?:20\d{2}(?:-\d{2}-\d{2})?|20\d{6})$")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _normalized_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    if ":" in text:
        text = text.split(":", 1)[1]
    return NON_ALNUM_RE.sub("", text)


def _slug_key(value: Any) -> str:
    text = str(value or "").strip().casefold().split(":", 1)[0]
    if "/" not in text:
        return ""
    author, slug = text.split("/", 1)
    slug = DATE_SUFFIX_RE.sub("", slug)
    return f"{author}/{slug}"


def _unique_index(rows: Sequence[Mapping[str, Any]], key_fn) -> Dict[str, Mapping[str, Any]]:
    buckets = defaultdict(list)
    for row in rows:
        key = key_fn(row)
        if key:
            buckets[key].append(row)
    return {key: values[0] for key, values in buckets.items() if len(values) == 1}


def augment_benchmark_payload(
    payload: Mapping[str, Any],
    models: Sequence[ModelInfo],
) -> Dict[str, Any]:
    """Add request-ID aliases only when a benchmark row resolves uniquely."""
    cloned = copy.deepcopy(dict(payload))
    data = cloned.get("data")
    if not isinstance(data, list):
        return cloned
    rows = [row for row in data if isinstance(row, Mapping)]
    exact = {str(row.get("model_permaslug") or "") for row in rows}
    by_name = _unique_index(rows, lambda row: _normalized_name(row.get("display_name")))
    by_slug = _unique_index(rows, lambda row: _slug_key(row.get("model_permaslug")))

    aliases = []
    methods: Dict[str, int] = defaultdict(int)
    unresolved = []
    for model in models:
        if model.id in exact:
            methods["exact_model_permaslug"] += 1
            continue
        row = by_slug.get(_slug_key(model.id))
        method = "slug_key"
        if row is None:
            row = by_name.get(_normalized_name(model.name))
            method = "unique_display_name"
        if row is None:
            unresolved.append(model.id)
            continue
        alias = copy.deepcopy(dict(row))
        source_slug = str(alias.get("model_permaslug") or "")
        alias["model_permaslug"] = model.id
        alias["resolved_from_permaslug"] = source_slug
        alias["resolution_method"] = method
        aliases.append(alias)
        methods[method] += 1

    cloned["data"] = list(data) + aliases
    meta = dict(cloned.get("meta") or {}) if isinstance(cloned.get("meta"), Mapping) else {}
    meta["alias_resolution"] = {
        "version": 1,
        "stable_eligible_model_count": len(models),
        "added_alias_count": len(aliases),
        "methods": dict(methods),
        "unresolved_count": len(unresolved),
        "unresolved_model_ids": unresolved,
    }
    cloned["meta"] = meta
    return cloned


def select_team(
    ranked: Sequence[ModelInfo],
    profile: TaskProfile,
    run: RunConfig,
) -> Tuple[list[SelectedExpert], SelectedJudge, float]:
    """Delegate to the existing selector with scoped benchmark alias support."""
    original_request = base.request_json
    stable_models = base._stable_pool(ranked, profile)

    def request_with_aliases(url: str, *args, **kwargs):
        payload = original_request(url, *args, **kwargs)
        if url == base.BENCHMARKS_URL and isinstance(payload, Mapping):
            return augment_benchmark_payload(payload, stable_models)
        return payload

    base.request_json = request_with_aliases
    try:
        return base.select_team(ranked, profile, run)
    finally:
        base.request_json = original_request
