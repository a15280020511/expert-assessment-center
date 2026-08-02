from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "workflow-remediation" / ".github" / "workflows"
OUT.mkdir(parents=True, exist_ok=True)

execution = (ROOT / ".github/workflows/execution-ticket.yml").read_text(encoding="utf-8")
for exact in (
    '      quality_tier: ${{ steps.ticket.outputs.quality_tier }}\n',
    '            --expected-quality-tier "${{ needs.admit-ticket.outputs.quality_tier }}" \\\n',
    '            --quality-tier "${{ needs.admit-ticket.outputs.quality_tier }}"\n',
):
    if exact not in execution:
        raise SystemExit(f"execution workflow anchor missing: {exact!r}")
    execution = execution.replace(exact, "", 1)
(OUT / "execution-ticket.yml").write_text(execution, encoding="utf-8")


def expr(name: str) -> str:
    return "$" + "{{" + f" {name} " + "}}"


paid = '''name: V5 Final Paid Claude Acceptance

on:
  workflow_dispatch:
    inputs:
      confirm:
        description: Type RUN-EXACTLY-ONCE to authorize the bounded paid acceptance
        required: true
        type: string

permissions:
  contents: read

concurrency:
  group: v5-final-paid-claude-acceptance
  cancel-in-progress: false

jobs:
  paid-acceptance:
    if: __ACTOR__ == __OWNER__
    runs-on: ubuntu-24.04
    timeout-minutes: 25
    env:
      OPENROUTER_API_KEY: __OPENROUTER_SECRET__
      MODEL_MAX_RETRIES: "0"
      MODEL_TIMEOUT_SECONDS: "240"
      PARALLEL_WORKERS: "1"
      OPENROUTER_SITE_URL: https://github.com/__REPOSITORY__
      OPENROUTER_APP_NAME: expert-center-final-paid-acceptance
    steps:
      - name: Check explicit authorization
        run: test "__CONFIRM__" = "RUN-EXACTLY-ONCE"

      - name: Check out exact dispatched source
        uses: actions/checkout@de0fac2e4500dabe0009e67214ff5f5447ce83dd # v6.0.2
        with:
          ref: __SHA__
          fetch-depth: 0
          persist-credentials: false

      - name: Set up pinned Python
        uses: actions/setup-python@a309ff8b426b58ec0e2a45f0f869d46889d02405 # v6.2.0
        with:
          python-version: "3.12"
          cache: pip
          cache-dependency-path: requirements-runtime.txt

      - name: Install pinned runtime
        run: |
          set -euo pipefail
          test -n "$OPENROUTER_API_KEY"
          python -m pip install --disable-pip-version-check --no-input -r requirements-runtime.txt
          python -m pip check

      - name: Execute one bounded real task
        run: |
          set -euo pipefail
          task='仅依据以下题面，不得调用外部工具，不得补充外部事实：A方案月费20元、月流量100GB；B方案月费30元、月流量150GB。请严格按顺序输出两个Markdown二级标题：已知事实、最终建议。最终建议必须给出唯一推荐和两条理由，明确区分事实与推断，不得新增题面外数字。'
          python open-model-market/v5_pipeline.py \
            --task "$task" \
            --output-dir paid-acceptance-artifacts \
            --ranking-limit 12 \
            --maximum-total-calls 4 \
            --maximum-recovery-calls 0 \
            --cost-anomaly-usd 0.25 \
            --max-completion-tokens 512 \
            --reasoning-effort low \
            --require-live-catalog

      - name: Verify paid-call constitution
        env:
          ACCEPTANCE_SHA: __SHA__
          ACCEPTANCE_RUN_ID: __RUN_ID__
        run: |
          python - <<'PY_VERIFY'
          import json
          import os
          from pathlib import Path

          root = Path("paid-acceptance-artifacts")
          calls = json.loads((root / "v5-governance-calls.json").read_text())
          governance = json.loads((root / "v5-governance-result.json").read_text())
          resolution = json.loads((root / "v5-governance-models.json").read_text())
          result = json.loads((root / "v5-result.json").read_text())
          audit = json.loads((root / "v5-request-audit.json").read_text())
          selection = json.loads((root / "v5-selection.json").read_text())
          kinds = [row["kind"] for row in calls["calls"]]
          assert resolution["status"] == "PASS"
          assert resolution["provider_fallback_allowed"] is False
          assert resolution["gpt"]["logical_model"] == "~openai/gpt-latest"
          assert resolution["claude"]["logical_model"] == "~anthropic/claude-opus-latest"
          assert kinds == ["gpt_proposal", "claude_red_team", "gpt_synthesis"]
          assert calls["actual_governance_calls"] == 3
          assert governance["claude_review_count"] == 1
          assert governance["gpt_synthesis_count"] == 1
          assert governance["claude_is_advisory_only"] is True
          assert governance["claude_gatekeeping_allowed"] is False
          assert governance["second_claude_review_allowed"] is False
          assert governance["model_loop_allowed"] is False
          assert selection["local_task_classification_used"] is False
          assert selection["local_atomic_work_generation_used"] is False
          assert selection["local_resource_matrix_used"] is False
          assert selection["local_scoring_used"] is False
          assert selection["optimizer_used"] is False
          assert result["total_model_calls"] == 4
          assert result["actual_cost_usd"] <= 0.25 + 1e-12
          assert audit["status"] == "PASS"
          assert audit["request_count"] == 4
          assert audit["external_tools_allowed"] is False
          receipt = {
              "schema_version": "v5-final-paid-claude-acceptance-3",
              "status": "PASS",
              "commit_sha": os.environ["ACCEPTANCE_SHA"],
              "workflow_run_id": os.environ["ACCEPTANCE_RUN_ID"],
              "governance_sequence": kinds,
              "governance_calls": 3,
              "claude_calls": 1,
              "expert_calls": 1,
              "total_model_calls": result["total_model_calls"],
              "actual_cost_usd": result["actual_cost_usd"],
              "claude_is_advisory_only": True,
              "claude_gatekeeping_allowed": False,
              "old_local_planner_used": False,
              "optimizer_used": False,
          }
          (root / "paid-acceptance-receipt.json").write_text(
              json.dumps(receipt, ensure_ascii=False, indent=2),
              encoding="utf-8",
          )
          PY_VERIFY

      - name: Upload paid acceptance evidence
        if: always()
        uses: actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a # v7.0.1
        with:
          name: v5-final-paid-claude-acceptance-__RUN_ID__
          path: paid-acceptance-artifacts/
          if-no-files-found: error
          retention-days: 30
'''
for token, value in {
    "__ACTOR__": expr("github.actor"),
    "__OWNER__": expr("github.repository_owner"),
    "__OPENROUTER_SECRET__": expr("secrets.OPENROUTER_API_KEY"),
    "__REPOSITORY__": expr("github.repository"),
    "__CONFIRM__": expr("inputs.confirm"),
    "__SHA__": expr("github.sha"),
    "__RUN_ID__": expr("github.run_id"),
}.items():
    paid = paid.replace(token, value)
(OUT / "v5-final-paid-claude-acceptance-20260803.yml").write_text(paid, encoding="utf-8")

print(OUT)
