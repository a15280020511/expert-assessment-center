import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "open-model-market" / "expert_team.py"
SPEC = importlib.util.spec_from_file_location("expert_team_seat", MODULE)
expert_team = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = expert_team
SPEC.loader.exec_module(expert_team)


class SeatScoringTests(unittest.TestCase):
    def test_red_seat_prefers_risk_fit(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.dict(os.environ, {"MODEL_HISTORY_PATH": str(Path(temp) / "history.json")}, clear=False):
            args = expert_team.build_parser().parse_args(["--task", "Static validation: compare software investment with technical financial and risk constraints", "--config", str(ROOT / "open-model-market" / "config.json"), "--catalog-file", str(ROOT / "tests" / "fixtures" / "models.json"), "--output-dir", temp])
            run = expert_team.build_run_config(args)
            profile = expert_team.classify_task(run.task, run)
            models, _ = expert_team.fetch_catalog(run)
            ranked = expert_team.rank_models(models, profile, run)
            experts, _, _ = expert_team.select_team(ranked, profile, run)
            red = next(item for item in experts if item.seat_key == "red")
            self.assertEqual(red.model_id, "kappa/risk")
            self.assertIn("风险反证匹配", red.selection_reason)


if __name__ == "__main__":
    unittest.main()
