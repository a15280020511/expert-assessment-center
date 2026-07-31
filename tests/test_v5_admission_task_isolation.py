from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import model_market  # noqa: E402
from execution_graph import GraphLimits  # noqa: E402
from resource_matrix import compile_v5_task_resources  # noqa: E402
from v5_production_ticket import _canonical_user_task  # noqa: E402
from v5_runtime import ProductionRuntime, RuntimeConfig  # noqa: E402


QUESTION = (
    "真实决策任务：用户在福州市担任小学夜班保安，独自在岗亭值班；"
    "岗亭无法申请学校有线网络或室内Wi-Fi，但移动4G/5G流量可用；"
    "每月流量需求约100GB以上；希望长期月成本尽量控制在20元人民币左右，"
    "可以接受一次性购买随身Wi-Fi设备。请比较方案A：继续使用手机热点，"
    "与方案B：购买并使用随身Wi-Fi。仅依据题面已知条件分析，不调用外部工具、"
    "不联网、不编造具体套餐价格。必须给出：1）关键假设与未知信息；"
    "2）一次性成本和月成本的比较公式及盈亏平衡阈值；"
    "3）稳定性、信号、电池、发热、携带、维护和故障风险；"
    "4）什么条件下选A、什么条件下选B；5）明确推荐；"
    "6）一个低成本、可撤销的7天验证步骤。事实、假设和推断必须分开。"
)

DELEGATION_NOTICE = (
    "委托边界：外部网页 GPT 只负责提交用户问题和证据、报告运行状态；"
    "不得替代专家分析。"
)


def _run(task: str) -> SimpleNamespace:
    return SimpleNamespace(
        task=task,
        minimum_context_length=16_384,
        max_completion_tokens=3_000,
    )


def _capabilities(
    *,
    domain: float,
    general: float,
    reasoning: float,
    delivery: float,
    synthesis: float,
) -> dict[str, float]:
    return {
        "adversarial_reasoning": domain,
        "causal_reasoning": reasoning,
        "complex_reasoning": reasoning,
        "counterfactual_analysis": max(domain, reasoning - 0.12),
        "creative_generation": domain,
        "decision_comparison": domain,
        "delivery": delivery,
        "domain:business": domain,
        "domain:research": domain,
        "evidence_validation": domain,
        "forecasting": domain,
        "general_analysis": general,
        "implementation": general,
        "long_context": 1.0,
        "quantitative_reasoning": domain,
        "risk_discovery": domain,
        "statistics": domain,
        "structured_output": 0.9,
        "synthesis": synthesis,
    }


def _endpoint(
    model: str,
    provider: str,
    *,
    prompt: float,
    completion: float,
    benchmark: float,
    capabilities: dict[str, float],
) -> dict:
    return {
        "endpoint_id": f"frozen-{model}-{provider}",
        "model_id": model,
        "provider_slug": provider,
        "provider_endpoint": f"{model}@{provider}",
        "author": model.split("/", 1)[0],
        "context_length": 1_000_000,
        "max_completion_tokens": 128_000,
        "prompt_price_per_million": prompt,
        "completion_price_per_million": completion,
        "supported_parameters": [
            "include_reasoning",
            "max_tokens",
            "reasoning",
            "reasoning_effort",
            "response_format",
            "structured_outputs",
            "tool_choice",
            "tools",
        ],
        "input_modalities": ["file", "image", "text"],
        "output_modalities": ["text"],
        "capability_scores": capabilities,
        "benchmark_score": benchmark,
        "benchmark_confidence": 0.95,
        "reliability": 1.0,
        "synthetic_fixture_only": False,
    }


