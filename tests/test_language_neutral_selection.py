import importlib.util
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "open-model-market" / "expert_team.py"
SPEC = importlib.util.spec_from_file_location("expert_team_language_neutral", MODULE_PATH)
expert_team = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = expert_team
SPEC.loader.exec_module(expert_team)

FIXTURE = ROOT / "tests" / "fixtures" / "models.json"
CONFIG = ROOT / "open-model-market" / "config.json"


class LanguageNeutralSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def ranked_snapshot(self, chinese: bool):
        args = expert_team.build_parser().parse_args([
            "--task", "评估商业投资、财务回报和风险",
            "--config", str(CONFIG),
            "--catalog-file", str(FIXTURE),
            "--output-dir", str(Path(self.temp.name) / ("zh" if chinese else "neutral")),
        ])
        run = expert_team.build_run_config(args)
        profile = replace(expert_team.classify_task(run.task, run), chinese=chinese)
        models, _ = expert_team.fetch_catalog(run)
        ranked = expert_team.rank_models(models, profile, run)
        return {
            model.id: (round(model.score, 12), tuple(model.fit_reasons))
            for model in ranked
        }

    def test_chinese_detection_does_not_change_model_scores_or_reasons(self):
        chinese = self.ranked_snapshot(True)
        neutral = self.ranked_snapshot(False)
        self.assertEqual(chinese, neutral)
        for _, reasons in chinese.values():
            self.assertNotIn("中文任务适配", reasons)


if __name__ == "__main__":
    unittest.main()
