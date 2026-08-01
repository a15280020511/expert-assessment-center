from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import artifact_manifest  # noqa: E402


EXECUTION_SHA = "a" * 40
EVENT_SHA = "b" * 40


class V5ArtifactManifestProvenanceTests(unittest.TestCase):
    def test_explicit_execution_sha_is_authoritative(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "V5_EXECUTION_COMMIT_SHA": EXECUTION_SHA,
                "GITHUB_SHA": EVENT_SHA,
            },
            clear=False,
        ):
            sha, source = artifact_manifest.checked_out_commit_sha()
        self.assertEqual(EXECUTION_SHA, sha)
        self.assertEqual("V5_EXECUTION_COMMIT_SHA", source)

    def test_git_checkout_is_used_before_event_context(self) -> None:
        completed = SimpleNamespace(stdout=f"{EXECUTION_SHA}\n")
        with (
            mock.patch.dict(
                os.environ,
                {"V5_EXECUTION_COMMIT_SHA": "", "GITHUB_SHA": EVENT_SHA},
                clear=False,
            ),
            mock.patch.object(
                artifact_manifest.subprocess,
                "run",
                return_value=completed,
            ),
        ):
            sha, source = artifact_manifest.checked_out_commit_sha()
        self.assertEqual(EXECUTION_SHA, sha)
        self.assertEqual("checked-out-git-head", source)

    def test_manifest_records_event_mismatch_without_relabeling_execution(self) -> None:
        with tempfile.TemporaryDirectory(prefix="v5-manifest-") as directory:
            root = Path(directory)
            (root / "result.json").write_text("{}", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {
                    "V5_EXECUTION_COMMIT_SHA": EXECUTION_SHA,
                    "GITHUB_SHA": EVENT_SHA,
                },
                clear=False,
            ):
                manifest = artifact_manifest.write_manifest(root)
            stored = json.loads(
                (root / "artifact-manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(EXECUTION_SHA, manifest["execution_commit_sha"])
            self.assertEqual(EXECUTION_SHA, stored["github_sha"])
            self.assertEqual(EVENT_SHA, stored["event_context_commit_sha"])
            self.assertFalse(stored["event_context_commit_matched_checkout"])
            self.assertEqual("result.json", stored["files"][0]["path"])


if __name__ == "__main__":
    unittest.main()
