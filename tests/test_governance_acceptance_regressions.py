import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MARKET = ROOT / "open-model-market"
sys.path.insert(0, str(MARKET))

import v5_governance_plan_evidence as evidence  # noqa: E402
import v5_price_ranked_independent_revalidation as revalidation  # noqa: E402
from v5_price_ranked_support import models_from_graph  # noqa: E402


MODELS = (
    "deepseek/deepseek-v4-pro",
    "xiaomi/mimo-v2.5-pro",
    "amazon/nova-pro-v1",
    "nvidia/nemotron-3-ultra-550b-a55b",
)


class GovernanceAcceptanceRegressionTests(unittest.TestCase):
    def _source(self) -> dict:
        graph = {"nodes": [{"model": model} for model in MODELS]}
        return {
            "request_audit": {"status": "PASS"},
            "requests": ({}, {}, {}, {}),
            "graph_nodes": tuple(graph["nodes"]),
            "graph": graph,
            "summary": {"execution_budget": {"calls_reserved": 4}},
        }

    def test_models_from_graph_preserves_materialized_plan_order(self) -> None:
        graph = {
            "nodes": [
                {"model": MODELS[0]},
                {"model": MODELS[1]},
                {"model": MODELS[0]},
                {"model": MODELS[2]},
                {"model": MODELS[3]},
            ]
        }
        self.assertEqual(models_from_graph(graph), MODELS)

    def test_graph_validation_accepts_same_models_in_plan_order(self) -> None:
        plan = {"selected_models": [{"model": model} for model in MODELS]}
        with mock.patch.object(
            evidence, "canonical_provider_lock", return_value=True
        ):
            calls = evidence._validate_graph_and_requests(  # noqa: SLF001
                self._source(), plan, maximum_nodes=4
            )
        self.assertEqual(calls, 4)

    def test_graph_validation_still_rejects_reordered_or_duplicate_plan(self) -> None:
        for planned in (
            (MODELS[1], MODELS[0], MODELS[2], MODELS[3]),
            (MODELS[0], MODELS[0], MODELS[2], MODELS[3]),
        ):
            with self.subTest(planned=planned), mock.patch.object(
                evidence, "canonical_provider_lock", return_value=True
            ):
                with self.assertRaisesRegex(
                    RuntimeError,
                    "executed graph models differ from governance model plan",
                ):
                    evidence._validate_graph_and_requests(  # noqa: SLF001
                        self._source(),
                        {"selected_models": [{"model": model} for model in planned]},
                        maximum_nodes=4,
                    )

    def test_independent_revalidation_cli_accepts_absent_cost_advisory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "result.json"
            argv = [
                "v5_price_ranked_independent_revalidation.py",
                "--artifact-dir",
                str(root),
                "--expected-sha",
                "a" * 40,
                "--expected-run-id",
                "123",
                "--maximum-calls",
                "4",
                "--archive",
                str(root / "artifact.zip"),
                "--expected-artifact-digest",
                "b" * 64,
                "--output",
                str(output),
            ]
            expected = {
                "status": "PASS",
                "cost_advisory_usd": None,
                "cost_advisory_exceeded": False,
            }
            with mock.patch.object(sys, "argv", argv), mock.patch.object(
                revalidation, "revalidate", return_value=expected
            ) as validate:
                self.assertEqual(revalidation.main(), 0)
            self.assertIsNone(validate.call_args.kwargs["cost_advisory_usd"])
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), expected)


if __name__ == "__main__":
    unittest.main()
