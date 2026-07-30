import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_micro_canary as canary  # noqa: E402


class TestV5MicroCanary(unittest.TestCase):
    def test_prepare_has_sub_cent_v5_only_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "issue": {
                            "number": 39,
                            "body": json.dumps(
                                {
                                    "canary_id": "micro-test",
                                    "task_id": "software-job-runner-security",
                                }
                            ),
                        }
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(canary.prepare(event, root / "out"), 0)
            config = json.loads(
                (root / "out" / "v5-micro-canary-config.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(config["max_actual_cost_usd"], 0.01)
            self.assertEqual(config["max_calls"], 8)
            self.assertEqual(config["max_nodes"], 8)
            self.assertEqual(config["output_allowance_tokens"], 600)
            self.assertFalse(config["production_cutover_eligible"])
            self.assertFalse(config["production_entrypoint_changed"])
            self.assertFalse(config["v3_executed"])
            self.assertFalse(config["v3_deleted"])

    def test_prepare_rejects_paid_limit_overrides(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "issue": {
                            "number": 39,
                            "body": json.dumps(
                                {
                                    "task_id": "software-job-runner-security",
                                    "max_cost_usd": 1.0,
                                }
                            ),
                        }
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(canary.CanaryError):
                canary.prepare(event, root / "out")

    def test_optional_reasoning_is_disabled(self):
        model = SimpleNamespace(reasoning={"mandatory": False})
        self.assertEqual(canary._reasoning_effort(model), "none")

    def test_mandatory_reasoning_uses_minimal_or_lowest_supported(self):
        minimal = SimpleNamespace(
            reasoning={"mandatory": True, "supported_efforts": ["low", "minimal"]}
        )
        lowest = SimpleNamespace(
            reasoning={"mandatory": True, "supported_efforts": ["high", "low"]}
        )
        unspecified = SimpleNamespace(reasoning={"mandatory": True})
        self.assertEqual(canary._reasoning_effort(minimal), "minimal")
        self.assertEqual(canary._reasoning_effort(lowest), "low")
        self.assertEqual(canary._reasoning_effort(unspecified), "minimal")

    def test_endpoint_filter_keeps_only_cheap_reliable_parameter_compatible_rows(self):
        raw = {
            "endpoints": [
                {
                    "endpoint_id": "kept",
                    "model_id": "vendor/model-a",
                    "provider_slug": "provider-a",
                    "prompt_price_per_million": 0.30,
                    "completion_price_per_million": 0.90,
                    "reliability": 0.95,
                    "supported_parameters": ["max_tokens", "reasoning"],
                    "synthetic_fixture_only": False,
                    "capability_scores": {
                        "complex_reasoning": 0.31,
                        "implementation": 0.72,
                    },
                },
                {
                    "endpoint_id": "expensive",
                    "model_id": "vendor/model-b",
                    "provider_slug": "provider-b",
                    "prompt_price_per_million": 0.30,
                    "completion_price_per_million": 1.10,
                    "reliability": 0.99,
                    "supported_parameters": ["max_tokens", "reasoning"],
                    "synthetic_fixture_only": False,
                    "capability_scores": {"complex_reasoning": 0.90},
                },
                {
                    "endpoint_id": "missing-reasoning",
                    "model_id": "vendor/model-c",
                    "provider_slug": "provider-c",
                    "prompt_price_per_million": 0.10,
                    "completion_price_per_million": 0.20,
                    "reliability": 0.99,
                    "supported_parameters": ["max_tokens"],
                    "synthetic_fixture_only": False,
                    "capability_scores": {"complex_reasoning": 0.90},
                },
            ],
            "rejected": [],
        }
        # A second qualifying model is required by the canary safety boundary.
        raw["endpoints"].append(
            {
                "endpoint_id": "kept-2",
                "model_id": "vendor/model-d",
                "provider_slug": "provider-d",
                "prompt_price_per_million": 0.20,
                "completion_price_per_million": 0.80,
                "reliability": 0.90,
                "supported_parameters": ["max_tokens", "reasoning"],
                "synthetic_fixture_only": False,
                "capability_scores": {"complex_reasoning": 0.50},
            }
        )
        filtered = canary._filter_and_neutralize_market(raw)
        self.assertEqual(
            {row["endpoint_id"] for row in filtered["endpoints"]},
            {"kept", "kept-2"},
        )
        first = next(row for row in filtered["endpoints"] if row["endpoint_id"] == "kept")
        self.assertEqual(first["capability_scores"]["complex_reasoning"], 0.48)
        self.assertEqual(first["capability_scores"]["implementation"], 0.72)
        self.assertFalse(
            first["canary_capability_neutralization"]["production_policy_changed"]
        )

    def test_endpoint_filter_fails_without_two_distinct_cheap_models(self):
        raw = {
            "endpoints": [
                {
                    "endpoint_id": "only",
                    "model_id": "vendor/model-a",
                    "provider_slug": "provider-a",
                    "prompt_price_per_million": 0.10,
                    "completion_price_per_million": 0.20,
                    "reliability": 0.99,
                    "supported_parameters": ["max_tokens", "reasoning"],
                    "synthetic_fixture_only": False,
                    "capability_scores": {"complex_reasoning": 0.90},
                }
            ]
        }
        with self.assertRaises(canary.CanaryError):
            canary._filter_and_neutralize_market(raw)

    def test_output_requirements_are_bounded_only_in_canary_copy(self):
        resources = {
            "task_semantics": {
                "interpretations": [
                    {
                        "atomic_work": [
                            {
                                "context_requirements": {
                                    "expected_output_tokens": 1800
                                }
                            }
                        ]
                    }
                ]
            }
        }
        bounded = canary._bound_output_requirements(resources)
        value = bounded["task_semantics"]["interpretations"][0]["atomic_work"][0][
            "context_requirements"
        ]["expected_output_tokens"]
        self.assertEqual(value, 600)
        self.assertEqual(
            resources["task_semantics"]["interpretations"][0]["atomic_work"][0][
                "context_requirements"
            ]["expected_output_tokens"],
            1800,
        )
        self.assertFalse(
            bounded["micro_canary_output_policy"]["production_policy_changed"]
        )

    def test_workflow_cannot_cut_over_or_run_v3(self):
        workflow = (
            ROOT / ".github" / "workflows" / "v5-micro-canary.yml"
        ).read_text(encoding="utf-8")
        source = (ROOT / "open-model-market" / "v5_micro_canary.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("[v5-micro-canary]", workflow)
        self.assertIn("Hard actual-cost ceiling", workflow)
        self.assertIn("Production cutover eligible: false", workflow)
        self.assertNotIn("update_ref", workflow)
        self.assertNotIn("merge_pull_request", workflow)
        self.assertNotIn("_v3_strategy", source)
        self.assertNotIn("expert_team_hardened", source)
        self.assertIn('"production_cutover_allowed": False', source)
        self.assertIn('"v3_executed": False', source)


if __name__ == "__main__":
    unittest.main()
