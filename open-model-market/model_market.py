"""V5 task profiling, OpenRouter catalog collection, and cost helpers."""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from openrouter_api import MODELS_URL, request_json

DEFAULT_CONFIG = Path(__file__).with_name("config.json")
POLICY_FILE = Path(__file__).with_name("team_policy.json")
MAX_TASK_CHARS = 50_000
ROUTER_PREFIXES = ("openrouter/",)


class ExpertTeamError(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskProfile:
    domains: List[str]
    primary_domain: str
    secondary_domain: str
    complexity: str
    complexity_score: int
    high_stakes: bool
    chinese: bool
    long_context: bool
    requested_context: int
    team_pattern: str = "dynamic-v5-dag"
    expert_count: int = 0


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
    score: float = 0.0
    components: Dict[str, float] = field(default_factory=dict)
    fit_reasons: List[str] = field(default_factory=list)

    @property
    def blended_price_per_million(self) -> Optional[float]:
        if self.prompt_price_per_million is None or self.completion_price_per_million is None:
            return None
        return self.prompt_price_per_million * 0.35 + self.completion_price_per_million * 0.65


@dataclass(frozen=True)
class RunConfig:
    task: str
    output_dir: Path
    api_key: Optional[str]
    quality_tier: str
    ranking_limit: int
    minimum_context_length: int
    candidate_pool_per_seat: int
    catalog_sorts: List[str]
    weights: Dict[str, float]
    soft_price_cap: float
    catalog_file: Optional[Path]
    max_estimated_cost_usd: Optional[float]
    budget_safety_factor: float
    max_completion_tokens: int
    judge_max_completion_tokens: int
    reasoning_effort: str
    temperature: float
    catalog_timeout_seconds: int
    catalog_max_retries: int
    model_timeout_seconds: int
    model_max_retries: int
    maximum_replacements: int
    parallel_workers: int
    judge_context_budget_chars: int
    require_all_experts: bool
    provider: Dict[str, Any]
    dry_run: bool
    require_live_catalog: bool


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


def _finite_number(value: Any, name: str, *, allow_none: bool = True) -> Optional[float]:
    if value in {None, ""} and allow_none:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ExpertTeamError(f"{name} must be numeric.") from exc
    if not math.isfinite(number):
        raise ExpertTeamError(f"{name} must be finite.")
    return number


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
    cfg = load_json(Path(args.config))
    selection = cfg.get("selection", {})
    execution = cfg.get("execution", {})
    provider = cfg.get("provider", {})
    if not all(isinstance(item, dict) for item in (selection, execution, provider)):
        raise ExpertTeamError("selection, execution, and provider must be JSON objects.")

    task = (getattr(args, "task", None) or os.getenv("EXPERT_TASK") or "").strip()
    if not task:
        raise ExpertTeamError("Task is required. Use --task or EXPERT_TASK.")
    if len(task) > MAX_TASK_CHARS:
        raise ExpertTeamError(f"Task exceeds {MAX_TASK_CHARS} characters.")

    tier = getattr(args, "quality_tier", None) or os.getenv("QUALITY_TIER") or selection.get("quality_tier", "value")
    if tier not in {"budget", "value", "quality"}:
        raise ExpertTeamError("quality_tier must be budget, value, or quality.")
    weights = {
        key: float(value)
        for key, value in dict((selection.get("weights") or {}).get(tier, {})).items()
    }

    raw_cost = getattr(args, "max_estimated_cost_usd", None) or os.getenv("MAX_ESTIMATED_COST_USD")
    max_cost = _finite_number(raw_cost, "max_estimated_cost_usd")
    if max_cost is not None and max_cost <= 0:
        raise ExpertTeamError("max_estimated_cost_usd must be greater than zero.")

    ranking = int(getattr(args, "ranking_limit", None) or os.getenv("RANKING_LIMIT") or selection.get("ranking_limit", 20))
    max_tokens = int(
        getattr(args, "max_completion_tokens", None)
        or os.getenv("MAX_COMPLETION_TOKENS")
        or execution.get("max_completion_tokens", 3000)
    )
    reasoning = (
        getattr(args, "reasoning_effort", None)
        or os.getenv("REASONING_EFFORT")
        or execution.get("reasoning_effort", "high")
    )
    if not 5 <= ranking <= 150:
        raise ExpertTeamError("ranking_limit must be between 5 and 150.")
    if not 256 <= max_tokens <= 32768:
        raise ExpertTeamError("max_completion_tokens must be between 256 and 32768.")
    if reasoning not in {"low", "medium", "high"}:
        raise ExpertTeamError("reasoning_effort must be low, medium, or high.")

    sorts = list(selection.get("catalog_sorts", []))
    if len(sorts) < 2:
        raise ExpertTeamError("catalog_sorts must contain at least two rankings.")
    caps = selection.get("soft_price_caps_per_million", {})
    catalog_arg = getattr(args, "catalog_file", None)
    return RunConfig(
        task=task,
        output_dir=Path(getattr(args, "output_dir", "v5-artifacts")),
        api_key=os.getenv("OPENROUTER_API_KEY"),
        quality_tier=tier,
        ranking_limit=ranking,
        minimum_context_length=int(selection.get("minimum_context_length", 16384)),
        candidate_pool_per_seat=int(selection.get("candidate_pool_per_seat", 12)),
        catalog_sorts=sorts,
        weights=weights,
        soft_price_cap=float(caps.get(tier, 15.0)),
        catalog_file=Path(catalog_arg) if catalog_arg else None,
        max_estimated_cost_usd=max_cost,
        budget_safety_factor=float(selection.get("budget_safety_factor", 1.25)),
        max_completion_tokens=max_tokens,
        judge_max_completion_tokens=int(execution.get("judge_max_completion_tokens", 4000)),
        reasoning_effort=reasoning,
        temperature=float(execution.get("temperature", 0.2)),
        catalog_timeout_seconds=int(execution.get("catalog_timeout_seconds", 30)),
        catalog_max_retries=int(execution.get("catalog_max_retries", 1)),
        model_timeout_seconds=int(execution.get("model_timeout_seconds", 240)),
        model_max_retries=int(execution.get("model_max_retries", 0)),
        maximum_replacements=int(execution.get("maximum_replacements", 2)),
        parallel_workers=int(execution.get("parallel_workers", 3)),
        judge_context_budget_chars=int(execution.get("judge_context_budget_chars", 120000)),
        require_all_experts=False,
        provider=dict(provider),
        dry_run=bool(getattr(args, "dry_run", False)),
        require_live_catalog=bool(getattr(args, "require_live_catalog", False)),
    )


def classify_task(task: str, run: RunConfig) -> TaskProfile:
    policy = load_json(POLICY_FILE)
    text = task.lower()
    keywords = policy["keywords"]
    scored = [(domain, sum(1 for term in terms if term in text)) for domain, terms in keywords.items()]
    scored = [item for item in scored if item[1] > 0]
    scored.sort(key=lambda item: (-item[1], list(keywords).index(item[0])))
    domains = [domain for domain, _ in scored] or ["general"]
    primary = domains[0]
    secondary = domains[1] if len(domains) > 1 else primary
    high_stakes = any(term in text for term in policy["high_stakes"])
    chinese = len(re.findall(r"[\u4e00-\u9fff]", task)) >= max(4, len(task) // 20)
    long_context = len(task) > 12000 or any(term in text for term in policy["long_context"])
    score = int(len(task) > 1200) + int(len(task) > 6000) + int(len(domains) >= 2) + int(len(domains) >= 3)
    score += 2 if high_stakes else 0
    score += int(any(term in text for term in ("compare", "evaluate", "strategy", "simulate", "red team", "比较", "评估", "策略", "推演", "红队")))
    complexity = "simple" if score <= 1 else "medium" if score <= 3 else "complex"
    context = max(run.minimum_context_length, int(len(task) / 2.5) + 3 * run.max_completion_tokens)
    if long_context:
        context = max(context, 65536)
    return TaskProfile(domains, primary, secondary, complexity, score, high_stakes, chinese, long_context, context)


def _expired(value: Any) -> bool:
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except ValueError:
        try:
            parsed = date.fromisoformat(str(value)[:10])
        except ValueError:
            return False
    return parsed <= date.today()


def fetch_catalog(run: RunConfig) -> Tuple[Dict[str, ModelInfo], str]:
    if run.catalog_file:
        payload = load_json(run.catalog_file).get("sorts")
        if not isinstance(payload, dict):
            raise ExpertTeamError("Fixture catalog must contain a sorts object.")
        source = f"fixture:{run.catalog_file}"
    else:
        if run.require_live_catalog and not run.api_key:
            raise ExpertTeamError("OPENROUTER_API_KEY is required for live catalog verification.")
        payload: Dict[str, Any] = {}
        errors: Dict[str, str] = {}

        def fetch_one(sort_name: str) -> Tuple[str, Dict[str, Any]]:
            query = urllib.parse.urlencode({"sort": sort_name, "output_modalities": "text"})
            data = request_json(
                f"{MODELS_URL}?{query}",
                run.api_key,
                run.catalog_timeout_seconds,
                run.catalog_max_retries,
            )
            return sort_name, data

        with ThreadPoolExecutor(max_workers=min(5, len(run.catalog_sorts))) as pool:
            futures = {pool.submit(fetch_one, name): name for name in run.catalog_sorts}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    sort_name, data = future.result()
                    payload[sort_name] = data
                except Exception as exc:  # noqa: BLE001 - degradation is explicit
                    errors[name] = str(exc)
        if len(payload) < 2:
            raise ExpertTeamError(f"Live catalog returned fewer than two usable rankings: {errors}")
        missing = sorted(set(run.catalog_sorts) - set(payload))
        source = "openrouter-live" + (f";degraded_missing={','.join(missing)}" if missing else "")

    models: Dict[str, ModelInfo] = {}
    for sort_name, sort_payload in payload.items():
        rows = sort_payload.get("data") if isinstance(sort_payload, dict) else None
        if not isinstance(rows, list):
            continue
        for rank, row in enumerate(rows, 1):
            if not isinstance(row, dict) or not isinstance(row.get("id"), str):
                continue
            model_id = row["id"]
            if model_id.startswith(ROUTER_PREFIXES) or ":online" in model_id or ":batch" in model_id:
                continue
            pricing = row.get("pricing") if isinstance(row.get("pricing"), dict) else {}
            top = row.get("top_provider") if isinstance(row.get("top_provider"), dict) else {}
            architecture = row.get("architecture") if isinstance(row.get("architecture"), dict) else {}
            reasoning = row.get("reasoning") if isinstance(row.get("reasoning"), dict) else {}
            if model_id not in models:
                models[model_id] = ModelInfo(
                    id=model_id,
                    name=str(row.get("name") or model_id),
                    description=str(row.get("description") or ""),
                    author=model_id.split("/", 1)[0],
                    context_length=int(row.get("context_length") or top.get("context_length") or 0),
                    max_completion_tokens=int(top.get("max_completion_tokens") or row.get("max_completion_tokens") or 0),
                    prompt_price_per_million=_ppm(pricing, "prompt"),
                    completion_price_per_million=_ppm(pricing, "completion"),
                    supported_parameters=[str(item) for item in row.get("supported_parameters", [])],
                    input_modalities=[str(item) for item in architecture.get("input_modalities", [])],
                    output_modalities=[str(item) for item in architecture.get("output_modalities", [])],
                    knowledge_cutoff=row.get("knowledge_cutoff"),
                    expiration_date=row.get("expiration_date"),
                    reasoning=dict(reasoning),
                )
            models[model_id].ranks[sort_name] = rank
    if not models:
        raise ExpertTeamError("No usable direct models were returned by the catalog.")
    return models, source


def _domain_fit(model: ModelInfo, domain: str) -> float:
    policy = load_json(POLICY_FILE)
    terms = policy["description_terms"].get(domain, policy["description_terms"]["general"])
    text = f"{model.id} {model.name} {model.description}".lower()
    return min(1.0, sum(1 for term in terms if term in text) / max(2.0, len(terms) / 2.0))


def _task_fit(model: ModelInfo, profile: TaskProfile) -> Tuple[float, List[str]]:
    policy = load_json(POLICY_FILE)
    primary = _domain_fit(model, profile.primary_domain)
    secondary = _domain_fit(model, profile.secondary_domain)
    score = 0.30 + primary * 0.30 + secondary * 0.12
    reasons: List[str] = []
    if primary:
        reasons.append(f"匹配核心领域{profile.primary_domain}")
    if profile.secondary_domain != profile.primary_domain and secondary:
        reasons.append(f"匹配交叉领域{profile.secondary_domain}")
    if profile.chinese and model.author.lower() in policy["chinese_authors"]:
        score += 0.16
        reasons.append("中文任务适配")
    if profile.complexity == "complex" and "reasoning" in model.supported_parameters:
        score += 0.08
        reasons.append("支持reasoning")
    if profile.high_stakes and "structured_outputs" in model.supported_parameters:
        score += 0.04
        reasons.append("支持结构化输出")
    return min(score, 1.0), reasons


def estimate_call_cost(model: ModelInfo, input_chars: int, output_tokens: int) -> float:
    if model.prompt_price_per_million is None or model.completion_price_per_million is None:
        raise ExpertTeamError(f"Pricing missing for {model.id}.")
    input_tokens = math.ceil(max(0, input_chars) / 4)
    return (
        input_tokens / 1_000_000 * model.prompt_price_per_million
        + output_tokens / 1_000_000 * model.completion_price_per_million
    )
