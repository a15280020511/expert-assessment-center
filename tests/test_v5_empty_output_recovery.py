import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_production_hardening  # noqa: E402
import v5_production_ticket  # noqa: E402
from execution_graph import ExecutionGraph, GraphLimits, SelectedNode  # noqa: E402


FIELDS = ["conclusions", "assumptions", "uncertainties", "calculations"]


def node(model="vendor/model-a", provider="provider-a", node_id="node-a"):
    return SelectedNode(
        node_id=node_id,
        assigned_work=("work-a",),
        professional_capabilities={"general_analysis": 0.85},
        functions=("analysis", "decision_comparison"),
        prompt_profile={"modules": ["structured_delivery", "quantitative_rigor"]},
        reasoning_profile={"reasoning_enabled": True, "effort": "medium"},
        parameter_profile={
            "supported_parameters": ["max_tokens", "reasoning"],
            "recommended_output_allowance_tokens": 4096,
        },
        model=model,
        provider_endpoint=f"{model}@{provider}",
        output_contract={
            "required_fields": FIELDS,
            "machine_readable_required": False,
            "must_separate_fact_assumption_inference": True,
        },
        estimated_quality=0.78,
        quality_uncertainty=0.08,
        estimated_cost=0.01,
        failure_probability=0.04,
        request_config={
            "provider": {
                "order": [provider],
                "only": [provider],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
    )


def graph(recovery_rows=None):
    selected = node()
    return ExecutionGraph(
        nodes=(selected,),
        edges=(),
        execution_stages=((selected.node_id,),),
        entry_nodes=(selected.node_id,),
        final_nodes=(selected.node_id,),
        required_work=("work-a",),
        estimated_quality=0.78,
        quality_floor=0.60,
        estimated_total_cost=0.01,
        metadata={
            "version": 5,
            "recovery_pool": {selected.node_id: list(recovery_rows or [])},
        },
    )


def good_answer(label="A"):
    return (
        f"conclusions {label}；assumptions 已列明；uncertainties 已标记；"
        "calculations 公式和复核完整。事实、假设、推断和未知事项明确分开。"
        "最终建议包含条件、风险边界和可执行验收标准。"
    ) * 8


def empty_response(payload):
    return {
        "model": payload["model"],
        "choices": [{"finish_reason": "stop", "message": {"content": ""}}],
    }, 0.8


def good_response(payload, label="A"):
    return {
        "id": f"response-{label}",
        "model": payload["model"],
        "provider": payload["provider"]["order"][0],
        "choices": [
            {"finish_reason": "stop", "message": {"content": good_answer(label)}}
        ],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 500,
            "cost": 0.01,
        },
    }, 1.0


def run_config():
    return SimpleNamespace(
        parallel_workers=1,
        api_key=None,
        model_timeout_seconds=30,
        model_max_retries=0,
    )


def execute(*args, **kwargs):
    return v5_production_hardening.resilient_execute_v5_graph(*args, **kwargs)


class V5EmptyOutputRecoveryTests(unittest.TestCase):
    def test_empty_response_retries_once_inside_shared_recovery_pool(self):
        calls = []

        def fake_call(run, payload):
            calls.append(payload["model"])
            return empty_response(payload) if len(calls) == 1 else good_response(payload)

        with tempfile.TemporaryDirectory() as temp:
            result = execute(
                graph(),
                run_config(),
                "task",
                call_fn=fake_call,
                output_dir=temp,
                limits=GraphLimits(
                    max_nodes=3,
                    max_model_calls=4,
                    max_retries=1,
                    max_replacements=1,
                    max_budget_usd=0.20,
                ),
            )
            self.assertEqual(result["status"], "success")
            self.assertEqual(result["quality_status"], "full_success")
            row = result["node_results"][0]
            self.assertEqual(row["status"], "success_retried")
            self.assertEqual(row["resolved_model"], "vendor/model-a")
            self.assertEqual(len(row["attempts"]), 2)
            budget = result["execution_budget"]
            self.assertEqual(budget["calls_reserved"], 2)
            self.assertEqual(budget["retries_reserved"], 1)
            self.assertEqual(budget["replacements_reserved"], 0)
            self.assertEqual(budget["recovery_calls_reserved"], 1)
            self.assertEqual(budget["maximum_recovery_calls"], 1)
            self.assertEqual(budget["maximum_initial_calls"], 3)

    def test_zero_recovery_fails_after_one_empty_request_and_keeps_selected_model(self):
        replacement = node(
            model="vendor/model-b",
            provider="provider-b",
            node_id="replacement-b",
        ).to_dict()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(Exception, "minimum audited work-coverage gate"):
                execute(
                    graph([replacement]),
                    run_config(),
                    "task",
                    call_fn=lambda run, payload: empty_response(payload),
                    output_dir=temp,
                    limits=GraphLimits(
                        max_nodes=4,
                        max_model_calls=4,
                        max_retries=0,
                        max_replacements=0,
                        max_budget_usd=0.20,
                    ),
                )
            rows = json.loads(
                (Path(temp) / "v5-node-results.json").read_text(encoding="utf-8")
            )
            self.assertEqual(rows[0]["resolved_model"], "vendor/model-a")
            self.assertEqual(rows[0]["provider_endpoint"], "vendor/model-a@provider-a")
            self.assertEqual(len(rows[0]["attempts"]), 1)
            request_audit = json.loads(
                (Path(temp) / "v5-request-audit.json").read_text(encoding="utf-8")
            )
            self.assertEqual(request_audit["request_count"], 1)
            self.assertFalse(request_audit["artificial_token_ceiling_sent"])
            self.assertEqual(request_audit["quality_integrity_status"], "FAIL")

    def test_one_recovery_call_cannot_be_used_for_both_retry_and_replacement(self):
        replacement = node(
            model="vendor/model-b",
            provider="provider-b",
            node_id="replacement-b",
        ).to_dict()
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(Exception):
                execute(
                    graph([replacement]),
                    run_config(),
                    "task",
                    call_fn=lambda run, payload: empty_response(payload),
                    output_dir=temp,
                    limits=GraphLimits(
                        max_nodes=3,
                        max_model_calls=4,
                        max_retries=1,
                        max_replacements=1,
                        max_budget_usd=0.20,
                    ),
                )
            summary = json.loads(
                (Path(temp) / "v5-execution-summary.json").read_text(encoding="utf-8")
            )
            budget = summary["execution_budget"]
            self.assertEqual(budget["calls_reserved"], 2)
            self.assertEqual(budget["recovery_calls_reserved"], 1)
            self.assertEqual(budget["retries_reserved"], 1)
            self.assertEqual(budget["replacements_reserved"], 0)
            self.assertTrue(any(
                denial["reason"] == "shared-recovery-pool-exhausted"
                and denial["kind"] == "replacement"
                for denial in budget["denials"]
            ))

    def test_two_recovery_calls_allow_retry_then_replacement(self):
        replacement = node(
            model="vendor/model-b",
            provider="provider-b",
            node_id="replacement-b",
        ).to_dict()
        calls = []

        def fake_call(run, payload):
            calls.append(payload["model"])
            if payload["model"] == "vendor/model-a":
                return empty_response(payload)
            return good_response(payload, "B")

        with tempfile.TemporaryDirectory() as temp:
            result = execute(
                graph([replacement]),
                run_config(),
                "task",
                call_fn=fake_call,
                output_dir=temp,
                limits=GraphLimits(
                    max_nodes=2,
                    max_model_calls=4,
                    max_retries=2,
                    max_replacements=2,
                    max_budget_usd=0.20,
                ),
            )
            row = result["node_results"][0]
            self.assertEqual(row["status"], "success_recovered")
            self.assertEqual(row["resolved_model"], "vendor/model-b")
            self.assertEqual(calls, ["vendor/model-a", "vendor/model-a", "vendor/model-b"])
            budget = result["execution_budget"]
            self.assertEqual(budget["calls_reserved"], 3)
            self.assertEqual(budget["recovery_calls_reserved"], 2)
            self.assertEqual(budget["retries_reserved"], 1)
            self.assertEqual(budget["replacements_reserved"], 1)

    def test_failed_production_normalization_preserves_attempted_request(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "ticket-status.json").write_text(
                json.dumps({"task_id": "t1"}), encoding="utf-8"
            )
            (root / "v5-execution-graph.json").write_text(
                json.dumps(graph().to_dict()), encoding="utf-8"
            )
            (root / "v5-execution-summary.json").write_text(
                json.dumps({
                    "status": "failed",
                    "completion_mode": "none",
                    "quality_status": "failed",
                    "executor": "v5-native-execution-engine",
                    "actual_cost_usd": 0.0,
                    "execution_budget": {
                        "calls_reserved": 1,
                        "maximum_total_calls": 4,
                        "maximum_initial_calls": 4,
                        "retries_reserved": 0,
                        "replacements_reserved": 0,
                        "recovery_calls_reserved": 0,
                    },
                }),
                encoding="utf-8",
            )
            failed_row = {
                "node_id": "node-a",
                "status": "failed",
                "selected_model": "vendor/model-a",
                "resolved_model": "vendor/model-a",
                "provider_endpoint": "vendor/model-a@provider-a",
                "attempts": [{
                    "provider_endpoint": "vendor/model-a@provider-a",
                    "request": {
                        "model": "vendor/model-a",
                        "provider": {"only": ["provider-a"]},
                        "max_tokens": 4096,
                    },
                    "answer": None,
                    "response_id": None,
                    "response_provider": None,
                    "usage": {},
                    "gate_reasons": ["answer-too-short"],
                    "failure": {
                        "category": "QUALITY_GATE_FAILED",
                        "retryable": False,
                    },
                }],
            }
            (root / "v5-node-results.json").write_text(
                json.dumps([failed_row]), encoding="utf-8"
            )
            (root / "v5-request-audit.json").write_text(
                json.dumps({
                    "status": "PASS",
                    "request_count": 1,
                    "requests": [failed_row["attempts"][0]["request"]],
                    "dynamic_output_allowance_sent": True,
                    "bounded_output_allowance_sent": True,
                    "artificial_token_ceiling_sent": False,
                    "quality_integrity_status": "FAIL",
                }),
                encoding="utf-8",
            )
            envelope = v5_production_ticket._normalize_evidence(
                root,
                total_calls=4,
                recovery_calls=0,
                anomaly_budget=0.20,
                require_report=False,
            )
            audit = json.loads((root / "request-audit.json").read_text(encoding="utf-8"))
            ledger = json.loads((root / "call-ledger.json").read_text(encoding="utf-8"))
            runtime = json.loads((root / "production-runtime.json").read_text(encoding="utf-8"))
            self.assertEqual(audit["status"], "PASS")
            self.assertEqual(audit["expected_request_count"], 1)
            self.assertEqual(audit["captured_request_count"], 1)
            self.assertFalse(audit["artificial_token_ceiling_sent"])
            self.assertEqual(ledger["summary"]["call_count"], 1)
            self.assertEqual(ledger["summary"]["attempted_providers"], ["provider-a"])
            self.assertEqual(ledger["summary"]["substantive_provider_count"], 0)
            self.assertEqual(envelope["attempted_provider_count"], 1)
            self.assertEqual(envelope["provider_count"], 0)
            self.assertFalse(envelope["fallback_used"])
            self.assertFalse(runtime["legacy_runtime_present"])

    def test_structured_retryable_failure_is_detected_without_text_parsing(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "v5-node-results.json").write_text(
                json.dumps([{
                    "node_id": "node-a",
                    "attempts": [{
                        "answer": None,
                        "response_id": None,
                        "usage": {},
                        "gate_reasons": ["PROVIDER_EMPTY_RESPONSE"],
                        "failure": {
                            "category": "PROVIDER_EMPTY_RESPONSE",
                            "retryable": True,
                            "request_sent": True,
                            "response_received": True,
                        },
                    }],
                }]),
                encoding="utf-8",
            )
            self.assertTrue(v5_production_ticket._retryable_provider_failure(root))


if __name__ == "__main__":
    unittest.main()