def _frozen_live_market() -> dict:
    endpoints = [
        _endpoint(
            "openai/gpt-5.6-luna",
            "openai/flex",
            prompt=0.05,
            completion=0.30,
            benchmark=0.66,
            capabilities=_capabilities(
                domain=0.4728,
                general=0.5924,
                reasoning=0.7224,
                delivery=0.5528,
                synthesis=0.7224,
            ),
        ),
        _endpoint(
            "openai/gpt-5.6-terra",
            "openai/flex",
            prompt=0.50,
            completion=3.00,
            benchmark=0.86,
            capabilities=_capabilities(
                domain=0.5688,
                general=0.6884,
                reasoning=0.8184,
                delivery=0.6488,
                synthesis=0.8184,
            ),
        ),
        _endpoint(
            "anthropic/claude-sonnet-5",
            "anthropic",
            prompt=2.00,
            completion=10.00,
            benchmark=0.74,
            capabilities=_capabilities(
                domain=0.5112,
                general=0.6308,
                reasoning=0.7608,
                delivery=0.5912,
                synthesis=0.7608,
            ),
        ),
        _endpoint(
            "anthropic/claude-opus-5",
            "anthropic",
            prompt=5.00,
            completion=25.00,
            benchmark=1.00,
            capabilities=_capabilities(
                domain=0.6360,
                general=0.8544,
                reasoning=0.8856,
                delivery=0.8356,
                synthesis=0.8856,
            ),
        ),
    ]
    labels = sorted({label for endpoint in endpoints for label in endpoint["capability_scores"]})
    return {
        "version": 5,
        "architecture": "frozen-live-endpoint-replay",
        "task_digest": "issue-113-live-catalog-20260731",
        "capability_labels": labels,
        "endpoints": endpoints,
        "endpoint_count": len(endpoints),
        "real_endpoint_count": len(endpoints),
        "synthetic_fixture_count": 0,
        "rejected": [],
        "phase_b_invariants": {
            "all_endpoints_real": True,
            "all_prices_present": True,
            "provider_lock_available": True,
            "cross_task_history_used": False,
        },
        "planning_policy": {
            "composition": "explicit-direct-call",
            "provider_reliability_floor": 0.9,
            "cross_task_history_used": False,
        },
        "catalog_source": "frozen-live-2026-07-31",
        "endpoint_source": "frozen-live-2026-07-31",
        "cross_task_history_used": False,
    }


class V5AdmissionTaskIsolationTests(unittest.TestCase):
    def test_canonical_task_excludes_runtime_delegation_notice(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ticket.json").write_text(
                json.dumps(
                    {
                        "task": {
                            "question": QUESTION,
                            "requirements": ["禁止使用外部工具"],
                            "language": "zh-CN",
                        }
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            canonical, source = _canonical_user_task(
                root,
                DELEGATION_NOTICE + "\n\n" + QUESTION,
            )
        self.assertEqual("ticket.task", source)
        self.assertTrue(canonical.startswith(QUESTION))
        self.assertNotIn("委托边界", canonical)
        self.assertNotIn("报告运行状态", canonical)
        self.assertIn("禁止使用外部工具", canonical)
        self.assertIn("输出语言：zh-CN", canonical)

    def test_consumer_trial_is_not_research_or_long_context(self) -> None:
        run = _run(QUESTION)
        profile = model_market.classify_task(QUESTION, run)
        self.assertEqual(["business"], profile.domains)
        self.assertEqual("business", profile.primary_domain)
        self.assertFalse(profile.high_stakes)
        self.assertFalse(profile.long_context)
        self.assertEqual(16_384, profile.requested_context)

        resources = compile_v5_task_resources(profile, run)
        signals = resources["task_semantics"]["task_signals"]
        self.assertEqual(["business"], signals["active_domains"])
        self.assertNotIn("research", signals["active_domains"])
        self.assertNotIn("evidence_validation", signals["active_operations"])
        self.assertTrue(
            resources["semantic_input_policy"]["trial_validation_disambiguated"]
        )
        self.assertLessEqual(
            min(len(row["atomic_work"]) for row in resources["task_semantics"]["interpretations"]),
            3,
        )

        hard = {
            item["capability"]
            for matrix in resources["resource_matrices"]["matrices"]
            for item in matrix["hard_requirements"]
        }
        self.assertNotIn("domain:business", hard)
        self.assertNotIn("evidence_validation", hard)
        self.assertIn("adversarial_reasoning", hard)
        self.assertIn("synthesis", hard)

    def test_frozen_live_market_replay_fits_original_budget(self) -> None:
        run = _run(QUESTION)
        profile = model_market.classify_task(QUESTION, run)
        resources = compile_v5_task_resources(profile, run)
        runtime = ProductionRuntime(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=1,
                cost_anomaly_usd=0.25,
                quality_tier="value",
                tools_allowed=False,
                live_catalog_required=True,
                provider_lock_required=True,
            )
        )
        candidates = runtime.planner_policy.generate_candidate_graph(
            resources,
            _frozen_live_market(),
            maximum_per_group=12,
        )
        optimization = runtime.planner_policy.optimize_execution_graph(
            candidates,
            limits=GraphLimits(
                max_nodes=3,
                max_edges=64,
                max_stages=8,
                max_model_calls=4,
                max_retries=1,
                max_replacements=1,
                max_budget_usd=0.25,
                cost_risk_multiplier=1.18,
            ),
            quality_tolerance_pct=2.0,
            solver_timeout_seconds=20.0,
        )
        graph = optimization["execution_graph"]
        self.assertIn(optimization["solver_status"], {"OPTIMAL", "FEASIBLE"})
        self.assertLessEqual(len(graph["nodes"]), 3)
        self.assertLessEqual(float(graph["estimated_total_cost"]), 0.25)
        self.assertFalse(candidates.get("cross_task_history_used", False))


if __name__ == "__main__":
    unittest.main()
