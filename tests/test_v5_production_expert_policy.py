from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

from v5_production_expert_policy import (  # noqa: E402
    EXPERT_DATA_COLLECTION_POLICY,
    EXPERT_ZDR_REQUIRED,
    EvidenceCompleteExecutionEngine,
    ProductionExpertPromptPolicy,
    install_production_expert_policy,
)
from v5_runtime import ProductionRuntime, RetryPolicy, RuntimeAttempt, RuntimeConfig  # noqa: E402
from v5_soft_resource_governance import SoftResourcePromptPolicy, build_runtime  # noqa: E402
from v5_task_constraints import compile_task_constraints  # noqa: E402


class ProductionExpertPolicyTests(unittest.TestCase):
    @staticmethod
    def _installed_runtime() -> ProductionRuntime:
        config = RuntimeConfig(
            total_call_limit=5,
            recovery_call_limit=1,
            cost_anomaly_usd=1.0,
            tools_allowed=False,
            live_catalog_required=True,
            provider_lock_required=False,
        )
        retry = RetryPolicy(
            retry_same_endpoint_categories=(),
            maximum_same_endpoint_retries_per_node=0,
        )
        return install_production_expert_policy(build_runtime(config, retry_policy=retry))

    def test_expert_request_removes_all_provider_filters(self) -> None:
        base = {
            "model": "deepseek/model",
            "messages": [{"role": "user", "content": "task"}],
            "provider": {
                "only": ["deepseek", "deepinfra"],
                "order": ["deepseek", "deepinfra"],
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
                "zdr": True,
            },
        }
        policy = ProductionExpertPromptPolicy()
        node = SimpleNamespace(node_id="N1")
        with patch.object(SoftResourcePromptPolicy, "build_payload", return_value=base):
            payload = policy.build_payload(node, "task", [])
        self.assertIsNone(EXPERT_DATA_COLLECTION_POLICY)
        self.assertFalse(EXPERT_ZDR_REQUIRED)
        self.assertNotIn("provider", payload)

    def test_tool_fields_remain_forbidden_after_provider_opening(self) -> None:
        base = {
            "model": "vendor/model",
            "messages": [{"role": "user", "content": "task"}],
            "provider": {"only": ["vendor"]},
            "functions": [],
        }
        policy = ProductionExpertPromptPolicy()
        with patch.object(SoftResourcePromptPolicy, "build_payload", return_value=base):
            with self.assertRaisesRegex(RuntimeError, "forbidden tool"):
                policy.build_payload(SimpleNamespace(node_id="N1"), "task", [])

    def test_constitutional_quality_failure_preserves_actual_cost(self) -> None:
        engine = self._installed_runtime().execution_engine
        node = SimpleNamespace(
            node_id="N1",
            model="vendor/model",
            provider_endpoint="vendor/model@provider",
            output_contract={},
            parameter_profile={},
        )
        attempt = RuntimeAttempt(
            attempt_index=1,
            attempt_kind="initial",
            candidate_id="N1",
            model=node.model,
            provider_endpoint=node.provider_endpoint,
            request={"model": node.model},
            status="passed",
            answer="raw answer",
            quality_score=1.0,
            gate_reasons=[],
            latency_seconds=0.1,
            usage={"cost": 0.125},
            response_id="response-1",
            response_model=node.model,
            response_provider="provider",
        )
        constraints = compile_task_constraints("task")
        with (
            patch(
                "v5_constitutional_runtime_legacy.normalize_answer",
                return_value=("normalized answer", {"applied": True}),
            ),
            patch.object(
                engine.quality_policy,
                "evaluate",
                return_value=(False, 0.25, ["quality-floor-not-met"]),
            ),
            patch(
                "v5_constitutional_runtime_legacy.delivery_contract.validate_answer_contract",
                return_value=[],
            ),
            patch(
                "v5_constitutional_runtime_legacy.validate_answer_evidence",
                return_value=[],
            ),
        ):
            normalized = engine._normalize_attempt(node, "task", attempt, constraints)
        self.assertFalse(normalized)
        self.assertEqual("quality_gate_failed", attempt.status)
        self.assertEqual("QUALITY_GATE_FAILED", attempt.failure["category"])
        self.assertAlmostEqual(0.125, attempt.failure["actual_cost_usd"])
        self.assertEqual("normalized answer", attempt.answer)

    def test_failed_result_raise_is_deferred_without_changing_status(self) -> None:
        result = {
            "status": "failed",
            "stop_reason": "insufficient-required-work-coverage",
        }
        EvidenceCompleteExecutionEngine._raise_failed_result(result)
        self.assertEqual("failed", result["status"])
        self.assertEqual("insufficient-required-work-coverage", result["stop_reason"])

    def test_constitutional_failure_is_fully_persisted_before_wrapper_failure(self) -> None:
        result = {
            "status": "success",
            "completion_mode": "full",
            "quality_status": "full_success",
            "final_answer": "unsafe",
            "node_results": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            EvidenceCompleteExecutionEngine._fail_constitutional_result(
                result,
                root,
                "unsupported-evidence-or-quantity",
            )
            summary = json.loads(
                (root / "v5-execution-summary.json").read_text(encoding="utf-8")
            )
            report = (root / "v5-final-report.md").read_text(encoding="utf-8")
        self.assertEqual("failed", result["status"])
        self.assertEqual("none", result["completion_mode"])
        self.assertEqual("failed", summary["status"])
        self.assertEqual(
            "unsupported-evidence-or-quantity",
            summary["stop_reason"],
        )
        self.assertIn("unsupported-evidence-or-quantity", report)

    def test_installer_replaces_prompt_and_engine_without_model_retry(self) -> None:
        runtime = self._installed_runtime()
        self.assertIsInstance(runtime, ProductionRuntime)
        self.assertIsInstance(runtime.prompt_policy, ProductionExpertPromptPolicy)
        self.assertIsInstance(runtime.execution_engine, EvidenceCompleteExecutionEngine)
        self.assertEqual((), runtime.retry_policy.retry_same_endpoint_categories)
        self.assertEqual(0, runtime.retry_policy.maximum_same_endpoint_retries_per_node)
        self.assertTrue(callable(runtime.execution_engine._actual_cost))
        self.assertTrue(callable(runtime.execution_engine._normalize_attempt))

    def test_machine_policy_requires_open_provider_routing_and_isolation(self) -> None:
        policy = json.loads(
            (MARKET / "constitutional_policy.json").read_text(encoding="utf-8")
        )
        provider = policy["provider_routing"]
        self.assertEqual("unrestricted-openrouter", provider["mode"])
        self.assertFalse(provider["provider_allowlist_allowed"])
        self.assertFalse(provider["provider_order_allowed"])
        self.assertFalse(provider["provider_ignore_list_allowed"])
        self.assertFalse(provider["provider_price_filter_allowed"])
        self.assertFalse(provider["provider_zdr_filter_required"])
        self.assertFalse(provider["provider_data_collection_filter_required"])
        self.assertFalse(provider["exact_provider_lock_required"])
        self.assertTrue(provider["openrouter_selects_provider"])
        self.assertTrue(provider["provider_fallback_allowed"])

        tools = policy["tool_policy"]
        self.assertFalse(tools["expert_external_tools_allowed"])
        self.assertFalse(tools["expert_web_browsing_allowed"])
        self.assertFalse(tools["expert_external_api_allowed"])

        security = policy["security_boundaries"]
        self.assertTrue(security["authentication_required"])
        self.assertTrue(security["secret_protection_required"])
        self.assertTrue(security["repository_isolation_preserved"])
        self.assertFalse(security["unsafe_infinite_execution_allowed"])

        delivery = policy["delivery"]
        self.assertTrue(delivery["request_and_cost_ledgers_required"])
        self.assertTrue(delivery["failed_or_missing_items_must_be_disclosed"])


if __name__ == "__main__":
    unittest.main()
