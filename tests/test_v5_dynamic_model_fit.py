from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from v5_constitutional_pipeline import (  # noqa: E402
    _dynamic_quality_tolerance,
    _dynamic_task_fit,
    _task_fit_feature_weights,
)
from v5_task_constraints import compile_task_constraints  # noqa: E402


class DynamicModelFitTests(unittest.TestCase):
    def _profile(self, **overrides):
        values = {
            "domains": ["general"],
            "primary_domain": "general",
            "secondary_domain": "general",
            "complexity_score": 0,
            "high_stakes": False,
            "chinese": False,
            "requested_context": 16_384,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def _model(self, **overrides):
        values = {
            "id": "vendor/reasoning-model",
            "name": "Reasoning Analysis Model",
            "description": "general reasoning analysis multilingual structured output",
            "supported_parameters": ["reasoning", "structured_outputs"],
            "reasoning": {"supported": True},
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_task_fit_weights_are_normalized_and_task_dependent(self) -> None:
        simple = self._profile()
        complex_high_stakes = self._profile(
            domains=["medical", "research", "math"],
            primary_domain="medical",
            secondary_domain="research",
            complexity_score=7,
            high_stakes=True,
            chinese=True,
        )
        simple_weights = _task_fit_feature_weights(simple)
        complex_weights = _task_fit_feature_weights(complex_high_stakes)
        self.assertAlmostEqual(sum(simple_weights.values()), 1.0)
        self.assertAlmostEqual(sum(complex_weights.values()), 1.0)
        self.assertNotEqual(simple_weights, complex_weights)
        self.assertGreater(
            complex_weights["structured_output"],
            simple_weights["structured_output"],
        )
        self.assertGreater(
            complex_weights["reasoning_capability"],
            simple_weights["reasoning_capability"],
        )

    def test_dynamic_task_fit_exposes_feature_evidence(self) -> None:
        profile = self._profile(
            domains=["research", "math"],
            primary_domain="research",
            secondary_domain="math",
            complexity_score=5,
            chinese=True,
        )
        score, reasons, weights, features = _dynamic_task_fit(
            self._model(),
            profile,
        )
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)
        self.assertAlmostEqual(sum(weights.values()), 1.0)
        self.assertEqual(set(weights), set(features))
        self.assertTrue(reasons)
        self.assertEqual(features["reasoning_capability"], 1.0)
        self.assertEqual(features["structured_output"], 1.0)
        self.assertEqual(features["language_fit"], 1.0)

    def test_quality_tolerance_tightens_for_strict_tasks(self) -> None:
        shape = {
            "explicit_output_contract": False,
            "maximum_atomic_work": 3,
        }
        simple = self._profile(complexity_score=0)
        strict = self._profile(complexity_score=7, high_stakes=True)
        simple_tolerance = _dynamic_quality_tolerance(
            simple,
            compile_task_constraints("比较两个方案"),
            shape,
            20.0,
        )
        strict_tolerance = _dynamic_quality_tolerance(
            strict,
            compile_task_constraints("仅依据题面，不得编造。"),
            shape,
            20.0,
        )
        self.assertGreater(simple_tolerance, strict_tolerance)
        self.assertEqual(strict_tolerance, 0.0)


if __name__ == "__main__":
    unittest.main()
