import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import task_semantic_compiler as compiler  # noqa: E402
import v5_output_contract_delivery as delivery  # noqa: E402
import v5_task_delivery_contract as contract_policy  # noqa: E402
from execution_graph import SelectedNode  # noqa: E402
from v5_runtime import ProductionRuntime, RuntimeConfig  # noqa: E402


TASK = (
    "请输出且只能输出一个合法JSON对象。JSON顶层必须严格包含且仅包含以下字段："
    "facts、assumptions、unknowns、options、formulas、decision_tree、hard_rejections、"
    "day_by_day_plan、red_team、final_recommendation。每个字段均不得为空；"
    "options必须恰好包含continue_current、supplier_a、supplier_b、hybrid四个对象；"
    "day_by_day_plan必须覆盖day_0到day_14。"
)
FIELDS = [
    "facts",
    "assumptions",
    "unknowns",
    "options",
    "formulas",
    "decision_tree",
    "hard_rejections",
    "day_by_day_plan",
    "red_team",
    "final_recommendation",
]
OPTIONS = ["continue_current", "supplier_a", "supplier_b", "hybrid"]
DAYS = [f"day_{index}" for index in range(15)]


def _node(output_contract):
    return SelectedNode(
        node_id="node-explicit-contract",
        assigned_work=("work-synthesis",),
        professional_capabilities={"synthesis": 1.0},
        functions=("synthesis",),
        prompt_profile={"modules": ["structured_delivery"]},
        reasoning_profile={"enabled": True},
        parameter_profile={},
        model="vendor/model",
        provider_endpoint="vendor/model@provider/default",
        output_contract=output_contract,
        estimated_quality=0.8,
        quality_uncertainty=0.1,
        estimated_cost=0.01,
        failure_probability=0.05,
        request_config={
            "provider": {
                "only": ["provider/default"],
                "order": ["provider/default"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


def _valid_payload():
    return {
        "facts": ["known"],
        "assumptions": ["assumed"],
        "unknowns": ["unknown"],
        "options": {key: {"decision": "conditional"} for key in OPTIONS},
        "formulas": ["x=y"],
        "decision_tree": {"if": "then"},
        "hard_rejections": ["unsafe"],
        "day_by_day_plan": {key: {"action": key} for key in DAYS},
        "red_team": ["counterexample"],
        "final_recommendation": {"condition": "evidence"},
    }


class TestV5ExplicitTaskDeliveryContract(unittest.TestCase):
    def test_extracts_exact_top_nested_and_range_keys(self):
        explicit = contract_policy.extract_explicit_contract(TASK)
        self.assertEqual(explicit["exact_top_level_fields"], FIELDS)
        self.assertTrue(explicit["forbid_extra_top_level_fields"])
        self.assertEqual(explicit["nested_exact_fields"]["options"], OPTIONS)
        self.assertEqual(explicit["nested_exact_fields"]["day_by_day_plan"], DAYS)
        self.assertIn("options", explicit["nested_values_must_be_objects"])

    def test_semantic_compiler_replaces_generic_synthesis_schema_only(self):
        synthesis = compiler._output_contract(TASK, {"synthesis": 1.0}, True)
        analysis = compiler._output_contract(TASK, {"analysis": 1.0}, True)
        self.assertEqual(synthesis["required_fields"], FIELDS)
        self.assertTrue(synthesis["explicit_user_contract"])
        self.assertIn("conclusions", analysis["required_fields"])
        self.assertNotIn("explicit_user_contract", analysis)

    def test_previous_false_pass_shape_is_rejected(self):
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, True)
        previous_shape = {
            "agreements": ["x"],
            "assumptions": ["x"],
            "conclusions": ["x"],
            "conflict_resolution": ["x"],
            "disagreements": ["x"],
            "evidence_gaps": ["x"],
            "final_recommendation": ["x"],
            "uncertainties": ["x"],
        }
        violations = contract_policy.validate_parsed_contract(previous_shape, contract)
        self.assertTrue(any(item.startswith("missing-exact-top-level-keys:") for item in violations))
        self.assertTrue(any(item.startswith("unexpected-top-level-keys:") for item in violations))

        passed, score, reasons = delivery.contract_aware_quality_gate(
            _node(contract),
            {"choices": [{"finish_reason": "stop"}]},
            json.dumps(previous_shape, ensure_ascii=False),
        )
        self.assertFalse(passed)
        self.assertLessEqual(score, 0.35)
        self.assertTrue(any(item.startswith("missing-exact-top-level-keys:") for item in reasons))

    def test_exact_contract_accepts_only_complete_nested_shape(self):
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, True)
        payload = _valid_payload()
        self.assertEqual(contract_policy.validate_parsed_contract(payload, contract), [])
        passed, _, reasons = delivery.contract_aware_quality_gate(
            _node(contract),
            {"choices": [{"finish_reason": "stop"}]},
            json.dumps(payload, ensure_ascii=False),
        )
        self.assertTrue(passed, reasons)

        payload["options"].pop("hybrid")
        payload["day_by_day_plan"]["day_15"] = {"action": "extra"}
        violations = contract_policy.validate_parsed_contract(payload, contract)
        self.assertIn("missing-nested-keys:options:hybrid", violations)
        self.assertIn("unexpected-nested-keys:day_by_day_plan:day_15", violations)

    def test_runtime_contract_evidence_cannot_mark_wrong_shape_complete(self):
        contract = compiler._output_contract(TASK, {"synthesis": 1.0}, True)
        runtime = ProductionRuntime(
            RuntimeConfig(
                total_call_limit=4,
                recovery_call_limit=1,
                cost_anomaly_usd=None,
                quality_tier="value",
            )
        )
        wrong = json.dumps({"assumptions": ["x"], "final_recommendation": ["x"]})
        evidence = runtime.execution_engine._contract(_node(contract), wrong)
        self.assertFalse(evidence["required_fields_complete"])
        self.assertTrue(evidence["contract_violations"])


if __name__ == "__main__":
    unittest.main()
