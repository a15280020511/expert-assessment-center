import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

from execution_graph import GraphLimits  # noqa: E402
import model_market  # noqa: E402
import v5_pipeline  # noqa: E402
import v5_production_ticket  # noqa: E402
from v5_model_company import (  # noqa: E402
    DEFAULT_INTELLIGENCE_RANKING_LIMIT,
    MINIMUM_CANDIDATES_PER_WORK,
)
from v5_planning_diagnostics import build_infeasibility_report  # noqa: E402


def candidate(candidate_id, coverage_key, model):
    work_id = coverage_key.split("#", 1)[0]
    return {
        "candidate_id": candidate_id,
        "interpretation_id": "i1",
        "coverage_keys": [coverage_key],
        "assigned_work": [work_id],
        "copy_indices": [0],
        "professional_capabilities": {"general_analysis": 0.8},
        "functions": ["analysis"],
        "prompt_profile": {"profile_id": f"prompt-{candidate_id}"},
        "reasoning_profile": {"reasoning_enabled": True, "effort": "low"},
        "parameter_profile": {"profile_id": f"params-{candidate_id}"},
        "model": model,
        "provider_endpoint": f"{model}@provider-{candidate_id}",
        "provider_slug": f"provider-{candidate_id}",
        "output_contract": {"required_fields": []},
        "estimated_quality": 0.8,
        "quality_uncertainty": 0.05,
        "estimated_cost": 0.001,
        "failure_probability": 0.02,
        "request_config": {
            "provider": {
                "order": [f"provider-{candidate_id}"],
                "only": [f"provider-{candidate_id}"],
                "allow_fallbacks": False,
                "require_parameters": True,
            }
        },
        "independence_groups": [],
    }


class V5Top150HardeningTests(unittest.TestCase):
    def test_run_config_accepts_top150(self):
        args = SimpleNamespace(
            config=str(model_market.DEFAULT_CONFIG),
            task="测试任务",
            quality_tier=None,
            max_estimated_cost_usd=None,
            ranking_limit=DEFAULT_INTELLIGENCE_RANKING_LIMIT,
            max_completion_tokens=None,
            reasoning_effort=None,
            catalog_file=None,
            output_dir="unused",
            dry_run=True,
            require_live_catalog=False,
        )
        run = model_market.build_run_config(args)
        self.assertEqual(run.ranking_limit, 150)

    def test_pipeline_defaults_to_24_company_diverse_candidates(self):
        args = v5_pipeline.build_parser().parse_args(["--task", "test"])
        self.assertEqual(
            args.maximum_candidates_per_work,
            MINIMUM_CANDIDATES_PER_WORK,
        )

    def test_production_ticket_explicitly_binds_top150_and_24(self):
        args = SimpleNamespace(
            maximum_total_calls=4,
            maximum_recovery_calls=1,
            cost_anomaly_usd=None,
            quality_tier="value",
            require_live_catalog=False,
        )
        command = v5_production_ticket._pipeline_command(
            args,
            Path("artifacts"),
            "task",
        )
        ranking_index = command.index("--ranking-limit")
        candidates_index = command.index("--maximum-candidates-per-work")
        self.assertEqual(command[ranking_index + 1], "150")
        self.assertEqual(command[candidates_index + 1], "24")
        runtime = v5_production_ticket._runtime(args)
        self.assertEqual(
            runtime.config.maximum_candidates_per_work,
            MINIMUM_CANDIDATES_PER_WORK,
        )

    def test_diagnostics_identify_company_shortage(self):
        bundle = {
            "version": 5,
            "candidates": [
                candidate("openai-w1", "w1#0", "openai/model-a"),
                candidate("openai-w2", "w2#0", "openai/model-b"),
            ],
            "interpretations": {
                "i1": {
                    "copies_by_work": {"w1": 1, "w2": 1},
                }
            },
            "candidate_count_before_pareto": 2,
            "candidate_count_after_pareto": 2,
            "pareto_pruned_count": 0,
        }
        report = build_infeasibility_report(
            bundle,
            GraphLimits(
                max_nodes=3,
                max_model_calls=4,
                max_replacements=1,
            ),
            message="No feasible V5 execution graph",
        )
        self.assertEqual(
            report["code"],
            "MODEL_COMPANY_DIVERSITY_INSUFFICIENT",
        )
        row = report["interpretations"][0]
        self.assertEqual(
            row["failure_reason"],
            "model_company_diversity_conflict",
        )
        diagnostic = row["model_company_diagnostic"]
        self.assertTrue(diagnostic["relaxed_feasible"])
        self.assertEqual(diagnostic["minimum_distinct_companies_required"], 2)
        self.assertEqual(diagnostic["available_company_count"], 1)
        self.assertEqual(report["model_calls_performed"], 0)


if __name__ == "__main__":
    unittest.main()
