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
SPEC = importlib.util.spec_from_file_location("expert_team_judge_partial", MODULE_PATH)
expert_team = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = expert_team
SPEC.loader.exec_module(expert_team)

CONFIG = ROOT / "open-model-market" / "config.json"
FIXTURE = ROOT / "tests" / "fixtures" / "models.json"


class JudgePartialEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.history = Path(self.temp.name) / "history.json"
        self.env = mock.patch.dict(os.environ, {"MODEL_HISTORY_PATH": str(self.history)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def run_config(self):
        args = expert_team.build_parser().parse_args([
            "--task", "评估一个复杂技术架构方案",
            "--config", str(CONFIG),
            "--catalog-file", str(FIXTURE),
            "--output-dir", str(Path(self.temp.name) / "out"),
        ])
        return expert_team.build_run_config(args)

    def test_substantial_truncated_judge_is_preserved_as_partial(self):
        run = self.run_config()
        response = {
            "id": "judge-partial",
            "model": "example/judge",
            "choices": [{"finish_reason": "length", "message": {"content": "裁决要点。" * 200}}],
            "usage": {"completion_tokens": 6000, "cost": 0.1},
        }
        adjusted, clean, info, answer, status = expert_team._prepare_judge_response(
            run, response, 12.5, 0.2
        )
        self.assertEqual(status, "success_partial")
        self.assertGreaterEqual(len(answer), expert_team.MIN_USABLE_JUDGE_CHARS)
        self.assertEqual(adjusted["choices"][0]["finish_reason"], "partial_length")
        self.assertEqual(clean["choices"][0]["finish_reason"], "length")
        self.assertEqual(info["finish_reason"], "length")
        saved = json.loads((run.output_dir / "judge-response-raw.json").read_text())
        self.assertEqual(saved["choices"][0]["finish_reason"], "length")

    def test_short_truncated_judge_fails_after_saving_evidence(self):
        run = self.run_config()
        response = {
            "id": "judge-short",
            "model": "example/judge",
            "choices": [{"finish_reason": "length", "message": {"content": "过短" * 20}}],
            "usage": {},
        }
        with self.assertRaises(expert_team.ExpertTeamError):
            expert_team._prepare_judge_response(run, response, 2.0, 0.01)
        self.assertTrue((run.output_dir / "judge-response-raw.json").exists())
        self.assertTrue((run.output_dir / "judge-response-diagnostics.json").exists())

    def test_finalize_marks_partial_report_and_attempt_metadata(self):
        run = self.run_config()
        run.output_dir.mkdir(parents=True, exist_ok=True)
        (run.output_dir / "expert-team-result.json").write_text(
            json.dumps({"status": "success", "judge_response": {}, "judge_diagnostics": {}, "final_answer": "old"}),
            encoding="utf-8",
        )
        (run.output_dir / "expert-team-report.md").write_text(
            "- OpenRouter router/plugin used: `false`\n\n## Final decision\n\nold\n",
            encoding="utf-8",
        )
        clean = {"choices": [{"finish_reason": "length", "message": {"content": "裁决正文"}}]}
        info = {"finish_reason": "length", "model": "example/judge"}
        expert_team._finalize_judge_artifacts(
            run,
            "example/judge",
            clean,
            info,
            "裁决正文",
            "success_partial",
            3.0,
            0.1,
            1,
            False,
        )
        result = json.loads((run.output_dir / "expert-team-result.json").read_text())
        self.assertEqual(result["status"], "success_partial")
        self.assertEqual(result["judge_status"], "success_partial")
        self.assertEqual(result["judge_attempt_count"], 1)
        self.assertFalse(result["judge_replacement_used"])
        report = (run.output_dir / "expert-team-report.md").read_text()
        self.assertIn("不得视为完整裁决", report)
        self.assertIn("Judge attempts: `1`", report)
        self.assertTrue((run.output_dir / "artifact-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
