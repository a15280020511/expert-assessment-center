import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_live_benchmark_economy_r6 as r6  # noqa: E402


class TestV5BenchmarkR6(unittest.TestCase):
    def test_candidate_labels_are_normalized(self):
        self.assertEqual(r6.canonical_candidate_label("C1"), "C1")
        self.assertEqual(r6.canonical_candidate_label("候选C1"), "C1")
        self.assertEqual(r6.canonical_candidate_label("候选 C2"), "C2")
        self.assertEqual(r6.canonical_candidate_label(" 候选  c3 "), "C3")

    def test_score_keys_and_ranking_are_normalized(self):
        payload = {
            "scores": {
                "候选 C1": {"total_score": 80},
                "候选C2": {"total_score": 70},
            },
            "ranking": ["候选C1", "候选 C2"],
        }
        parsed = r6.normalized_extract_json_object(json.dumps(payload, ensure_ascii=False))
        self.assertEqual(set(parsed["scores"]), {"C1", "C2"})
        self.assertEqual(parsed["ranking"], ["C1", "C2"])

    def test_prepare_enforces_10000_token_allowance(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            event = root / "event.json"
            event.write_text(
                json.dumps(
                    {
                        "issue": {
                            "number": 59,
                            "body": json.dumps(
                                {
                                    "output_allowance_tokens": 1800,
                                    "task_ids": ["retail-expansion-unit-economics"],
                                }
                            ),
                        }
                    }
                ),
                encoding="utf-8",
            )
            output = root / "out"
            self.assertEqual(r6.prepare(event, output), 0)
            config = json.loads((output / "benchmark-config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["output_allowance_tokens"], 10000)

    def test_workflow_uses_r6_and_active_10000_allowance(self):
        workflow = (
            ROOT / ".github" / "workflows" / "v5-live-benchmark-final.yml"
        ).read_text(encoding="utf-8")
        self.assertIn('V5_BENCHMARK_OUTPUT_ALLOWANCE_TOKENS: "10000"', workflow)
        self.assertGreaterEqual(workflow.count("v5_live_benchmark_economy_r6.py"), 3)
        self.assertNotIn("secrets.OPENROUTER_MANAGEMENT_KEY", workflow)


if __name__ == "__main__":
    unittest.main()
