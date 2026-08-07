"""OpenRouter catalog collection without task profiling or model selection."""
from __future__ import annotations

import argparse
import json
import math
import os
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from openrouter_api import MODELS_URL, request_json

DEFAULT_CONFIG = Path(__file__).with_name("config.json")
MAX_CATALOG_MODELS = 1000


class ExpertTeamError(RuntimeError):
    pass


@dataclass
class ModelInfo:
    id: str
    name: str
    description: str
    author: str
    context_length: int
    max_completion_tokens: int
    prompt_price_per_million: Optional[float]
    completion_price_per_million: Optional[float]
    supported_parameters: List[str]
    input_modalities: List[str]
    output_modalities: List[str]
    knowledge_cutoff: Optional[str]
    expiration_date: Optional[str]
    reasoning: Dict[str, Any] = field(default_factory=dict)
    ranks: Dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RunConfig:
    task: str
    output_dir: Path
    api_key: Optional[str]
    ranking_limit: int
    minimum_context_length: int
    catalog_sorts: List[str]
    catalog_file: Optional[Path]
    max_completion_tokens: int
    reasoning_effort: str
    temperature: float
    catalog_timeout_seconds: int
    catalog_max_retries: int
    model_timeout_seconds: int
    model_max_retries: int
    maximum_replacements: int
    parallel_workers: int
    provider: Dict[str, Any]
    dry_run: bool
    require_live_catalog: bool
    maximum_total_calls: int = 16
    maximum_recovery_calls: int = 2


