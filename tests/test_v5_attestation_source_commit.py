from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import v5_final_attestation as attestation  # noqa: E402


PRODUCTION_SHA = "f" * 40
EVENT_CONTEXT_SHA = "4" * 40


class V5AttestationSourceCommitTests(unittest.TestCase):
    def test_checked_out_commit_sha_reads_exact_git_head(self) -> None:
        completed = SimpleNamespace(stdout=f"{PRODUCTION_SHA}\n")
        with mock.patch.object(
            attestation.subprocess,
            "run",
            return_value=completed,
        ) as run:
            self.assertEqual(
                PRODUCTION_SHA,
                attestation.checked_out_commit_sha(),
            )
        run.assert_called_once_with(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )

    def test_checked_out_commit_sha_rejects_non_full_sha(self) -> None:
        completed = SimpleNamespace(stdout="main\n")
        with mock.patch.object(
            attestation.subprocess,
            "run",
            return_value=completed,
        ):
            with self.assertRaisesRegex(RuntimeError, "not a full Git SHA"):
                attestation.checked_out_commit_sha()

    def test_main_attests_checkout_not_issue_event_context(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="v5-attestation-source-"
        ) as directory:
            root = Path(directory)
            final_status = root / "final-status.md"
            final_status.write_text("status", encoding="utf-8")
            (root / "final-status.json").write_text(
                json.dumps({"status": "PASS"}),
                encoding="utf-8",
            )
            output = root / "final-attestation.json"
            argv = [
                "v5_final_attestation.py",
                "--output-dir",
                str(root),
                "--primary-artifact-id",
                "123",
                "--primary-artifact-digest",
                "digest",
                "--audit-status",
                "PASS",
                "--run-id",
                "456",
                "--commit-sha",
                EVENT_CONTEXT_SHA,
                "--final-status-file",
                str(final_status),
                "--output",
                str(output),
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(
                    attestation,
                    "checked_out_commit_sha",
                    return_value=PRODUCTION_SHA,
                ),
                mock.patch.object(
                    attestation,
                    "build_final_attestation_record",
                    return_value={"commit_sha": PRODUCTION_SHA},
                ) as build,
            ):
                self.assertEqual(0, attestation.main())

            self.assertEqual(
                PRODUCTION_SHA,
                build.call_args.kwargs["commit_sha"],
            )
            record = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(PRODUCTION_SHA, record["commit_sha"])
            self.assertEqual(
                "checked-out-git-head",
                record["commit_sha_source"],
            )
            self.assertEqual(
                EVENT_CONTEXT_SHA,
                record["event_context_commit_sha"],
            )
            self.assertFalse(
                record["event_context_commit_matched_checkout"]
            )
            self.assertEqual(
                "PASS",
                record["authoritative_delivery_status"],
            )


if __name__ == "__main__":
    unittest.main()
