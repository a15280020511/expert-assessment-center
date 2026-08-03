import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_production_ticket as production_ticket  # noqa: E402


class V5ProductionTicketTokenEnvelopeTests(unittest.TestCase):
    @staticmethod
    def _parse(*extra: str):
        return production_ticket.build_parser().parse_args(
            [
                "--task",
                "closed-world test",
                "--maximum-total-calls",
                "4",
                "--maximum-recovery-calls",
                "0",
                *extra,
            ]
        )

    def test_explicit_completion_limit_is_forwarded_to_pipeline(self):
        args = self._parse("--max-completion-tokens", "8000")
        values = production_ticket._pipeline_args(
            args,
            Path("ticket-artifacts"),
            "closed-world test",
        )
        index = values.index("--max-completion-tokens")
        self.assertEqual(values[index + 1], "8000")
        self.assertEqual(args.max_completion_tokens, 8000)

    def test_omitted_completion_limit_preserves_pipeline_default(self):
        args = self._parse()
        values = production_ticket._pipeline_args(
            args,
            Path("ticket-artifacts"),
            "closed-world test",
        )
        self.assertNotIn("--max-completion-tokens", values)
        self.assertIsNone(args.max_completion_tokens)

    def test_non_positive_completion_limit_fails_before_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaisesRegex(
                ValueError,
                "max-completion-tokens must be positive",
            ):
                production_ticket.main(
                    [
                        "--task",
                        "closed-world test",
                        "--output-dir",
                        temp,
                        "--maximum-total-calls",
                        "4",
                        "--maximum-recovery-calls",
                        "0",
                        "--max-completion-tokens",
                        "0",
                    ]
                )


if __name__ == "__main__":
    unittest.main()
