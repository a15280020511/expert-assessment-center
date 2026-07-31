"""One-shot migration to authoritative V5 release receipts."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "promote-v5-production.yml"
TEST = ROOT / "tests" / "test_v5_release_command_contract.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def update_workflow() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "      - name: Move production ref\n        working-directory: release-target\n",
        "      - name: Move production ref\n        id: move\n        working-directory: release-target\n",
        "move step id",
    )

    marker = "      - name: Record release result\n"
    if text.count(marker) != 1:
        raise RuntimeError("authoritative receipt suffix marker is not unique")
    start = text.index(marker)
    suffix = '''      - name: Verify production ref and create authoritative receipt
        id: receipt
        working-directory: release-target
        env:
          TARGET_SHA: ${{ steps.release.outputs.target_sha }}
          RELEASE_ACTION: ${{ steps.release.outputs.action }}
          REQUEST_ID: ${{ steps.release.outputs.request_id }}
          PREVIOUS_PRODUCTION: ${{ steps.direction.outputs.current_production }}
          RUN_ID: ${{ github.run_id }}
          RUN_URL: https://github.com/${{ github.repository }}/actions/runs/${{ github.run_id }}
        run: |
          set -euo pipefail
          observed="$(git ls-remote origin refs/heads/production | awk '{print $1}')"
          test "$observed" = "$TARGET_SHA"
          mkdir -p release-receipt
          OBSERVED_PRODUCTION="$observed" python - <<'PY'
          import json
          import os
          from pathlib import Path

          receipt = {
              "schema_version": "v5-production-release-receipt-1",
              "status": "production_ref_verified",
              "action": os.environ["RELEASE_ACTION"],
              "target_sha": os.environ["TARGET_SHA"],
              "observed_production_sha": os.environ["OBSERVED_PRODUCTION"],
              "previous_production_sha": os.environ.get("PREVIOUS_PRODUCTION", ""),
              "request_id": os.environ["REQUEST_ID"],
              "workflow_run_id": os.environ["RUN_ID"],
              "workflow_run_url": os.environ["RUN_URL"],
              "runtime": "V5 native production runtime",
              "paid_model_calls": 0,
              "model_cost_usd": 0,
              "cross_task_history_used": False,
          }
          path = Path("release-receipt/release-receipt.json")
          path.write_text(
              json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\\n",
              encoding="utf-8",
          )
          PY
          jq -e \
            --arg target "$TARGET_SHA" \
            --arg request "$REQUEST_ID" \
            '.schema_version == "v5-production-release-receipt-1" and
             .status == "production_ref_verified" and
             .target_sha == $target and
             .observed_production_sha == $target and
             .request_id == $request and
             .paid_model_calls == 0 and
             .model_cost_usd == 0 and
             .cross_task_history_used == false' \
            release-receipt/release-receipt.json >/dev/null
          {
            echo "## V5 production ref verified"
            echo
            echo "- Action: \\`$RELEASE_ACTION\\`"
            echo "- Target: \\`$TARGET_SHA\\`"
            echo "- Observed production: \\`$observed\\`"
            echo "- Request: \\`$REQUEST_ID\\`"
            echo "- Runtime: \\`V5 native production runtime\\`"
            echo "- Paid model calls: \\`0\\`"
            echo "- Model cost: \\`0 USD\\`"
            echo "- Cross-task model history: \\`disabled\\`"
          } >> "$GITHUB_STEP_SUMMARY"

      - name: Upload authoritative release receipt
        id: receipt_artifact
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: v5-production-release-receipt-${{ github.run_id }}
          path: release-target/release-receipt/release-receipt.json
          if-no-files-found: error
          retention-days: 90

      - name: Publish optional PR notification
        id: notify
        if: github.event_name == 'pull_request' && steps.receipt.outcome == 'success'
        continue-on-error: true
        uses: actions/github-script@ff4b64fc288a21d5291396a384c1273f032e6333 # v9.0.0
        env:
          TARGET_SHA: ${{ steps.release.outputs.target_sha }}
          RELEASE_ACTION: ${{ steps.release.outputs.action }}
          REQUEST_ID: ${{ steps.release.outputs.request_id }}
          RUN_URL: https://github.com/${{ github.repository }}/actions/runs/${{ github.run_id }}
        with:
          script: |
            const verb = process.env.RELEASE_ACTION === 'promote' ? 'PROMOTED' : 'ROLLED_BACK';
            await github.rest.issues.createComment({
              owner: context.repo.owner,
              repo: context.repo.repo,
              issue_number: context.issue.number,
              body: `## V5_PRODUCTION_${verb}\\n\\n- Target: \\`${process.env.TARGET_SHA}\\`\\n- Request: \\`${process.env.REQUEST_ID}\\`\\n- Authoritative receipt: \\`v5-production-release-receipt-${context.runId}\\`\\n- Paid model calls: \\`0\\`\\n- Run: \\`${process.env.RUN_URL}\\``
            });

      - name: Record optional notification outcome
        if: always() && steps.receipt.outcome == 'success'
        env:
          NOTIFICATION_OUTCOME: ${{ steps.notify.outcome }}
        run: |
          echo "- Optional PR notification: \\`$NOTIFICATION_OUTCOME\\`" >> "$GITHUB_STEP_SUMMARY"
'''
    WORKFLOW.write_text(text[:start] + suffix, encoding="utf-8")


def update_test() -> None:
    text = TEST.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '        move = self.text.index("name: Move production ref")\n\n',
        '        move = self.text.index("name: Move production ref")\n'
        '        verify = self.text.index(\n'
        '            "name: Verify production ref and create authoritative receipt"\n'
        '        )\n'
        '        receipt_artifact = self.text.index(\n'
        '            "name: Upload authoritative release receipt"\n'
        '        )\n'
        '        notify = self.text.index("name: Publish optional PR notification")\n\n',
        "release order indices",
    )
    text = replace_once(
        text,
        "        self.assertLess(dry, move)\n",
        "        self.assertLess(dry, move)\n"
        "        self.assertLess(move, verify)\n"
        "        self.assertLess(verify, receipt_artifact)\n"
        "        self.assertLess(receipt_artifact, notify)\n",
        "release order assertions",
    )

    anchor = "    def test_release_reuses_direction_and_rollback_guards(self):\n"
    methods = '''    def test_authoritative_receipt_verifies_remote_ref_and_is_retained(self):
        self.assertIn("id: move", self.text)
        self.assertIn("id: receipt", self.text)
        self.assertIn(
            "git ls-remote origin refs/heads/production",
            self.text,
        )
        self.assertIn('test "$observed" = "$TARGET_SHA"', self.text)
        self.assertIn(
            '"status": "production_ref_verified"',
            self.text,
        )
        self.assertIn(
            '"observed_production_sha": os.environ["OBSERVED_PRODUCTION"]',
            self.text,
        )
        self.assertIn("release-receipt/release-receipt.json", self.text)
        self.assertIn("name: v5-production-release-receipt-${{ github.run_id }}", self.text)
        self.assertIn("if-no-files-found: error", self.text)
        self.assertIn("retention-days: 90", self.text)
        self.assertIn("paid_model_calls", self.text)
        self.assertIn("model_cost_usd", self.text)

    def test_pr_comment_is_optional_and_cannot_change_release_truth(self):
        self.assertIn("name: Publish optional PR notification", self.text)
        self.assertIn("id: notify", self.text)
        self.assertIn("continue-on-error: true", self.text)
        self.assertIn(
            "if: github.event_name == 'pull_request' && steps.receipt.outcome == 'success'",
            self.text,
        )
        self.assertIn("name: Record optional notification outcome", self.text)
        self.assertIn("NOTIFICATION_OUTCOME: ${{ steps.notify.outcome }}", self.text)
        self.assertNotIn("name: Publish release receipt", self.text)
        self.assertNotIn("name: Record release result", self.text)

'''
    text = replace_once(text, anchor, methods + anchor, "authoritative receipt tests")
    TEST.write_text(text, encoding="utf-8")


def main() -> int:
    update_workflow()
    update_test()
    run(sys.executable, "-m", "ruff", "check", ".")
    run(sys.executable, "-m", "compileall", "-q", "open-model-market", "tests", "tools")
    run(
        sys.executable,
        "-m",
        "unittest",
        "discover",
        "-s",
        "tests",
        "-p",
        "test_v5_release_command_contract.py",
        "-v",
    )
    (ROOT / ".github/workflows/apply-release-receipt-authority.yml").unlink()
    Path(__file__).unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
