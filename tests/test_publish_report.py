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
        self.assertIn(
            hashlib.sha256(report.encode("utf-8")).hexdigest(),
            comments[0],
        )
        self.assertIn("expert-team-report-run:123:part:001", comments[0])
        self.assertIn("FULL_SUCCESS", comments[0])

    def test_long_unicode_report_is_split_without_loss(self):
        report = "".join(
            f"第{i}段：这是完整中文裁判内容。\n" for i in range(600)
        )
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
        self.assertTrue(
            all(
                f"part:{index:03d}" in item
                for index, item in enumerate(comments, 1)
            )
        )

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
            stored = json.loads(
                (output_dir / "report-comments-manifest.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(stored, manifest)
            self.assertEqual(
                len(manifest["files"]),
                manifest["comment_count"],
            )
            self.assertEqual(manifest["run_id"], "789")
            self.assertEqual(
                manifest["run_url"],
                "https://github.com/owner/repo/actions/runs/789",
            )
            self.assertEqual(
                manifest["publication_status"],
                "prepared_full_success",
            )
            self.assertEqual(manifest["delivery_status"], "full_success")
            self.assertTrue(
                all((output_dir / name).exists() for name in manifest["files"])
            )

    def _write_result(
        self,
        root: Path,
        *,
        completion_mode: str,
        quality_status: str,
        integrity_status: str,
        node_status: str = "success",
        contract_complete: bool = True,
        allow_degraded: bool = False,
        coverage_ratio: float = 0.0,
        minimum_coverage: float = 1.0,
        successful_content_nodes: int = 0,
    ) -> None:
        summary = {
            "status": "success",
            "completion_mode": completion_mode,
            "quality_status": quality_status,
            "quality_integrity": {"status": integrity_status},
            "delivery_policy": {
                "allow_degraded_success": allow_degraded,
                "blockers": [],
                "missing_non_degradable_work_ids": [],
            },
            "work_coverage": {
                "coverage_ratio": coverage_ratio,
                "minimum_degraded_coverage": minimum_coverage,
                "successful_content_nodes": successful_content_nodes,
            },
        }
        (root / "v5-execution-summary.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        (root / "expert-team-result.json").write_text(
            json.dumps(summary), encoding="utf-8"
        )
        (root / "v5-node-results.json").write_text(
            json.dumps(
                [
                    {
                        "node_id": "node-1",
                        "status": node_status,
                        "contract": {
                            "required_fields_complete": contract_complete
                        },
                    }
                ]
            ),
            encoding="utf-8",
        )

    def test_publication_gate_accepts_full_integrity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_result(
                root,
                completion_mode="full",
                quality_status="full_success",
                integrity_status="PASS",
            )
            allowed, blockers = publish_report.strict_publication_gate(root)
            self.assertTrue(allowed)
            self.assertEqual(blockers, [])

    def test_publication_gate_accepts_audited_degraded_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_result(
                root,
                completion_mode="degraded",
                quality_status="degraded_success",
                integrity_status="DEGRADED",
                node_status="success_recovered",
                contract_complete=True,
                allow_degraded=True,
                coverage_ratio=0.75,
                minimum_coverage=2 / 3,
                successful_content_nodes=1,
            )
            allowed, blockers = publish_report.strict_publication_gate(root)
            self.assertTrue(allowed, blockers)

    def test_publication_gate_blocks_unaudited_or_incomplete_degradation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self._write_result(
                root,
                completion_mode="degraded",
                quality_status="degraded_success",
                integrity_status="DEGRADED",
                node_status="success_recovered",
                contract_complete=False,
                allow_degraded=False,
                coverage_ratio=0.5,
                minimum_coverage=2 / 3,
                successful_content_nodes=0,
            )
            allowed, blockers = publish_report.strict_publication_gate(root)
            self.assertFalse(allowed)
            self.assertIn("degraded-delivery:not-authorized", blockers)
            self.assertIn("degraded-delivery:insufficient-coverage", blockers)
            self.assertIn(
                "degraded-delivery:no-strict-successful-content-node",
                blockers,
            )
            self.assertIn("node-contract-incomplete:node-1", blockers)

    def test_skip_manifest_removes_stale_public_comment_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            comments = root / "comments"
            comments.mkdir()
            (comments / "report-comment-001.md").write_text(
                "unsafe stale report",
                encoding="utf-8",
            )
            report = root / "v5-final-report.md"
            report.write_text("degraded report", encoding="utf-8")
            self._write_result(
                root,
                completion_mode="degraded",
                quality_status="degraded_success",
                integrity_status="DEGRADED",
                contract_complete=False,
            )
            manifest = publish_report.write_skip_manifest(
                root,
                report,
                comments,
                run_url="https://github.com/owner/repo/actions/runs/999",
                max_chars=5000,
                publication_status="skipped_non_audited_execution",
                blockers=["degraded-delivery:not-authorized"],
            )
            self.assertEqual(manifest["comment_count"], 0)
            self.assertEqual(manifest["files"], [])
            self.assertFalse(
                (comments / "report-comment-001.md").exists()
            )

    def test_degraded_comment_discloses_status(self):
        comments = publish_report.render_comments(
            "usable report",
            run_url="https://github.com/owner/repo/actions/runs/321",
            max_chars=5000,
            delivery_status="degraded_success",
        )
        self.assertIn("DEGRADED_SUCCESS", comments[0])
        self.assertIn("缺失、失败和降级项目", comments[0])

    def test_empty_report_is_rejected(self):
        with self.assertRaises(ValueError):
            publish_report.render_comments(
                "",
                run_url="https://github.com/owner/repo/actions/runs/123",
                max_chars=5000,
            )

    def test_missing_or_unknown_run_identity_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "GitHub Actions run"):
            publish_report.render_comments(
                "report",
                run_url="",
                max_chars=5000,
            )
        with self.assertRaisesRegex(ValueError, "numeric"):
            publish_report.render_comments(
                "report",
                run_url=(
                    "https://github.com/owner/repo/actions/runs/unknown"
                ),
                max_chars=5000,
            )


if __name__ == "__main__":
    unittest.main()