def load_json(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExpertTeamError(f"File not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ExpertTeamError(f"Invalid JSON in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ExpertTeamError(f"JSON root must be an object: {path}")
    return data


def _ppm(pricing: Mapping[str, Any], key: str) -> Optional[float]:
    raw = pricing.get(key)
    if raw in {None, ""}:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value * 1_000_000 if math.isfinite(value) and value >= 0 else None


def build_run_config(args: argparse.Namespace) -> RunConfig:
    cfg = load_json(Path(getattr(args, "config", DEFAULT_CONFIG)))
    catalog = cfg.get("catalog", {})
    execution = cfg.get("execution", {})
    provider = cfg.get("provider", {})
    if not all(
        isinstance(value, dict)
        for value in (catalog, execution, provider)
    ):
        raise ExpertTeamError("catalog, execution and provider must be objects")

    task = (
        getattr(args, "task", None)
        or os.getenv("EXPERT_TASK")
        or ""
    ).strip()
    if not task:
        raise ExpertTeamError("Task is required")
    ranking_arg = getattr(args, "ranking_limit", None)
    if ranking_arg is None:
        ranking_arg = catalog.get("maximum_models", MAX_CATALOG_MODELS)
    ranking = int(ranking_arg)
    # V9 uses governance-supplied dynamic candidate inventories. ranking_limit is
    # compatibility/advisory metadata and must not impose an artificial upper
    # bound on the number of candidates the current task may carry.
    if ranking <= 0:
        raise ExpertTeamError("ranking_limit must be positive")
    completion_advisory = int(
        getattr(args, "max_completion_tokens", None)
        or execution.get("max_completion_tokens", 10_000)
    )
    if completion_advisory <= 0:
        raise ExpertTeamError(
            "max_completion_tokens advisory must be positive"
        )
    reasoning = str(
        getattr(args, "reasoning_effort", None)
        or execution.get("reasoning_effort", "low")
    )
    if reasoning not in {"low", "medium", "high"}:
        raise ExpertTeamError(
            "reasoning_effort must be low, medium or high"
        )

    recovery_arg = getattr(args, "maximum_recovery_calls", None)
    recovery_calls = int(
        execution.get("maximum_replacements", 2)
        if recovery_arg is None
        else recovery_arg
    )
    if recovery_calls < 0:
        raise ExpertTeamError("maximum_recovery_calls must be non-negative")

    catalog_arg = getattr(args, "catalog_file", None)
    return RunConfig(
        task=task,
        output_dir=Path(getattr(args, "output_dir", "v5-artifacts")),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        ranking_limit=ranking,
        minimum_context_length=int(
            catalog.get("minimum_context_length", 16_384)
        ),
        catalog_sorts=[
            str(value)
            for value in catalog.get(
                "sorts",
                ["intelligence-high-to-low"],
            )
        ],
        catalog_file=Path(catalog_arg) if catalog_arg else None,
        max_completion_tokens=completion_advisory,
        reasoning_effort=reasoning,
        temperature=float(execution.get("temperature", 0.0)),
        catalog_timeout_seconds=int(
            execution.get("catalog_timeout_seconds", 30)
        ),
        catalog_max_retries=int(
            execution.get("catalog_max_retries", 1)
        ),
        model_timeout_seconds=int(
            execution.get("model_timeout_seconds", 240)
        ),
        model_max_retries=0,
        maximum_replacements=recovery_calls,
        parallel_workers=int(execution.get("parallel_workers", 4)),
        provider=dict(provider),
        dry_run=bool(getattr(args, "dry_run", False)),
        require_live_catalog=bool(
            getattr(args, "require_live_catalog", False)
        ),
        maximum_total_calls=int(
            getattr(args, "maximum_total_calls", 16)
        ),
        maximum_recovery_calls=recovery_calls,
    )


def _fixture_catalog(run: RunConfig) -> tuple[dict[str, Any], str]:
    payload = load_json(run.catalog_file).get("sorts")
    if not isinstance(payload, dict):
        raise ExpertTeamError("Fixture catalog must contain a sorts object")
    return payload, f"fixture:{run.catalog_file}"


def _live_catalog(run: RunConfig) -> tuple[dict[str, Any], str]:
    if run.require_live_catalog and not run.api_key:
        raise ExpertTeamError("OPENROUTER_API_KEY is required for live catalog")
    payload: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for sort_name in run.catalog_sorts:
        query = urllib.parse.urlencode(
            {"sort": sort_name, "output_modalities": "text"}
        )
        try:
            payload[sort_name] = request_json(
                f"{MODELS_URL}?{query}",
                run.api_key,
                run.catalog_timeout_seconds,
                run.catalog_max_retries,
            )
        except Exception as exc:  # noqa: BLE001
            errors[sort_name] = str(exc)
    if not payload:
        raise ExpertTeamError(f"Live catalog unavailable: {errors}")
    return payload, "openrouter-live"


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    return [str(item) for item in value] if isinstance(value, list) else []


def _model_from_row(model_id: str, row: Mapping[str, Any]) -> ModelInfo:
    pricing = _mapping(row.get("pricing"))
    top = _mapping(row.get("top_provider"))
    architecture = _mapping(row.get("architecture"))
    reasoning = _mapping(row.get("reasoning"))
    return ModelInfo(
        id=model_id,
        name=str(row.get("name") or model_id),
        description=str(row.get("description") or ""),
        author=model_id.split("/", 1)[0],
        context_length=int(
            row.get("context_length") or top.get("context_length") or 0
        ),
        max_completion_tokens=int(
            top.get("max_completion_tokens")
            or row.get("max_completion_tokens")
            or 0
        ),
        prompt_price_per_million=_ppm(pricing, "prompt"),
        completion_price_per_million=_ppm(pricing, "completion"),
        supported_parameters=_string_list(row.get("supported_parameters")),
        input_modalities=_string_list(architecture.get("input_modalities")),
        output_modalities=_string_list(architecture.get("output_modalities")),
        knowledge_cutoff=row.get("knowledge_cutoff"),
        expiration_date=row.get("expiration_date"),
        reasoning=dict(reasoning),
    )


def _merge_catalog_sort(
    models: dict[str, ModelInfo],
    sort_name: str,
    sort_payload: Any,
) -> None:
    rows = sort_payload.get("data") if isinstance(sort_payload, Mapping) else None
    if not isinstance(rows, list):
        return
    for rank, row in enumerate(rows, 1):
        if not isinstance(row, Mapping) or not isinstance(row.get("id"), str):
            continue
        model_id = str(row["id"])
        if model_id not in models:
            models[model_id] = _model_from_row(model_id, row)
        models[model_id].ranks[sort_name] = rank


def fetch_catalog(run: RunConfig) -> Tuple[Dict[str, ModelInfo], str]:
    payload, source = (
        _fixture_catalog(run) if run.catalog_file else _live_catalog(run)
    )

    models: Dict[str, ModelInfo] = {}
    for sort_name, sort_payload in payload.items():
        _merge_catalog_sort(models, sort_name, sort_payload)
    if not models:
        raise ExpertTeamError("No usable direct models in catalog")
    return models, source
