import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "open-model-market"))

import publish_report  # noqa: E402


class PublishReportTests(unittest.TestCase):
    def test_short_report_is_one_complete_comment(self):
        report = "# 裁判报告\n\n最终结论：采用方案一。\n"
        comments = publish_report.render_comments(
            report,
            run_url="https://github.com/owner/repo/actions/runs/123",
            max_chars=5000,
        )
        self.assertEqual(len(comments), 1)
        self.assertIn("EXPERT_TEAM_REPORT 1/1", comments[0])
        self.assertIn(report, comments[0])
        self.assertIn(hashlib.sha256(report.encode("utf-8")).hexdigest(), comments[0])
        self.assertIn("expert-team-report-run:123:part:001", comments[0])

    def test_long_unicode_report_is_split_without_loss(self):
        report = "".join(f"第{i}段：这是完整中文裁判内容。\n" for i in range(600))
        payloads = publish_report.split_text(report, 700)
        self.assertGreater(len(payloads), 1)
        self.assertEqual("".join(payloads), report)
        self.assertTrue(all(len(item) <= 700 for item in payloads))

        comments = publish_report.render_comments(
            report,
            run_url="https://github.com/owner/repo/actions/runs/456",
            max_chars=1800,
        )
        self.assertGreater(len(comments), 1)
        self.assertTrue(all(len(item) <= 1800 for item in comments))
        self.assertTrue(all(f"part:{index:03d}" in item for index, item in enumerate(comments, 1)))

    def test_write_comments_creates_manifest_and_numbered_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report_path = root / "expert-team-report.md"
            output_dir = root / "comments"
            report_path.write_text("完整报告\n" * 100, encoding="utf-8")
            manifest = publish_report.write_comments(
                report_path,
                output_dir,
                run_url="https://github.com/owner/repo/actions/runs/789",
                max_chars=1800,
            )
            stored = json.loads((output_dir / "report-comments-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(stored, manifest)
            self.assertEqual(len(manifest["files"]), manifest["comment_count"])
            self.assertTrue(all((output_dir / name).exists() for name in manifest["files"]))

    def test_empty_report_is_rejected(self):
        with self.assertRaises(ValueError):
            publish_report.render_comments("", run_url="", max_chars=5000)


if __name__ == "__main__":
    unittest.main()
