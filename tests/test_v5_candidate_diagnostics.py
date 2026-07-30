import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_candidate_diagnostics as diagnostics  # noqa: E402


class TestV5CandidateDiagnostics(unittest.TestCase):
    @staticmethod
    def market():
        return {
            "endpoints": [
                {
                    "model_id": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                },
                {
                    "model_id": "b/model",
                    "provider_slug": "p2",
                    "provider_endpoint": "b/model@p2",
                },
            ]
        }

    def test_reports_explicit_independent_copy_model_shortage(self):
        bundle = {
            "candidate_count_before_pareto": 2,
            "candidate_count_after_pareto": 2,
            "interpretations": {
                "i1": {"copies_by_work": {"w1": 2}}
            },
            "candidates": [
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#0"],
                    "model": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                    "independence_groups": ["w1"],
                },
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#1"],
                    "model": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                    "independence_groups": ["w1"],
                },
            ],
        }
        report = diagnostics.analyze_candidate_structure(self.market(), bundle)
        row = report["interpretations"][0]
        work = row["work_coverage"][0]
        self.assertFalse(row["local_structure_feasible"])
        self.assertIn("w1:distinct-models=1<required-copies=2", row["blockers"])
        self.assertNotIn("w1:distinct-endpoints=1<required-copies=2", row["blockers"])
        self.assertTrue(work["independence_policy"]["different_model_required"])
        self.assertFalse(work["independence_policy"]["different_provider_required"])
        self.assertEqual(report["model_calls"], 0)

    def test_ordinary_redundant_copies_can_reuse_model_and_provider(self):
        bundle = {
            "candidate_count_before_pareto": 2,
            "candidate_count_after_pareto": 2,
            "interpretations": {
                "i1": {"copies_by_work": {"w1": 2}}
            },
            "candidates": [
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#0"],
                    "model": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                    "independence_groups": [],
                },
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#1"],
                    "model": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                    "independence_groups": [],
                },
            ],
        }
        report = diagnostics.analyze_candidate_structure(self.market(), bundle)
        row = report["interpretations"][0]
        work = row["work_coverage"][0]
        self.assertTrue(row["local_structure_feasible"])
        self.assertTrue(work["local_independence_feasible"])
        self.assertFalse(work["independence_policy"]["different_model_required"])
        self.assertFalse(work["independence_policy"]["different_provider_required"])
        self.assertFalse(row["blockers"])

    def test_reports_two_model_structure_as_locally_feasible_without_provider_hard_gate(self):
        bundle = {
            "candidate_count_before_pareto": 2,
            "candidate_count_after_pareto": 2,
            "interpretations": {
                "i1": {"copies_by_work": {"w1": 2}}
            },
            "candidates": [
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#0"],
                    "model": "a/model",
                    "provider_slug": "shared",
                    "provider_endpoint": "a/model@shared",
                    "independence_groups": ["w1"],
                },
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#1"],
                    "model": "b/model",
                    "provider_slug": "shared",
                    "provider_endpoint": "b/model@shared",
                    "independence_groups": ["w1"],
                },
            ],
        }
        report = diagnostics.analyze_candidate_structure(self.market(), bundle)
        row = report["interpretations"][0]
        work = row["work_coverage"][0]
        self.assertTrue(row["local_structure_feasible"])
        self.assertTrue(report["local_structure_feasible_for_any_interpretation"])
        self.assertFalse(row["blockers"])
        self.assertEqual(work["union_distinct_model_count"], 2)
        self.assertEqual(work["union_distinct_provider_count"], 1)
        self.assertTrue(work["local_independence_feasible"])

    def test_explicit_provider_requirement_is_honored_when_present(self):
        bundle = {
            "interpretations": {
                "i1": {
                    "copies_by_work": {"w1": 2},
                    "independence_policy_by_work": {
                        "w1": {
                            "different_model_required": False,
                            "different_provider_required": True,
                        }
                    },
                }
            },
            "candidates": [
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#0"],
                    "model": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                    "independence_groups": [],
                },
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#1"],
                    "model": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                    "independence_groups": [],
                },
            ],
        }
        report = diagnostics.analyze_candidate_structure(self.market(), bundle)
        blockers = report["interpretations"][0]["blockers"]
        self.assertIn("w1:distinct-providers=1<required-copies=2", blockers)

    def test_reports_missing_copy_candidate(self):
        bundle = {
            "interpretations": {"i1": {"copies_by_work": {"w1": 2}}},
            "candidates": [
                {
                    "interpretation_id": "i1",
                    "coverage_keys": ["w1#0"],
                    "model": "a/model",
                    "provider_slug": "p1",
                    "provider_endpoint": "a/model@p1",
                    "independence_groups": [],
                }
            ],
        }
        report = diagnostics.analyze_candidate_structure(self.market(), bundle)
        blockers = report["interpretations"][0]["blockers"]
        self.assertIn("w1#1:no-candidate", blockers)


if __name__ == "__main__":
    unittest.main()
