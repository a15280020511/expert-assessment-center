from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_global_company_calibration as calibration  # noqa: E402


def endpoint(company: str, score: float) -> dict:
    model = f"{company}/model"
    return {
        "endpoint_id": f"{model}@provider-{company}",
        "model_id": model,
        "context_length": 131_072,
        "max_completion_tokens": 16_384,
        "benchmark_score": 0.80,
        "benchmark_confidence": 0.95,
        "capability_scores": {
            "complex_reasoning": score,
            "evidence_validation": score,
        },
    }


class V5GlobalCompanyCalibrationTests(unittest.TestCase):
    def test_global_company_target_expands_local_two_company_pool(self) -> None:
        endpoints = [
            endpoint("company-a", 0.90),
            endpoint("company-b", 0.85),
            endpoint("company-c", 0.80),
            endpoint("company-d", 0.75),
        ]
        work = {
            "work_id": "work-1",
            "context_requirements": {
                "required_context_tokens": 16_384,
                "expected_output_tokens": 2_000,
            },
            "independence_requirements": {
                "minimum_independent_copies": 2,
                "different_model_required": True,
            },
        }
        selected_ids, audit = (
            calibration._eligibility_for_global_assignment(
                work,
                endpoints,
                {
                    "complex_reasoning": 0.90,
                    "evidence_validation": 0.90,
                },
                {"complex_reasoning", "evidence_validation"},
                required_copies=2,
                global_company_target=4,
            )
        )
        self.assertEqual(4, len(selected_ids))
        self.assertEqual(4, audit["work_candidate_company_breadth_target"])
        self.assertEqual(4, audit["selected_eligible_company_count"])
        self.assertEqual(
            "rank-backed-global-company-breadth-calibrated",
            audit["calibration_status"],
        )
        self.assertTrue(audit["calibration_applied"])
        self.assertFalse(audit["capability_scores_modified"])
        self.assertFalse(audit["task_demands_modified"])
        self.assertFalse(audit["hard_labels_modified"])
        self.assertFalse(audit["proxy_floor_lowered"])
        self.assertGreaterEqual(
            audit["adaptive_proxy_floor"],
            calibration.local_calibration.MIN_PROXY_CAPABILITY_FLOOR,
        )

    def test_insufficient_rank_evidence_remains_fail_closed(self) -> None:
        endpoints = [
            endpoint("company-a", 0.90),
            endpoint("company-b", 0.85),
            {
                **endpoint("company-c", 0.80),
                "benchmark_confidence": 0.20,
            },
        ]
        work = {
            "work_id": "work-2",
            "context_requirements": {
                "required_context_tokens": 16_384,
                "expected_output_tokens": 2_000,
            },
            "independence_requirements": {
                "minimum_independent_copies": 2,
                "different_model_required": True,
            },
        }
        _, audit = calibration._eligibility_for_global_assignment(
            work,
            endpoints,
            {"complex_reasoning": 0.90},
            {"complex_reasoning"},
            required_copies=2,
            global_company_target=3,
        )
        self.assertEqual(
            "rank-backed-global-company-breadth-insufficient",
            audit["calibration_status"],
        )
        self.assertEqual(2, audit["selected_eligible_company_count"])
        self.assertFalse(audit["calibration_applied"])
        self.assertFalse(audit["proxy_floor_lowered"])


if __name__ == "__main__":
    unittest.main()
