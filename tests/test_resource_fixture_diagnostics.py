import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import expert_team  # noqa: E402
import resource_plan_optimizer as optimizer  # noqa: E402
import resource_requirements as requirements  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "models.json"
CONFIG = ROOT / "open-model-market" / "config.json"


class ResourceFixtureCompatibilityTests(unittest.TestCase):
    def test_fixture_resource_market_has_auditable_candidates(self):
        tasks = [
            "分析复杂商业架构风险",
            "分析外交战争制裁和重大升级风险",
            "复杂商业、代码和风险建模比较",
        ]
        reports = []
        for task in tasks:
            with tempfile.TemporaryDirectory() as temp:
                args = expert_team.build_parser().parse_args([
                    "--task", task, "--config", str(CONFIG),
                    "--catalog-file", str(FIXTURE), "--output-dir", temp,
                ])
                run = expert_team.build_run_config(args)
                profile = expert_team.classify_task(run.task, run)
                models, _ = expert_team.fetch_catalog(run)
                ranked = expert_team.rank_models(models, profile, run)
                matrix = requirements.compile_requirements(profile, run)
                packages = optimizer.generate_packages(matrix)
                pool = optimizer.legacy._eligible_pool(ranked, profile)
                package_rows = []
                for package in packages + [optimizer._synthesis(matrix)]:
                    eligible = [model.id for model in pool if optimizer._supports(model, package)]
                    package_rows.append({
                        "id": package["id"],
                        "units": package["unit_ids"],
                        "reasoning": package["minimum_reasoning_level"],
                        "structured": package["structured_output_required"],
                        "eligible": eligible,
                    })
                reports.append({
                    "task": task,
                    "profile": {
                        "domains": profile.domains,
                        "high_stakes": profile.high_stakes,
                        "requested_context": profile.requested_context,
                    },
                    "coverage": matrix["coverage_requirements"],
                    "constraints": matrix["constraints"],
                    "pool": [model.id for model in pool],
                    "packages": package_rows,
                })
        self.assertTrue(all(report["pool"] for report in reports))
        self.assertTrue(all(all(row["eligible"] for row in report["packages"]) for report in reports))


if __name__ == "__main__":
    unittest.main()
