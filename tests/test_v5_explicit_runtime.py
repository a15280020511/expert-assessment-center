import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import v5_executor as legacy_executor  # noqa: E402
import v5_production_hardening  # noqa: E402
from execution_graph import ExecutionGraph, SelectedNode  # noqa: E402
from openrouter_api import OpenRouterRequestError  # noqa: E402
from v5_runtime import (  # noqa: E402
    BudgetController,
    ExecutionEngine,
    FailureCategory,
    ProductionRuntime,
    RuntimeConfig,
)


def node(node_id="node-1", endpoint="model/a@provider-a"):
    model, provider = endpoint.split("@", 1)
    return SelectedNode(
        node_id=node_id,
        assigned_work=("work-1",),
        professional_capabilities={"analysis": 0.9},
        functions=("analysis",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={"supported_parameters": ["max_completion_tokens"]},
        model=model,
        provider_endpoint=endpoint,
        output_contract={
            "required_fields": ["conclusions"],
            "machine_readable_required": True,
            "must_separate_fact_assumption_inference": True,
        },
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "provider": {
                "only": [provider],
                "order": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


def graph() -> ExecutionGraph:
    selected = node()
    return ExecutionGraph(
        nodes=(selected,),
        edges=(),
        execution_stages=((selected.node_id,),),
        entry_nodes=(selected.node_id,),
        final_nodes=(selected.node_id,),
        required_work=("work-1",),
        estimated_quality=0.8,
        quality_floor=0.6,
        estimated_total_cost=0.01,
        metadata={},
    )


class V5ExplicitRuntimeTests(unittest.TestCase):
    def test_runtime_config_has_one_derived_initial_limit(self):
        config = RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        )
        self.assertEqual(config.initial_call_limit, 3)
        self.assertEqual(config.to_dict()["initial_call_limit"], 3)
        with self.assertRaises(ValueError):
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=4,
                cost_anomaly_usd=None,
                quality_tier="value",
            )

    def test_compatibility_install_never_mutates_executor(self):
        before = (legacy_executor.ExecutionBudget, legacy_executor.execute_v5_graph)
        v5_production_hardening.install()
        after = (legacy_executor.ExecutionBudget, legacy_executor.execute_v5_graph)
        self.assertEqual(before, after)

    def test_shared_recovery_pool_caps_retry_and_replacement_together(self):
        config = RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        )
        budget = BudgetController(config, graph())
        for index in range(3):
            allowed, reason = budget.reserve("initial", 0.0, f"node-{index}")
            self.assertTrue(allowed, reason)
        allowed, reason = budget.reserve("initial", 0.0, "node-4")
        self.assertFalse(allowed)
        self.assertEqual(reason, "initial-call-cap-reserved-for-recovery")
        self.assertTrue(budget.reserve("retry", 0.0, "node-1")[0])
        allowed, reason = budget.reserve("replacement", 0.0, "node-1")
        self.assertFalse(allowed)
        self.assertEqual(reason, "total-call-limit-exhausted")
        snapshot = budget.snapshot()
        self.assertEqual(snapshot["calls_reserved"], 4)
        self.assertEqual(snapshot["recovery_calls_reserved"], 1)

    def test_provider_circuit_is_isolated_per_runtime_budget(self):
        config = RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
            max_provider_failures=1,
        )
        first = BudgetController(config, graph())
        second = BudgetController(config, graph())
        first.fail_endpoint("model/a@provider-a", FailureCategory.PROVIDER_TIMEOUT)
        self.assertFalse(first.endpoint_available("model/a@provider-a"))
        self.assertTrue(second.endpoint_available("model/a@provider-a"))

    def test_structured_openrouter_failure_requires_no_text_parsing(self):
        error = OpenRouterRequestError(
            "opaque upstream message",
            category="rate_limited",
            retryable=True,
            http_status=429,
            retry_after_seconds=2.5,
            request_sent=True,
            response_received=True,
        )
        failure = ExecutionEngine._failure_from_exception(error, node())
        self.assertEqual(failure.category, FailureCategory.PROVIDER_RATE_LIMITED)
        self.assertTrue(failure.retryable)
        self.assertEqual(failure.http_status, 429)
        self.assertEqual(failure.retry_after_seconds, 2.5)

    def test_node_contract_is_structured_and_content_addressed(self):
        runtime = ProductionRuntime(RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        ))
        contract = runtime.execution_engine._contract(
            node(),
            json.dumps({"conclusions": ["A"], "risks": ["B"]}, ensure_ascii=False),
        )
        self.assertEqual(contract["schema_version"], "v5-node-result-1")
        self.assertTrue(contract["required_fields_complete"])
        self.assertEqual(len(contract["content_sha256"]), 64)
        self.assertFalse(contract["compression_used"])
        self.assertEqual(contract["conclusions"], ["A"])

    def test_catalog_snapshot_is_deterministic_and_run_local(self):
        class Model:
            id = "vendor/model"
            context_length = 32768
            max_completion_tokens = 8192
            prompt_price_per_million = 1.0
            completion_price_per_million = 2.0
            supported_parameters = ["reasoning"]
            ranks = {"intelligence-high-to-low": 1}

        runtime = ProductionRuntime(RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        ))
        first = runtime.build_catalog_snapshot(
            [Model()],
            {"vendor/model": {"data": {"endpoints": []}}},
            catalog_source="fixture",
            endpoint_source="fixture",
        )
        second = runtime.build_catalog_snapshot(
            [Model()],
            {"vendor/model": {"data": {"endpoints": []}}},
            catalog_source="fixture",
            endpoint_source="fixture",
        )
        self.assertEqual(first.snapshot_id, second.snapshot_id)
        self.assertFalse(first.to_dict()["cross_task_history_used"])

    def test_history_inputs_are_deleted_from_formal_sources(self):
        formal = [
            MARKET / "model_market.py",
            MARKET / "v5_cost_reliability_hardening.py",
            MARKET / "v5_pipeline.py",
            MARKET / "v5_production_ticket.py",
            MARKET / "v5_runtime.py",
        ]
        combined = "\n".join(path.read_text(encoding="utf-8") for path in formal)
        self.assertNotIn("MODEL_HISTORY_PATH", combined)
        self.assertNotIn("history_weight", combined)
        self.assertNotIn("history_path", combined)

    def test_runtime_description_declares_no_global_patch_or_history(self):
        runtime = ProductionRuntime(RuntimeConfig(
            total_call_limit=4,
            recovery_call_limit=1,
            cost_anomaly_usd=None,
            quality_tier="value",
        ))
        description = runtime.describe()
        self.assertFalse(description["global_monkey_patching"])
        self.assertFalse(description["cross_task_history_used"])


if __name__ == "__main__":
    unittest.main()
