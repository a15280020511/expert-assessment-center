#!/usr/bin/env python3
"""Bounded live blind benchmark for V5, V3, and auditable baselines."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import model_market as market
from artifact_manifest import write_manifest
from execution_graph import ExecutionGraph, GraphLimits
from openrouter_api import CHAT_URL, OpenRouterRequestError, request_json
from resource_matrix import compile_v5_task_resources
from task_resource_artifacts import write_task_resource_artifacts
from v5_benchmark import live_cutover_gate
from v5_executor import V5ExecutionError, execute_v5_graph
from v5_pipeline import _rank_v5_models
from v5_planner import compile_and_optimize_v5, fetch_live_endpoint_payloads

HERE = Path(__file__).resolve().parent
DEFAULT_SUITE = HERE / "v5_live_benchmark_suite.json"
DEFAULT_CONFIG = HERE / "config.json"
STRATEGIES = (
    "v5_joint_graph",
    "v3",
    "strongest_single_model",
    "lowest_price_single_model",
    "fixed_3_plus_1",
    "random_feasible",
)
FORBIDDEN_FIELDS = {
    "tools", "tool_choice", "plugins", "web_search", "web_search_options",
    "file_search", "browser", "code_interpreter", "models",
}
JSON_OBJECT_RE = re.compile(r"\{.*\}", re.S)


class LiveBenchmarkError(RuntimeError):
    pass


class BenchmarkLimitExceeded(LiveBenchmarkError):
    pass


@dataclass
class GlobalLedger:
    max_cost_usd: float
    max_calls: int
    calls: int = 0
    actual_cost_usd: float = 0.0
    events: list[dict[str, Any]] = field(default_factory=list)

    def before_call(self, *, task_id: str, strategy: str, model: str) -> None:
        if self.calls >= self.max_calls:
            raise BenchmarkLimitExceeded(f"global call ceiling {self.max_calls} exhausted")
        if self.actual_cost_usd >= self.max_cost_usd - 1e-12:
            raise BenchmarkLimitExceeded(f"global actual cost ceiling {self.max_cost_usd:.4f} USD exhausted")
        self.calls += 1
        self.events.append({
            "kind": "call_reserved",
            "task_id": task_id,
            "strategy": strategy,
            "model": model,
            "call_index": self.calls,
        })

    def charge(self, *, task_id: str, strategy: str, model: str, cost_usd: float) -> None:
        value = max(0.0, float(cost_usd))
        self.actual_cost_usd += value
        self.events.append({
            "kind": "cost_charged",
            "task_id": task_id,
            "strategy": strategy,
            "model": model,
            "cost_usd": round(value, 8),
            "cumulative_cost_usd": round(self.actual_cost_usd, 8),
        })
        if self.actual_cost_usd > self.max_cost_usd + 1e-12:
            raise BenchmarkLimitExceeded(
                f"global actual cost {self.actual_cost_usd:.6f} USD exceeded ceiling {self.max_cost_usd:.6f} USD"
            )

    def add_external(self, *, task_id: str, strategy: str, calls: int, cost_usd: float) -> None:
        if self.calls + max(0, int(calls)) > self.max_calls:
            raise BenchmarkLimitExceeded(f"external strategy would exceed global call ceiling {self.max_calls}")
        self.calls += max(0, int(calls))
        self.actual_cost_usd += max(0.0, float(cost_usd))
        self.events.append({
            "kind": "external_strategy_accounted",
            "task_id": task_id,
            "strategy": strategy,
            "calls": max(0, int(calls)),
            "cost_usd": round(max(0.0, float(cost_usd)), 8),
            "cumulative_calls": self.calls,
            "cumulative_cost_usd": round(self.actual_cost_usd, 8),
        })
        if self.actual_cost_usd > self.max_cost_usd + 1e-12:
            raise BenchmarkLimitExceeded(
                f"external strategy caused total cost {self.actual_cost_usd:.6f} USD to exceed ceiling"
            )

    def remaining_cost(self) -> float:
        return max(0.0, self.max_cost_usd - self.actual_cost_usd)

    def snapshot(self) -> dict[str, Any]:
        return {
            "max_cost_usd": self.max_cost_usd,
            "max_calls": self.max_calls,
            "calls": self.calls,
            "actual_cost_usd": round(self.actual_cost_usd, 8),
            "remaining_cost_usd": round(self.remaining_cost(), 8),
            "events": list(self.events),
        }


@dataclass
class StrategyOutcome:
    task_id: str
    strategy: str
    status: str
    answer: str | None
    actual_cost_usd: float
    latency_seconds: float
    call_count: int
    models: list[str]
    providers: list[str]
    safety_failure: bool
    error: str | None = None
    artifacts: Mapping[str, Any] = field(default_factory=dict)

    def record(self) -> dict[str, Any]:
        return asdict(self)


def _write_output(name: str, value: Any) -> None:
    path = os.getenv("GITHUB_OUTPUT")
    if not path:
        return
    text = str(value).replace("\n", " ").replace("\r", " ")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={text}\n")


def _load_json(path: str | Path) -> Mapping[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise LiveBenchmarkError(f"JSON root must be an object: {path}")
    return value


def _write_json(path: str | Path, value: Any) -> None:
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _task_text(task: Mapping[str, Any]) -> str:
    requirements = task.get("requirements") if isinstance(task.get("requirements"), list) else []
    return str(task.get("question") or "").strip() + "\n\n执行要求：\n" + "\n".join(
        f"- {str(row)}" for row in requirements
    )


def _namespace(task: str, output_dir: Path, *, ranking_limit: int = 24, max_cost_usd: float | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        task=task,
        config=str(DEFAULT_CONFIG),
        output_dir=str(output_dir),
        quality_tier="value",
        ranking_limit=ranking_limit,
        max_estimated_cost_usd=None if max_cost_usd is None else str(max_cost_usd),
        max_completion_tokens=None,
        reasoning_effort="low",
        catalog_file=None,
        require_live_catalog=True,
        dry_run=False,
    )


def _actual_cost(response: Mapping[str, Any]) -> float:
    usage = response.get("usage") if isinstance(response.get("usage"), Mapping) else {}
    for key in ("cost", "total_cost"):
        try:
            if usage.get(key) is not None:
                return max(0.0, float(usage[key]))
        except (TypeError, ValueError):
            pass
    return 0.0


def _answer(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
        return ""
    message = choices[0].get("message") if isinstance(choices[0].get("message"), Mapping) else {}
    content = message.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(row.get("text")) for row in content
            if isinstance(row, Mapping) and isinstance(row.get("text"), str)
        ).strip()
    return ""


def _finish_reason(response: Mapping[str, Any]) -> str:
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], Mapping):
        return str(choices[0].get("finish_reason") or "")
    return ""


def _endpoint_cost(endpoint: Mapping[str, Any]) -> float:
    return 0.35 * float(endpoint.get("prompt_price_per_million", 0.0)) + 0.65 * float(
        endpoint.get("completion_price_per_million", 0.0)
    )


def _provider_payload(endpoint: Mapping[str, Any]) -> dict[str, Any]:
    slug = str(endpoint.get("provider_slug") or "")
    if not slug:
        raise LiveBenchmarkError("endpoint is missing provider_slug")
    return {
        "order": [slug],
        "only": [slug],
        "allow_fallbacks": False,
        "require_parameters": True,
    }


def _safe_payload(endpoint: Mapping[str, Any], system: str, user: str) -> dict[str, Any]:
    model_id = str(endpoint.get("model_id") or "")
    folded = model_id.casefold()
    if not model_id or model_id.startswith("openrouter/") or ":online" in folded or ":batch" in folded:
        raise LiveBenchmarkError(f"forbidden model in direct baseline: {model_id}")
    payload: dict[str, Any] = {
        "model": model_id,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "stream": False,
        "provider": _provider_payload(endpoint),
    }
    supported = {str(row).casefold() for row in endpoint.get("supported_parameters", [])}
    if "reasoning" in supported:
        payload["reasoning"] = {"effort": "low", "exclude": True}
    if "temperature" in supported:
        payload["temperature"] = 0.1
    if FORBIDDEN_FIELDS.intersection(payload):
        raise LiveBenchmarkError(f"forbidden request fields: {sorted(FORBIDDEN_FIELDS.intersection(payload))}")
    if "max_tokens" in payload or "max_completion_tokens" in payload:
        raise LiveBenchmarkError("artificial output token ceiling is forbidden")
    return payload


def _direct_call(
    run: Any,
    endpoint: Mapping[str, Any],
    payload: Mapping[str, Any],
    ledger: GlobalLedger,
    *,
    task_id: str,
    strategy: str,
) -> tuple[Mapping[str, Any], float]:
    model_id = str(endpoint.get("model_id") or payload.get("model") or "")
    ledger.before_call(task_id=task_id, strategy=strategy, model=model_id)
    started = time.monotonic()
    try:
        response = request_json(
            CHAT_URL,
            run.api_key,
            int(getattr(run, "model_timeout_seconds", 240)),
            0,
            dict(payload),
        )
    except OpenRouterRequestError as exc:
        raise LiveBenchmarkError(str(exc)) from exc
    latency = time.monotonic() - started
    ledger.charge(task_id=task_id, strategy=strategy, model=model_id, cost_usd=_actual_cost(response))
    return response, latency


def _successful_answer(response: Mapping[str, Any]) -> tuple[bool, str, str]:
    answer = _answer(response)
    finish = _finish_reason(response).casefold()
    if finish in {"length", "max_tokens"}:
        return False, answer, "truncated-output"
    if len(answer) < 160:
        return False, answer, "answer-too-short"
    return True, answer, ""


def _role_score(endpoint: Mapping[str, Any], labels: Sequence[str]) -> float:
    capabilities = endpoint.get("capability_scores") if isinstance(endpoint.get("capability_scores"), Mapping) else {}
    return sum(float(capabilities.get(label, 0.0)) for label in labels) / max(1, len(labels))


def _select_distinct(
    endpoints: Sequence[Mapping[str, Any]],
    role_labels: Sequence[Sequence[str]],
    *,
    random_seed: int | None = None,
) -> list[Mapping[str, Any]]:
    pool = [row for row in endpoints if int(row.get("context_length", 0)) >= 32768]
    if len({str(row.get("model_id")) for row in pool}) < len(role_labels):
        pool = list(endpoints)
    selected: list[Mapping[str, Any]] = []
    used_models: set[str] = set()
    used_providers: set[str] = set()
    rng = random.Random(random_seed)
    for labels in role_labels:
        candidates = [row for row in pool if str(row.get("model_id")) not in used_models]
        if not candidates:
            candidates = list(pool)
        if random_seed is None:
            candidates.sort(key=lambda row: (
                -_role_score(row, labels),
                str(row.get("provider_slug")) in used_providers,
                -float(row.get("reliability", 0.0)),
                _endpoint_cost(row),
                str(row.get("endpoint_id")),
            ))
        else:
            rng.shuffle(candidates)
            candidates.sort(key=lambda row: (
                str(row.get("provider_slug")) in used_providers,
                -float(row.get("reliability", 0.0)),
            ))
        chosen = candidates[0]
        selected.append(chosen)
        used_models.add(str(chosen.get("model_id")))
        used_providers.add(str(chosen.get("provider_slug")))
    return selected


def _single_strategy(
    run: Any,
    task: Mapping[str, Any],
    endpoint: Mapping[str, Any],
    ledger: GlobalLedger,
    strategy: str,
) -> StrategyOutcome:
    task_id = str(task["task_id"])
    system = (
        "你是独立单模型基线。禁止网页、搜索、工具、文件、代码执行、API或其他模型。"
        "仅依据题目提供的数据完成最终答案。必须覆盖计算、假设、不确定性、风险、建议和否决条件；不要展示隐藏思维过程。"
    )
    payload = _safe_payload(endpoint, system, _task_text(task))
    started = time.monotonic()
    try:
        response, _ = _direct_call(run, endpoint, payload, ledger, task_id=task_id, strategy=strategy)
        passed, answer, error = _successful_answer(response)
        return StrategyOutcome(
            task_id=task_id,
            strategy=strategy,
            status="success" if passed else "failed",
            answer=answer or None,
            actual_cost_usd=_actual_cost(response),
            latency_seconds=round(time.monotonic() - started, 6),
            call_count=1,
            models=[str(endpoint.get("model_id"))],
            providers=[str(endpoint.get("provider_slug"))],
            safety_failure=False,
            error=error or None,
            artifacts={"request": payload, "response_id": response.get("id")},
        )
    except Exception as exc:  # noqa: BLE001
        return StrategyOutcome(
            task_id=task_id,
            strategy=strategy,
            status="failed",
            answer=None,
            actual_cost_usd=0.0,
            latency_seconds=round(time.monotonic() - started, 6),
            call_count=1,
            models=[str(endpoint.get("model_id"))],
            providers=[str(endpoint.get("provider_slug"))],
            safety_failure=isinstance(exc, LiveBenchmarkError) and "forbidden" in str(exc).casefold(),
            error=str(exc),
        )


def _team_strategy(
    run: Any,
    task: Mapping[str, Any],
    endpoints: Sequence[Mapping[str, Any]],
    ledger: GlobalLedger,
    strategy: str,
) -> StrategyOutcome:
    task_id = str(task["task_id"])
    roles = (
        ("定量与证据专家", "重点检查计算、数据一致性、假设和证据缺口。"),
        ("领域与决策专家", "重点比较方案、权衡、实施约束和最终建议。"),
        ("独立红队专家", "重点寻找反例、失败路径、脆弱假设和否决条件。"),
    )
    started = time.monotonic()
    answers: list[str] = []
    costs = 0.0
    requests: list[Mapping[str, Any]] = []
    models: list[str] = []
    providers: list[str] = []
    try:
        for endpoint, (role, mission) in zip(endpoints[:3], roles):
            system = (
                f"你是固定3+1基线中的{role}。{mission}"
                "禁止网页、搜索、工具、文件、代码执行、API或其他模型。只能依据题目文本，输出完整中文结果，不展示隐藏思维过程。"
            )
            payload = _safe_payload(endpoint, system, _task_text(task))
            requests.append(payload)
            response, _ = _direct_call(run, endpoint, payload, ledger, task_id=task_id, strategy=strategy)
            costs += _actual_cost(response)
            passed, answer, error = _successful_answer(response)
            if not passed:
                raise LiveBenchmarkError(f"expert baseline failed: {error}")
            answers.append(answer)
            models.append(str(endpoint.get("model_id")))
            providers.append(str(endpoint.get("provider_slug")))
        judge_endpoint = endpoints[3]
        judge_system = (
            "你是固定3+1基线的裁判。禁止网页、搜索、工具、文件、代码执行、API或其他模型。"
            "比较三份专家结果，核对计算与约束，解决分歧，输出一个完整最终答案；不要按多数表决，不展示隐藏思维过程。"
        )
        judge_user = _task_text(task) + "\n\n三份专家结果：\n" + "\n\n".join(
            f"### 专家{index}\n{answer}" for index, answer in enumerate(answers, 1)
        )
        judge_payload = _safe_payload(judge_endpoint, judge_system, judge_user)
        requests.append(judge_payload)
        response, _ = _direct_call(run, judge_endpoint, judge_payload, ledger, task_id=task_id, strategy=strategy)
        costs += _actual_cost(response)
        passed, final_answer, error = _successful_answer(response)
        models.append(str(judge_endpoint.get("model_id")))
        providers.append(str(judge_endpoint.get("provider_slug")))
        return StrategyOutcome(
            task_id=task_id,
            strategy=strategy,
            status="success" if passed else "failed",
            answer=final_answer or None,
            actual_cost_usd=round(costs, 8),
            latency_seconds=round(time.monotonic() - started, 6),
            call_count=4,
            models=models,
            providers=providers,
            safety_failure=False,
            error=error or None,
            artifacts={"requests": requests, "expert_answers": answers},
        )
    except Exception as exc:  # noqa: BLE001
        return StrategyOutcome(
            task_id=task_id,
            strategy=strategy,
            status="failed",
            answer=None,
            actual_cost_usd=round(costs, 8),
            latency_seconds=round(time.monotonic() - started, 6),
            call_count=len(requests),
            models=models,
            providers=providers,
            safety_failure="forbidden" in str(exc).casefold(),
            error=str(exc),
            artifacts={"requests": requests, "expert_answers": answers},
        )


def _parse_v3_cost(root: Path) -> float:
    for name in ("cost-evidence.json", "expert-team-result.json"):
        path = root / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for key in ("provider_actual_team_cost_usd", "actual_cost_usd", "conservative_team_cost_usd"):
            try:
                if data.get(key) is not None:
                    return max(0.0, float(data[key]))
            except (TypeError, ValueError):
                pass
    return 0.0


def _parse_v3_calls(root: Path) -> int:
    path = root / "request-audit.json"
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return int(data.get("expected_request_count") or data.get("captured_request_count") or 0)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    return 4


def _v3_strategy(task: Mapping[str, Any], root: Path, ledger: GlobalLedger, strategy_cap: float) -> StrategyOutcome:
    task_id = str(task["task_id"])
    root.mkdir(parents=True, exist_ok=True)
    task_path = root / "task.txt"
    task_path.write_text(_task_text(task), encoding="utf-8")
    command = [
        sys.executable,
        str(HERE / "expert_team_hardened.py"),
        "--task",
        _task_text(task),
        "--quality-tier",
        "value",
        "--require-live-catalog",
        "--max-estimated-cost-usd",
        str(strategy_cap),
        "--output-dir",
        str(root),
    ]
    env = os.environ.copy()
    env["TOTAL_MODEL_CALLS"] = "6"
    env["EXPERT_MAX_REPLACEMENTS"] = "2"
    started = time.monotonic()
    completed = subprocess.run(command, env=env, capture_output=True, text=True, timeout=2400, check=False)
    (root / "benchmark-subprocess.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (root / "benchmark-subprocess.stderr.log").write_text(completed.stderr, encoding="utf-8")
    cost = _parse_v3_cost(root)
    calls = _parse_v3_calls(root)
    ledger.add_external(task_id=task_id, strategy="v3", calls=calls, cost_usd=cost)
    result_path = root / "expert-team-result.json"
    result: Mapping[str, Any] = {}
    if result_path.exists():
        try:
            loaded = json.loads(result_path.read_text(encoding="utf-8"))
            result = loaded if isinstance(loaded, Mapping) else {}
        except (OSError, json.JSONDecodeError):
            result = {}
    audit_path = root / "request-audit.json"
    audit: Mapping[str, Any] = {}
    if audit_path.exists():
        try:
            loaded = json.loads(audit_path.read_text(encoding="utf-8"))
            audit = loaded if isinstance(loaded, Mapping) else {}
        except (OSError, json.JSONDecodeError):
            audit = {}
    models = []
    providers = []
    selection_path = root / "model-selection.json"
    if selection_path.exists():
        try:
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            models = [str(row.get("model")) for row in selection.get("experts", []) if isinstance(row, Mapping)]
            judge = selection.get("judge") if isinstance(selection.get("judge"), Mapping) else {}
            if judge.get("model"):
                models.append(str(judge["model"]))
        except (OSError, json.JSONDecodeError):
            pass
    answer = str(result.get("final_answer") or "").strip()
    success = completed.returncode == 0 and len(answer) >= 160
    safety_failure = bool(audit and audit.get("status") != "PASS")
    return StrategyOutcome(
        task_id=task_id,
        strategy="v3",
        status="success" if success else "failed",
        answer=answer or None,
        actual_cost_usd=round(cost, 8),
        latency_seconds=round(time.monotonic() - started, 6),
        call_count=calls,
        models=models,
        providers=providers,
        safety_failure=safety_failure,
        error=None if success else (completed.stderr[-2000:] or "V3 execution failed"),
        artifacts={"returncode": completed.returncode, "request_audit": dict(audit)},
    )


def _v5_strategy(
    task: Mapping[str, Any],
    root: Path,
    ledger: GlobalLedger,
    models: Mapping[str, Any],
    endpoint_cache: dict[str, Mapping[str, Any]],
    strategy_cap: float,
) -> tuple[StrategyOutcome, Mapping[str, Any]]:
    task_id = str(task["task_id"])
    started = time.monotonic()
    run = market.build_run_config(_namespace(_task_text(task), root, max_cost_usd=strategy_cap))
    profile = market.classify_task(run.task, run)
    ranked = _rank_v5_models(models, profile, run)
    missing = [row for row in ranked[:24] if row.id not in endpoint_cache]
    if missing:
        endpoint_cache.update(fetch_live_endpoint_payloads(missing, run, maximum_models=24))
    payloads = {row.id: endpoint_cache.get(row.id, {}) for row in ranked[:24]}
    resources = compile_v5_task_resources(profile, run)
    write_task_resource_artifacts(resources, root)
    limits = GraphLimits(
        max_nodes=16,
        max_edges=64,
        max_stages=8,
        max_model_calls=16,
        max_retries=1,
        max_replacements=2,
        max_budget_usd=strategy_cap,
    )
    planner = compile_and_optimize_v5(
        ranked,
        resources,
        endpoint_payloads=payloads,
        allow_synthetic_fixture=False,
        ranking_limit=24,
        limits=limits,
        maximum_per_group=12,
        quality_tolerance_pct=2.0,
        solver_timeout_seconds=20.0,
    )
    _write_json(root / "v5-model-endpoint-market.json", planner["market"])
    _write_json(root / "v5-candidate-graph.json", planner["candidate_graph"])
    _write_json(root / "v5-optimization.json", planner["optimization"])
    _write_json(root / "v5-execution-graph.json", planner["optimization"]["execution_graph"])
    graph = ExecutionGraph.from_mapping(planner["optimization"]["execution_graph"])
    result: Mapping[str, Any] = {}
    error = ""
    try:
        result = execute_v5_graph(graph, run, run.task, output_dir=root, limits=limits)
    except V5ExecutionError as exc:
        error = str(exc)
        summary = root / "v5-execution-summary.json"
        if summary.exists():
            loaded = json.loads(summary.read_text(encoding="utf-8"))
            result = loaded if isinstance(loaded, Mapping) else {}
    cost = float(result.get("actual_cost_usd", 0.0) or 0.0)
    budget = result.get("execution_budget") if isinstance(result.get("execution_budget"), Mapping) else {}
    calls = int(budget.get("calls_reserved", 0) or 0)
    ledger.add_external(task_id=task_id, strategy="v5_joint_graph", calls=calls, cost_usd=cost)
    answer = str(result.get("final_answer") or "").strip()
    audit_path = root / "v5-request-audit.json"
    audit = _load_json(audit_path) if audit_path.exists() else {}
    success = result.get("status") == "success" and len(answer) >= 160
    outcome = StrategyOutcome(
        task_id=task_id,
        strategy="v5_joint_graph",
        status="success" if success else "failed",
        answer=answer or None,
        actual_cost_usd=round(cost, 8),
        latency_seconds=round(time.monotonic() - started, 6),
        call_count=calls,
        models=sorted({node.model for node in graph.nodes}),
        providers=sorted({node.provider_endpoint for node in graph.nodes}),
        safety_failure=bool(audit and audit.get("status") != "PASS"),
        error=None if success else (error or "V5 execution failed"),
        artifacts={
            "execution_budget": dict(budget),
            "request_audit": dict(audit),
            "selected_interpretation": planner["optimization"].get("selected_interpretation"),
        },
    )
    return outcome, planner["market"]


def _extract_json_object(text: str) -> Mapping[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*|\s*```$", "", candidate, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(candidate)
        if isinstance(parsed, Mapping):
            return parsed
    except json.JSONDecodeError:
        pass
    match = JSON_OBJECT_RE.search(candidate)
    if not match:
        raise LiveBenchmarkError("judge response did not contain a JSON object")
    parsed = json.loads(match.group(0))
    if not isinstance(parsed, Mapping):
        raise LiveBenchmarkError("judge JSON root must be an object")
    return parsed


def _judge_prompt(task: Mapping[str, Any], anonymous_outputs: Mapping[str, str]) -> tuple[str, str]:
    rubric = task.get("rubric") if isinstance(task.get("rubric"), Mapping) else {}
    system = (
        "你是严格的匿名基准裁判。禁止网页、搜索、工具、文件、代码执行、API或其他模型。"
        "你不知道候选答案来自何种系统，不得猜测来源。只按题目、评分标准和答案正文评分。"
        "输出一个JSON对象，不要输出Markdown或解释性前缀。"
    )
    user = (
        "任务：\n" + _task_text(task)
        + "\n\n评分标准：\n" + json.dumps(rubric, ensure_ascii=False, sort_keys=True)
        + "\n\n匿名候选答案：\n" + "\n\n".join(
            f"### 候选 {label}\n{answer[:12000]}" for label, answer in anonymous_outputs.items()
        )
        + "\n\n请返回：{\"scores\":{\"候选标签\":{\"criterion_scores\":{\"标准ID\":0到100},"
        "\"total_score\":0到100,\"fatal_errors\":[字符串],\"brief_reason\":字符串}},"
        "\"ranking\":[候选标签],\"global_notes\":字符串}。"
        "total_score必须与各项权重一致；发现fatal_errors时仍需评分，但说明致命错误。"
    )
    return system, user


def _judge_endpoints(market_bundle: Mapping[str, Any], used_models: set[str]) -> list[Mapping[str, Any]]:
    endpoints = [row for row in market_bundle.get("endpoints", []) if isinstance(row, Mapping)]
    endpoints.sort(key=lambda row: (
        str(row.get("model_id")) in used_models,
        -_role_score(row, ("synthesis", "evidence_validation", "complex_reasoning", "delivery")),
        -float(row.get("benchmark_score", 0.0)),
        -float(row.get("reliability", 0.0)),
        _endpoint_cost(row),
    ))
    selected: list[Mapping[str, Any]] = []
    models: set[str] = set()
    providers: set[str] = set()
    for row in endpoints:
        model_id = str(row.get("model_id"))
        provider = str(row.get("provider_slug"))
        if model_id in models:
            continue
        if len(selected) < 2 and provider in providers:
            continue
        selected.append(row)
        models.add(model_id)
        providers.add(provider)
        if len(selected) >= 4:
            break
    return selected


def _evaluate_task(
    run: Any,
    task: Mapping[str, Any],
    outcomes: Sequence[StrategyOutcome],
    market_bundle: Mapping[str, Any],
    ledger: GlobalLedger,
    root: Path,
) -> tuple[dict[str, float], dict[str, Any]]:
    task_id = str(task["task_id"])
    rng = random.Random(int(hashlib.sha256(task_id.encode()).hexdigest()[:12], 16))
    labels = [f"C{index + 1}" for index in range(len(outcomes))]
    rng.shuffle(labels)
    strategy_to_label = {outcome.strategy: label for outcome, label in zip(outcomes, labels)}
    label_to_strategy = {label: strategy for strategy, label in strategy_to_label.items()}
    anonymous_outputs = {
        strategy_to_label[outcome.strategy]: outcome.answer or "[EXECUTION_FAILED: 未生成可用答案]"
        for outcome in outcomes
    }
    used_models = {model for outcome in outcomes for model in outcome.models}
    judges = _judge_endpoints(market_bundle, used_models)
    if len(judges) < 2:
        raise LiveBenchmarkError("fewer than two distinct judge model/provider endpoints are available")
    system, user = _judge_prompt(task, anonymous_outputs)
    successful: list[dict[str, Any]] = []
    attempts: list[dict[str, Any]] = []
    for endpoint in judges:
        payload = _safe_payload(endpoint, system, user)
        try:
            response, latency = _direct_call(
                run, endpoint, payload, ledger, task_id=task_id, strategy="blind_judge"
            )
            parsed = _extract_json_object(_answer(response))
            score_rows = parsed.get("scores") if isinstance(parsed.get("scores"), Mapping) else {}
            if not all(label in score_rows for label in anonymous_outputs):
                raise LiveBenchmarkError("judge JSON omitted one or more anonymous candidates")
            normalized: dict[str, Any] = {}
            for label in anonymous_outputs:
                row = score_rows[label] if isinstance(score_rows[label], Mapping) else {}
                score = max(0.0, min(100.0, float(row.get("total_score", 0.0))))
                fatal = row.get("fatal_errors") if isinstance(row.get("fatal_errors"), list) else []
                normalized[label] = {
                    "total_score": score,
                    "fatal_errors": [str(item) for item in fatal],
                    "brief_reason": str(row.get("brief_reason") or ""),
                    "criterion_scores": dict(row.get("criterion_scores") or {}) if isinstance(row.get("criterion_scores"), Mapping) else {},
                }
            item = {
                "model": endpoint.get("model_id"),
                "provider": endpoint.get("provider_slug"),
                "latency_seconds": round(latency, 6),
                "cost_usd": _actual_cost(response),
                "scores": normalized,
                "ranking": list(parsed.get("ranking") or []),
                "global_notes": str(parsed.get("global_notes") or ""),
            }
            successful.append(item)
            attempts.append({"status": "success", **item})
        except Exception as exc:  # noqa: BLE001
            attempts.append({
                "status": "failed",
                "model": endpoint.get("model_id"),
                "provider": endpoint.get("provider_slug"),
                "error": str(exc),
            })
        if len(successful) >= 2:
            differences = [
                abs(float(successful[0]["scores"][label]["total_score"]) - float(successful[1]["scores"][label]["total_score"]))
                for label in anonymous_outputs
            ]
            if max(differences, default=0.0) <= 15.0 or len(successful) >= 3:
                break
    if len(successful) < 2:
        raise LiveBenchmarkError("fewer than two blind judges returned valid complete score JSON")
    scores: dict[str, float] = {}
    fatal_by_strategy: dict[str, bool] = {}
    disagreement_by_strategy: dict[str, float] = {}
    for label, strategy in label_to_strategy.items():
        values = [float(row["scores"][label]["total_score"]) for row in successful]
        fatal_votes = sum(bool(row["scores"][label]["fatal_errors"]) for row in successful)
        average = sum(values) / len(values)
        if fatal_votes > len(successful) / 2:
            average = min(average, 40.0)
        scores[strategy] = round(average / 100.0, 6)
        fatal_by_strategy[strategy] = fatal_votes > len(successful) / 2
        disagreement_by_strategy[strategy] = round(max(values) - min(values), 6)
    audit = {
        "version": 1,
        "task_id": task_id,
        "strategy_to_anonymous_label": strategy_to_label,
        "judge_count": len(successful),
        "judge_models": [str(row["model"]) for row in successful],
        "judge_providers": [str(row["provider"]) for row in successful],
        "distinct_judge_models": len({str(row["model"]) for row in successful}),
        "distinct_judge_providers": len({str(row["provider"]) for row in successful}),
        "fatal_by_strategy": fatal_by_strategy,
        "disagreement_points_by_strategy": disagreement_by_strategy,
        "attempts": attempts,
    }
    _write_json(root / "blind-evaluation.json", audit)
    return scores, audit


def _summary_markdown(bundle: Mapping[str, Any]) -> str:
    gate = bundle.get("cutover_gate") if isinstance(bundle.get("cutover_gate"), Mapping) else {}
    lines = [
        "# V5 Live Blind Benchmark",
        "",
        f"- Status: `{bundle.get('status')}`",
        f"- Benchmark ID: `{bundle.get('benchmark_id')}`",
        f"- Tasks completed: `{bundle.get('tasks_completed')}` / `{bundle.get('tasks_requested')}`",
        f"- Paid calls: `{bundle.get('ledger', {}).get('calls')}`",
        f"- Actual cost: `${float(bundle.get('ledger', {}).get('actual_cost_usd', 0.0)):.6f}`",
        f"- Production cutover allowed: `{str(bool(gate.get('production_cutover_allowed'))).lower()}`",
        "",
        "## Strategy summary",
        "",
        "| Strategy | Tasks | Success | Blind quality | Mean cost USD | Safety failures |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for strategy, row in sorted((gate.get("summaries") or {}).items()):
        lines.append(
            f"| {strategy} | {row.get('task_count', 0)} | {float(row.get('success_rate', 0.0)):.3f} | "
            f"{float(row.get('mean_blind_quality', 0.0)):.3f} | {float(row.get('mean_cost_usd', 0.0)):.6f} | {row.get('safety_failures', 0)} |"
        )
    lines.extend(["", "## Cutover blockers", ""])
    blockers = list(gate.get("blockers") or [])
    lines.extend(f"- `{row}`" for row in blockers) if blockers else lines.append("- None")
    return "\n".join(lines) + "\n"


def prepare(event_path: str | Path, output_dir: str | Path) -> int:
    event = _load_json(event_path)
    issue = event.get("issue") if isinstance(event.get("issue"), Mapping) else {}
    body = str(issue.get("body") or "").strip()
    raw: Mapping[str, Any] = {}
    if body:
        try:
            parsed = json.loads(body)
        except json.JSONDecodeError as exc:
            raise LiveBenchmarkError(f"Issue body must be one JSON object: {exc}") from exc
        if not isinstance(parsed, Mapping):
            raise LiveBenchmarkError("Issue body must be one JSON object")
        raw = parsed
    allowed = {"benchmark_id", "max_cost_usd", "max_calls", "max_strategy_cost_usd", "task_ids"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise LiveBenchmarkError(f"Unknown benchmark config fields: {unknown}")
    suite = _load_json(DEFAULT_SUITE)
    available = [str(row.get("task_id")) for row in suite.get("tasks", []) if isinstance(row, Mapping)]
    task_ids = [str(row) for row in raw.get("task_ids", available)]
    if not task_ids or any(row not in available for row in task_ids):
        raise LiveBenchmarkError("task_ids must select one or more known suite tasks")
    config = {
        "version": 1,
        "benchmark_id": str(raw.get("benchmark_id") or suite.get("benchmark_id") or "v5-live-benchmark"),
        "max_cost_usd": max(1.0, min(50.0, float(raw.get("max_cost_usd", 20.0)))),
        "max_calls": max(30, min(300, int(raw.get("max_calls", 200)))),
        "max_strategy_cost_usd": max(0.25, min(10.0, float(raw.get("max_strategy_cost_usd", 4.0)))),
        "task_ids": task_ids,
        "issue_number": int(issue.get("number") or 0),
    }
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    _write_json(root / "benchmark-config.json", config)
    _write_output("accepted", "true")
    _write_output("benchmark_id", config["benchmark_id"])
    _write_output("max_cost_usd", config["max_cost_usd"])
    _write_output("max_calls", config["max_calls"])
    return 0


def run_benchmark(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    if not os.getenv("OPENROUTER_API_KEY"):
        raise LiveBenchmarkError("OPENROUTER_API_KEY is not set")
    config = _load_json(config_path)
    suite = _load_json(suite_path)
    requested = {str(row) for row in config.get("task_ids", [])}
    tasks = [row for row in suite.get("tasks", []) if isinstance(row, Mapping) and str(row.get("task_id")) in requested]
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    ledger = GlobalLedger(float(config["max_cost_usd"]), int(config["max_calls"]))
    strategy_cap = min(float(config["max_strategy_cost_usd"]), ledger.max_cost_usd)
    catalog_run = market.build_run_config(_namespace(_task_text(tasks[0]), root / "catalog", ranking_limit=24))
    models, catalog_source = market.fetch_catalog(catalog_run)
    endpoint_cache: dict[str, Mapping[str, Any]] = {}
    records: list[dict[str, Any]] = []
    task_bundles: list[dict[str, Any]] = []
    status = "success"
    error = ""
    for task in tasks:
        task_id = str(task["task_id"])
        task_root = root / "tasks" / task_id
        task_root.mkdir(parents=True, exist_ok=True)
        outcomes: list[StrategyOutcome] = []
        try:
            v5_outcome, market_bundle = _v5_strategy(
                task,
                task_root / "v5_joint_graph",
                ledger,
                models,
                endpoint_cache,
                min(strategy_cap, max(0.25, ledger.remaining_cost())),
            )
            outcomes.append(v5_outcome)
            v3_outcome = _v3_strategy(
                task,
                task_root / "v3",
                ledger,
                min(strategy_cap, max(0.25, ledger.remaining_cost())),
            )
            outcomes.append(v3_outcome)
            endpoint_rows = [row for row in market_bundle.get("endpoints", []) if isinstance(row, Mapping)]
            strongest = sorted(endpoint_rows, key=lambda row: (
                -float(row.get("benchmark_score", 0.0)),
                -float(row.get("reliability", 0.0)),
                _endpoint_cost(row),
            ))[0]
            cheapest = sorted(endpoint_rows, key=lambda row: (
                _endpoint_cost(row),
                -float(row.get("benchmark_score", 0.0)),
                -float(row.get("reliability", 0.0)),
            ))[0]
            direct_run = market.build_run_config(_namespace(_task_text(task), task_root / "direct", ranking_limit=24))
            outcomes.append(_single_strategy(direct_run, task, strongest, ledger, "strongest_single_model"))
            outcomes.append(_single_strategy(direct_run, task, cheapest, ledger, "lowest_price_single_model"))
            fixed_endpoints = _select_distinct(
                endpoint_rows,
                (
                    ("quantitative_reasoning", "evidence_validation", "statistics"),
                    ("general_analysis", "decision_comparison", "delivery"),
                    ("adversarial_reasoning", "risk_discovery", "evidence_validation"),
                    ("synthesis", "complex_reasoning", "delivery"),
                ),
            )
            outcomes.append(_team_strategy(direct_run, task, fixed_endpoints, ledger, "fixed_3_plus_1"))
            seed = int(hashlib.sha256(task_id.encode()).hexdigest()[:12], 16)
            random_endpoints = _select_distinct(
                endpoint_rows,
                (("general_analysis",), ("general_analysis",), ("general_analysis",), ("synthesis",)),
                random_seed=seed,
            )
            outcomes.append(_team_strategy(direct_run, task, random_endpoints, ledger, "random_feasible"))
            scores, evaluation = _evaluate_task(
                direct_run,
                task,
                outcomes,
                market_bundle,
                ledger,
                task_root,
            )
            for outcome in outcomes:
                row = outcome.record()
                row["blind_quality_score"] = scores.get(outcome.strategy, 0.0)
                row["blind_judge_count"] = evaluation["judge_count"]
                row["blind_judge_models"] = evaluation["judge_models"]
                row["blind_judge_providers"] = evaluation["judge_providers"]
                row["blind_fatal_error"] = bool(evaluation["fatal_by_strategy"].get(outcome.strategy))
                row["blind_judge_disagreement_points"] = float(
                    evaluation["disagreement_points_by_strategy"].get(outcome.strategy, 100.0)
                )
                records.append(row)
            task_bundle = {
                "task_id": task_id,
                "domain": task.get("domain"),
                "outcomes": [outcome.record() for outcome in outcomes],
                "blind_scores": scores,
                "evaluation": evaluation,
                "ledger_after_task": ledger.snapshot(),
            }
            task_bundles.append(task_bundle)
            _write_json(task_root / "task-benchmark-result.json", task_bundle)
        except BenchmarkLimitExceeded as exc:
            status = "budget_or_call_limit_exceeded"
            error = str(exc)
            break
        except Exception as exc:  # noqa: BLE001
            status = "technical_failure"
            error = f"task {task_id}: {exc}"
            task_bundles.append({
                "task_id": task_id,
                "status": "technical_failure",
                "error": str(exc),
                "outcomes": [outcome.record() for outcome in outcomes],
            })
            break
    cutover = live_cutover_gate(records)
    bundle = {
        "version": 1,
        "benchmark_id": config["benchmark_id"],
        "status": status,
        "error": error or None,
        "catalog_source": catalog_source,
        "tasks_requested": len(tasks),
        "tasks_completed": len({row.get("task_id") for row in records}),
        "strategies": list(STRATEGIES),
        "records": records,
        "task_bundles": task_bundles,
        "ledger": ledger.snapshot(),
        "cutover_gate": cutover,
        "production_entrypoint_changed": False,
    }
    _write_json(root / "v5-live-benchmark-results.json", bundle)
    (root / "v5-live-benchmark-summary.md").write_text(_summary_markdown(bundle), encoding="utf-8")
    write_manifest(root)
    return 0 if status == "success" else 2


def render(output_dir: str | Path, run_url: str) -> int:
    root = Path(output_dir)
    summary = root / "v5-live-benchmark-summary.md"
    if not summary.exists():
        print("## V5_BENCHMARK_FAILED\n\nBenchmark summary was not generated.\n")
        return 2
    text = summary.read_text(encoding="utf-8")
    result = _load_json(root / "v5-live-benchmark-results.json")
    artifact_note = (
        f"\n- Run: `{run_url}`\n"
        f"- Production entrypoint changed: `{str(bool(result.get('production_entrypoint_changed'))).lower()}`\n"
    )
    print("## V5_BENCHMARK_COMPLETED\n\n" + text + artifact_note)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the bounded V5 live blind benchmark.")
    sub = parser.add_subparsers(dest="command", required=True)
    prepare_parser = sub.add_parser("prepare")
    prepare_parser.add_argument("--event-path", required=True)
    prepare_parser.add_argument("--output-dir", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--config", required=True)
    run_parser.add_argument("--suite", default=str(DEFAULT_SUITE))
    run_parser.add_argument("--output-dir", required=True)
    render_parser = sub.add_parser("render")
    render_parser.add_argument("--output-dir", required=True)
    render_parser.add_argument("--run-url", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "prepare":
            return prepare(args.event_path, args.output_dir)
        if args.command == "run":
            return run_benchmark(args.config, args.suite, args.output_dir)
        if args.command == "render":
            return render(args.output_dir, args.run_url)
        raise LiveBenchmarkError(f"unsupported command {args.command}")
    except (LiveBenchmarkError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
