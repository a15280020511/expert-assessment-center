"""Benchmark alias normalization and compatibility for task-matrix optimization."""
from __future__ import annotations

import copy
import re
import unicodedata
from collections import defaultdict
from dataclasses import replace
from typing import Any, Dict, Mapping, Sequence, Tuple

import model_market as market
import task_matrix_optimizer
import seat_scoring as base
from model_market import ModelInfo, RunConfig, SeatSpec, SelectedExpert, SelectedJudge, TaskProfile

DATE_SUFFIX_RE = re.compile(r"-(?:20\d{2}(?:-\d{2}-\d{2})?|20\d{6})$")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def _language_neutral_live_rank(models, profile, run):
    return task_matrix_optimizer.rank_models_live_only(models, replace(profile, chinese=False), run)


market.rank_models = _language_neutral_live_rank
_ORIGINAL_GENERATE_SEATS = task_matrix_optimizer.generate_seats


def _compatible_generate_seats(matrix, profile):
    rows = _ORIGINAL_GENERATE_SEATS(matrix, profile)
    for row in rows:
        spec = row.get("spec")
        if isinstance(spec, SeatSpec) and spec.key == "adversarial":
            row["spec"] = SeatSpec("red", spec.function, spec.profession, spec.domain_focus, spec.mission)
    return rows


task_matrix_optimizer.generate_seats = _compatible_generate_seats


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
    return f"{author}/{DATE_SUFFIX_RE.sub('', slug)}"


def _unique_index(rows: Sequence[Mapping[str, Any]], key_fn) -> Dict[str, Mapping[str, Any]]:
    buckets = defaultdict(list)
    for row in rows:
        key = key_fn(row)
        if key:
            buckets[key].append(row)
    return {key: values[0] for key, values in buckets.items() if len(values) == 1}


def augment_benchmark_payload(payload: Mapping[str, Any], models: Sequence[ModelInfo]) -> Dict[str, Any]:
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
        row = by_slug.get(_slug_key(model.id)); method = "slug_key"
        if row is None:
            row = by_name.get(_normalized_name(model.name)); method = "unique_display_name"
        if row is None:
            unresolved.append(model.id)
            continue
        alias = copy.deepcopy(dict(row))
        alias["resolved_from_permaslug"] = str(alias.get("model_permaslug") or "")
        alias["model_permaslug"] = model.id
        alias["resolution_method"] = method
        aliases.append(alias); methods[method] += 1
    cloned["data"] = list(data) + aliases
    meta = dict(cloned.get("meta") or {}) if isinstance(cloned.get("meta"), Mapping) else {}
    meta["alias_resolution"] = {
        "version": 1, "stable_eligible_model_count": len(models),
        "added_alias_count": len(aliases), "methods": dict(methods),
        "unresolved_count": len(unresolved), "unresolved_model_ids": unresolved,
    }
    cloned["meta"] = meta
    return cloned


def select_team(ranked: Sequence[ModelInfo], profile: TaskProfile, run: RunConfig) -> Tuple[list[SelectedExpert], SelectedJudge, float]:
    """Normalize benchmarks, run CP-SAT, and preserve stable external seat contracts."""
    original_request = base.request_json
    stable_models = task_matrix_optimizer._eligible_pool(ranked, profile)

    def request_with_aliases(url: str, *args, **kwargs):
        payload = original_request(url, *args, **kwargs)
        if url == base.BENCHMARKS_URL and isinstance(payload, Mapping):
            return augment_benchmark_payload(payload, stable_models)
        return payload

    base.request_json = request_with_aliases
    try:
        experts, judge, estimated = task_matrix_optimizer.select_team(ranked, profile, run)
    finally:
        base.request_json = original_request
    by_id = {model.id: model for model in ranked}
    experts = [
        replace(
            expert,
            selection_reason=(
                expert.selection_reason
                + f"；风险反证匹配={base._term_fit(by_id[expert.model_id], base.RISK_TERMS):.3f}"
                if expert.seat_key == "red" and expert.model_id in by_id
                else expert.selection_reason
            ),
        )
        for expert in experts
    ]
    return experts, judge, estimated
