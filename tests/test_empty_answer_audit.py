import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "open-model-market" / "expert_team.py"
SPEC = importlib.util.spec_from_file_location("expert_team_empty", MODULE_PATH)
expert_team = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = expert_team
SPEC.loader.exec_module(expert_team)
import response_audit  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "models.json"
CONFIG = ROOT / "open-model-market" / "config.json"


class EmptyAnswerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.env = mock.patch.dict(os.environ, {"MODEL_HISTORY_PATH": str(Path(self.temp.name) / "history.json")}, clear=False)
        self.env.start(); self.addCleanup(self.env.stop)

    def prepare(self, task):
        args = expert_team.build_parser().parse_args(["--task", task, "--config", str(CONFIG), "--catalog-file", str(FIXTURE), "--output-dir", str(Path(self.temp.name) / "out")])
        run = expert_team.build_run_config(args)
        profile = expert_team.classify_task(run.task, run)
        models, _ = expert_team.fetch_catalog(run)
        ranked = expert_team.rank_models(models, profile, run)
        experts, judge, _ = expert_team.select_team(ranked, profile, run)
        return run, profile, ranked, experts, judge

    def test_content_array_is_normalized(self):
        response = {"choices": [{"message": {"content": [{"type": "text", "text": "完整答案"}]}}]}
        self.assertEqual(response_audit.extract_answer(response), "完整答案")

    def test_reasoning_only_is_diagnostic_not_answer(self):
        response = {"id": "r1", "model": "example/reasoner", "choices": [{"finish_reason": "length", "message": {"content": "", "reasoning": "private chain"}}], "usage": {"completion_tokens": 3000, "completion_tokens_details": {"reasoning_tokens": 3000}, "cost": 0.01}}
        with self.assertRaises(expert_team.ExpertTeamError) as caught:
            response_audit.extract_answer(response)
        self.assertIn("reasoning but no final answer", str(caught.exception))
        self.assertNotIn("private chain", str(caught.exception))

    def test_geopolitical_metadata_isolation_and_profession(self):
        _, profile, _, experts, judge = self.prepare("任务目标：由GitHub专家团分析外交会谈。来源URL=https://example.com/api。分析俄方短中长期目标、证据缺口和替代解释")
        self.assertEqual(profile.primary_domain, "international_relations")
        self.assertTrue(profile.high_stakes)
        self.assertIn("国际关系", experts[0].profession)
        self.assertIn("国际战略", judge.profession)


if __name__ == "__main__":
    unittest.main()
