import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_governance_runtime as governance  # noqa: E402
import v5_pipeline  # noqa: E402
from v5_gpt_expert_selector import GPTSelectorError  # noqa: E402


class GovernanceCompletionEnvelopeTests(unittest.TestCase):
    def test_gpt_protocol_limit_is_reduced(self):
        request = governance.bounded_governance_request(
            {"max_tokens": 10_000},
            8_000,
        )
        self.assertEqual(request["max_tokens"], 8_000)

    def test_claude_native_limit_is_not_increased(self):
        request = governance.bounded_governance_request(
            {"max_tokens": 512},
            8_000,
        )
        self.assertEqual(request["max_tokens"], 512)

    def test_omitted_task_limit_preserves_protocol_default(self):
        request = governance.bounded_governance_request(
            {"max_tokens": 10_000},
            None,
        )
        self.assertEqual(request["max_tokens"], 10_000)

    def test_nonpositive_task_limit_is_rejected(self):
        with self.assertRaisesRegex(
            governance.GovernanceRuntimeError,
            "must be positive",
        ):
            governance.bounded_governance_request(
                {"max_tokens": 10_000},
                0,
            )

    def test_cap_survives_endpoint_parameter_conversion(self):
        bounded = governance.bounded_governance_request(
            {"max_tokens": 10_000},
            8_000,
        )
        endpoint = {
            "logical_model": "~openai/gpt-latest",
            "resolved_model": "openai/gpt-test",
            "company": "openai",
            "provider": "openai",
            "provider_fallback_allowed": False,
            "official_intelligence_rank": 1,
            "supported_parameters": ["max_completion_tokens"],
        }
        request = governance._bind_governance_request(bounded, endpoint)
        self.assertNotIn("max_tokens", request)
        self.assertEqual(request["max_completion_tokens"], 8_000)

    def test_single_pass_caps_both_gpt_calls_and_preserves_claude(self):
        captured = []

        def fake_call(_run, request):
            captured.append(dict(request))
            provider = request["provider"]["only"][0]
            return {
                "id": f"response-{len(captured)}",
                "model": request["model"],
                "provider": provider,
                "choices": [{"message": {"content": "{}"}}],
                "usage": {},
            }, 0.01

        def endpoint(company, provider, model):
            return {
                "logical_model": f"~{company}/latest",
                "resolved_model": model,
                "company": company,
                "provider": provider,
                "provider_fallback_allowed": False,
                "official_intelligence_rank": 1,
                "supported_parameters": ["max_tokens"],
            }

        governance_models = {
            "status": "PASS",
            "provider_fallback_allowed": False,
            "gpt": endpoint("openai", "openai", "openai/gpt-test"),
            "claude": endpoint("anthropic", "anthropic", "anthropic/claude-test"),
        }
        proposal = {
            "work_items": [],
            "nodes": [],
            "edges": [],
            "final_nodes": [],
        }
        with (
            mock.patch.object(
                governance,
                "build_proposal_request",
                return_value={"max_tokens": 10_000},
            ),
            mock.patch.object(
                governance,
                "build_claude_red_team_request",
                return_value={"max_tokens": 512},
            ),
            mock.patch.object(
                governance,
                "build_synthesis_request",
                return_value={"max_tokens": 10_000},
            ),
            mock.patch.object(
                governance,
                "parse_proposal",
                return_value=proposal,
            ),
            mock.patch.object(
                governance,
                "parse_claude_red_team_advice",
                return_value={"suggestions": []},
            ),
            mock.patch.object(
                governance,
                "claude_unified_review_payload",
                return_value={},
            ),
            mock.patch.object(
                governance,
                "deterministic_violations",
                return_value=[],
            ),
            mock.patch.object(
                governance,
                "materialize_proposal",
                return_value=("graph", "limits", {"status": "PASS"}),
            ),
        ):
            governance.run_single_pass_governance(
                run=SimpleNamespace(api_key="unused"),
                task="closed-world task",
                task_digest="a" * 64,
                task_envelope={},
                catalog={},
                approved_total_calls=4,
                governance_calls_reserved=3,
                approved_recovery_calls=0,
                cost_anomaly_usd=0.25,
                max_completion_tokens=8_000,
                governance_models=governance_models,
                call_fn=fake_call,
            )
        self.assertEqual(
            [row["max_tokens"] for row in captured],
            [8_000, 512, 8_000],
        )

    def test_pipeline_rejects_nonpositive_direct_limit(self):
        args = SimpleNamespace(
            maximum_total_calls=4,
            maximum_recovery_calls=0,
            cost_anomaly_usd=0.25,
            max_completion_tokens=0,
            governance_max_completion_tokens=None,
        )
        with self.assertRaisesRegex(
            ValueError,
            "max_completion_tokens must be positive",
        ):
            v5_pipeline._validate_budget(args)

    def test_separate_governance_limit_is_validated(self):
        args = SimpleNamespace(
            maximum_total_calls=4,
            maximum_recovery_calls=0,
            cost_anomaly_usd=0.25,
            max_completion_tokens=512,
            governance_max_completion_tokens=0,
        )
        with self.assertRaisesRegex(
            ValueError,
            "governance_max_completion_tokens must be positive",
        ):
            v5_pipeline._validate_budget(args)

    def test_malformed_paid_output_persists_failure_ledger(self):
        def fake_call(_run, request):
            provider = request["provider"]["only"][0]
            return {
                "id": "paid-response-1",
                "model": request["model"],
                "provider": provider,
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {
                            "content": '{"work_items":[{"objective":"cut'
                        },
                    }
                ],
                "usage": {"cost": 0.0123},
            }, 0.02

        endpoint = {
            "logical_model": "~openai/gpt-latest",
            "resolved_model": "openai/gpt-test",
            "company": "openai",
            "provider": "openai",
            "provider_fallback_allowed": False,
            "official_intelligence_rank": 1,
            "supported_parameters": ["max_tokens"],
        }
        governance_models = {
            "status": "PASS",
            "provider_fallback_allowed": False,
            "gpt": endpoint,
            "claude": {
                **endpoint,
                "logical_model": "~anthropic/claude-opus-latest",
                "resolved_model": "anthropic/claude-test",
                "company": "anthropic",
                "provider": "anthropic",
            },
        }
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            with (
                mock.patch.object(
                    governance,
                    "build_proposal_request",
                    return_value={"max_tokens": 10_000},
                ),
                self.assertRaisesRegex(
                    GPTSelectorError,
                    "not valid JSON",
                ),
            ):
                governance.run_single_pass_governance(
                    run=SimpleNamespace(api_key="unused"),
                    task="closed-world task",
                    task_digest="a" * 64,
                    task_envelope={},
                    catalog={},
                    approved_total_calls=4,
                    governance_calls_reserved=3,
                    approved_recovery_calls=0,
                    cost_anomaly_usd=0.25,
                    artifact_root=artifact_root,
                    max_completion_tokens=4096,
                    governance_models=governance_models,
                    call_fn=fake_call,
                )
            ledger = json.loads(
                (artifact_root / "v5-governance-calls.json").read_text(
                    encoding="utf-8"
                )
            )
        self.assertEqual(ledger["status"], "FAIL")
        self.assertEqual(ledger["actual_governance_calls"], 1)
        self.assertEqual(
            ledger["calls"][0]["finish_reason"],
            "length",
        )
        self.assertGreater(
            ledger["calls"][0]["visible_output_characters"],
            0,
        )
        self.assertEqual(ledger["failure"]["kind"], "gpt_proposal")
        self.assertFalse(
            ledger["failure"]["raw_visible_output_persisted"]
        )


if __name__ == "__main__":
    unittest.main()
