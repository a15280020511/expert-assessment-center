import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_candidate_diagnostics as diagnostics  # noqa: E402
import v5_task_domain_proxy as proxy  # noqa: E402


class TestV5TaskDomainProxy(unittest.TestCase):
    @staticmethod
    def resource_bundle():
        return {
            "resource_matrices": {
                "matrices": [
                    {
                        "interpretation_id": "i1",
                        "capability_labels": [
                            "quantitative_reasoning",
                            "evidence_validation",
                            "decision_comparison",
                            "domain:business",
                        ],
                        "task_resource_matrix": [
                            [0.8, 0.9, 0.7, 1.0],
                            [0.6, 0.8, 0.9, 0.8],
                        ],
                        "confidence_matrix": [
                            [0.9, 0.9, 0.8, 0.95],
                            [0.8, 0.9, 0.9, 0.9],
                        ],
                        "work_index": [
                            {
                                "work_id": "w1",
                                "required_context_tokens": 4096,
                                "expected_output_tokens": 1500,
                            }
                        ],
                        "hard_requirements": [
                            {
                                "work_id": "w1",
                                "capability": "domain:business",
                                "minimum_demand": 1.0,
                            }
                        ],
                    }
                ]
            }
        }

    def test_proxy_weights_are_derived_from_task_cooccurrence(self):
        weights = proxy.derive_domain_proxy_weights(self.resource_bundle())
        business = weights["domain:business"]
        self.assertEqual(set(business), {
            "quantitative_reasoning",
            "evidence_validation",
            "decision_comparison",
        })
        self.assertAlmostEqual(sum(business.values()), 1.0, places=6)
        self.assertGreater(business["evidence_validation"], 0.0)

    def test_domain_score_can_be_raised_without_changing_functional_scores(self):
        market = {
            "endpoints": [
                {
                    "endpoint_id": "e1",
                    "model_id": "vendor/model",
                    "provider_slug": "p1",
                    "benchmark_confidence": 0.9,
                    "capability_scores": {
                        "quantitative_reasoning": 0.82,
                        "evidence_validation": 0.88,
                        "decision_comparison": 0.84,
                        "domain:business": 0.30,
                    },
                }
            ]
        }
        calibrated = proxy.calibrate_domain_market(market, self.resource_bundle())
        endpoint = calibrated["endpoints"][0]
        scores = endpoint["capability_scores"]
        self.assertGreater(scores["domain:business"], 0.80)
        self.assertEqual(scores["quantitative_reasoning"], 0.82)
        self.assertEqual(scores["evidence_validation"], 0.88)
        audit = calibrated["task_domain_proxy_calibration"]
        self.assertFalse(audit["hard_requirement_thresholds_changed"])
        self.assertFalse(audit["functional_capability_scores_changed"])
        self.assertEqual(audit["model_calls"], 0)
        self.assertEqual(
            endpoint["task_domain_proxy_calibration"]["domain:business"]["raw_score"],
            0.30,
        )

    def test_hard_gap_report_preserves_raw_and_calibrated_scores(self):
        market = {
            "endpoints": [
                {
                    "endpoint_id": "e1",
                    "model_id": "vendor/model",
                    "provider_slug": "p1",
                    "context_length": 8192,
                    "max_completion_tokens": 4096,
                    "benchmark_confidence": 0.9,
                    "capability_scores": {
                        "quantitative_reasoning": 0.82,
                        "evidence_validation": 0.88,
                        "decision_comparison": 0.84,
                        "domain:business": 0.30,
                    },
                }
            ]
        }
        calibrated = proxy.calibrate_domain_market(market, self.resource_bundle())
        report = diagnostics.analyze_hard_requirement_gaps(
            self.resource_bundle(), calibrated
        )
        work = report["interpretations"][0]["work_gaps"][0]
        requirement = work["hard_requirements"][0]
        self.assertEqual(requirement["planner_threshold"], 0.62)
        self.assertEqual(requirement["maximum_raw_score"], 0.30)
        self.assertGreater(requirement["maximum_calibrated_score"], 0.80)
        self.assertEqual(work["all_hard_requirements_passing_endpoint_count"], 1)
        self.assertFalse(report["hard_requirement_thresholds_changed"])
        self.assertEqual(report["model_calls"], 0)

    def test_no_domain_demand_produces_no_proxy(self):
        bundle = {
            "resource_matrices": {
                "matrices": [{
                    "capability_labels": ["general_analysis", "domain:business"],
                    "task_resource_matrix": [[0.8, 0.0]],
                    "confidence_matrix": [[0.9, 0.9]],
                }]
            }
        }
        self.assertEqual(proxy.derive_domain_proxy_weights(bundle), {})


if __name__ == "__main__":
    unittest.main()
