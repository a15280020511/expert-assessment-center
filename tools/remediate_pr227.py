from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def file(path: str) -> Path:
    return ROOT / path


def replace_exact(path: str, old: str, new: str = "", *, count: int = 1) -> None:
    target = file(path)
    text = target.read_text(encoding="utf-8")
    hits = text.count(old)
    if hits < count:
        raise SystemExit(
            f"{path}: expected at least {count} occurrences, found {hits}: {old!r}"
        )
    target.write_text(text.replace(old, new, count), encoding="utf-8")


def remove_all(path: str, snippets: tuple[str, ...]) -> None:
    target = file(path)
    text = target.read_text(encoding="utf-8")
    for snippet in snippets:
        if snippet not in text:
            raise SystemExit(f"{path}: missing required snippet {snippet!r}")
        text = text.replace(snippet, "")
    target.write_text(text, encoding="utf-8")


remove_all(
    "open-model-market/v5_runtime.py",
    (
        "    quality_tier: str\n",
        "        if self.quality_tier not in {\"budget\", \"value\", \"quality\"}:\n"
        "            raise ValueError(\"quality_tier must be budget, value, or quality\")\n",
    ),
)
replace_exact("open-model-market/v5_pipeline.py", '        quality_tier="value",\n')

for path in (
    "tests/test_v5_p0_governance.py",
    "tests/test_v5_output_contract_delivery.py",
    "tests/test_v5_full_load_stability.py",
    "tests/test_v5_explicit_task_delivery_contract.py",
    "tests/test_v5_constitutional_runtime.py",
):
    target = file(path)
    text = target.read_text(encoding="utf-8")
    before = text
    for indent in (8, 12, 16):
        text = text.replace(" " * indent + 'quality_tier="value",\n', "")
    if text == before:
        raise SystemExit(f"{path}: no quality_tier keyword argument removed")
    target.write_text(text, encoding="utf-8")

for path in (
    "tests/test_v5_fact_label_purity_normalization.py",
    "tests/test_v5_deterministic_answer_normalization.py",
    "tests/test_v5_reasoning_saturation_recovery.py",
):
    target = file(path)
    text = target.read_text(encoding="utf-8")
    updated = text.replace(', "value")', ")")
    if updated == text:
        raise SystemExit(f"{path}: no positional quality-tier argument removed")
    target.write_text(updated, encoding="utf-8")

workflow_path = ".github/workflows/v5-one-time-paid-acceptance.yml"
workflow = file(workflow_path).read_text(encoding="utf-8")
workflow_replacements = (
    ('              "quality_tier",\n', ""),
    (
        '            .schema_version == "v5-paid-acceptance-2" and\n',
        '            .schema_version == "v5-paid-acceptance-3" and\n',
    ),
    (
        '            (.quality_tier == "budget" or .quality_tier == "value" or .quality_tier == "quality") and\n',
        "",
    ),
    ('            echo "quality_tier=$(jq -er \'.quality_tier\' "$request")"\n', ""),
    ('          QUALITY_TIER: ${{ steps.request.outputs.quality_tier }}\n', ""),
    ('            --quality-tier "$QUALITY_TIER" \\\n', ""),
)
for old, new in workflow_replacements:
    if old not in workflow:
        raise SystemExit(f"{workflow_path}: missing workflow anchor {old!r}")
    workflow = workflow.replace(old, new, 1)
file(workflow_path).write_text(workflow, encoding="utf-8")

replace_exact(
    "tests/test_workflow_contract.py",
    'schema_version == "v5-paid-acceptance-2"',
    'schema_version == "v5-paid-acceptance-3"',
)
replace_exact(
    "tests/test_workflow_contract.py",
    '        self.assertIn("quality_tier", paid)\n',
    '        self.assertNotIn("quality_tier", paid)\n',
)

cleanup_path = "tests/test_v5_complete_cleanup_regression.py"
cleanup = file(cleanup_path).read_text(encoding="utf-8")
cleanup_replacements = (
    (
        "    def test_quality_tier_is_not_an_external_contract(self) -> None:\n",
        "    def test_quality_tier_is_removed_from_all_live_contracts(self) -> None:\n",
    ),
    (
        '            ROOT / ".github/workflows/execution-ticket.yml",\n',
        '            ROOT / ".github/workflows/execution-ticket.yml",\n'
        '            ROOT / ".github/workflows/v5-one-time-paid-acceptance.yml",\n'
        '            MODULE / "v5_runtime.py",\n'
        '            MODULE / "expert-team-capabilities.json",\n',
    ),
    (
        '        self.assertNotIn(\'parser.add_argument("--quality-tier"\', pipeline)\n',
        '        self.assertNotIn(\'parser.add_argument("--quality-tier"\', pipeline)\n'
        '        self.assertNotIn("quality_tier", pipeline)\n',
    ),
    (
        '        self.assertNotIn("pull_request", paid)\n',
        '        self.assertNotIn("pull_request", paid)\n'
        '        self.assertFalse((ROOT / ".github/v5-paid-acceptance-request.json").exists())\n'
        '        self.assertFalse((ROOT / ".github/v5-paid-acceptance-attestation.json").exists())\n',
    ),
)
for old, new in cleanup_replacements:
    if old not in cleanup:
        raise SystemExit(f"{cleanup_path}: missing regression anchor {old!r}")
    cleanup = cleanup.replace(old, new, 1)
file(cleanup_path).write_text(cleanup, encoding="utf-8")

capabilities = {
    "schema_version": "expert-team-capabilities-v2",
    "catalog_status": "pre-production-candidate",
    "selection_owner": "gpt-latest-governance-model",
    "execution_owner": "github-expert-team-control-plane",
    "maintenance_owner": "ordinary-web-gpt-with-github-plugin",
    "gpts_exposure": {
        "capabilities_exposed": True,
        "discovery_action": "getExpertTeamCatalog",
        "ticket_schema_action": "getExpertTicketSchema",
        "delegation_contract_action": "getExpertDelegationContract",
        "model_ids_selectable_by_gpts": False,
        "providers_selectable_by_gpts": False,
        "internal_prompts_exposed": False,
        "secret_values_exposed": False,
    },
    "use_when": [
        "the task requires multidisciplinary judgment rather than only deterministic calculation",
        "evidence, assumptions, trade-offs, strategy, policy, business, risk, or red-team analysis must be synthesized",
        "the user explicitly requests the GitHub expert team",
        "API or compute results require independent interpretation and synthesis",
    ],
    "do_not_use_when": [
        "a fixed API connector can directly return the requested public data",
        "a deterministic compute operation can fully answer the question",
        "the user requests an immediate factual lookup without expert synthesis",
        "the ticket would contain secrets, private personal data, or unverified artifact identifiers without the underlying body",
    ],
    "governance_chain": {
        "gpt_proposal_calls": 1,
        "claude_red_team_calls": 1,
        "gpt_synthesis_calls": 1,
        "claude_role": "advisory-only structured modification advice",
        "claude_gatekeeping_allowed": False,
        "second_claude_review_allowed": False,
        "model_loop_allowed": False,
        "deterministic_validator_is_only_hard_gate": True,
    },
    "dynamic_execution": {
        "expert_count": "chosen by GPT latest within the approved call ceiling",
        "roles": "task-derived by GPT latest",
        "work_items": "task-derived by GPT latest",
        "topology": "task-derived DAG proposed by GPT latest",
        "model_and_provider": "exact endpoint pairs selected by GPT latest from the frozen catalog snapshot",
        "recovery_order": "proposed by GPT latest and bounded by the approved recovery reserve",
        "local_task_classification": False,
        "local_model_scoring": False,
        "local_optimizer": False,
        "cross_task_history": False,
    },
    "ticket_inputs": [
        "task_id",
        "objective",
        "pipeline",
        "task.question",
        "task.requirements",
        "task.language",
        "execution_acceptance",
        "evidence",
        "approved_budget.calls",
        "approved_budget.maximum_recovery_calls",
        "approved_budget.cost_policy",
        "approved_budget.cost_anomaly_usd",
        "private_output=false",
    ],
    "outputs": [
        "GPT proposal, Claude advice, and GPT synthesis governance receipts",
        "validated dynamic expert execution graph or a structured failure",
        "expert attempts and final task delivery",
        "call ledger with actual models, providers, tokens, cost, and errors",
        "execution diagnosis and deterministic constitutional audit",
        "report SHA-256, artifact manifest, and authoritative final status",
    ],
    "completion_rule": (
        "Only EXECUTION_COMPLETED with a complete final report, PASS audit, primary Artifact, "
        "final attestation Artifact, and verified delivery evidence is a normal PASS. queued, "
        "in_progress, accepted, workflow success, skipped jobs, or artifact metadata alone are not completion."
    ),
    "restrictions": [
        "GPTs must not choose a concrete model ID or provider",
        "Claude provides advice only and cannot approve, reject, gate, or execute",
        "experts cannot browse, call tools, plugins, APIs, file search, or code execution",
        "OpenAI and Anthropic governance companies cannot be assigned as expert companies",
        "expert companies must be globally unique across initial and recovery nodes",
        "no unlimited retries, replacements, model loops, or alternate runtime",
        "no secret or private data in public issues",
        "no self-answer by Web GPT before the GitHub report when delegation is explicit",
    ],
    "upstream_guidance": {
        "api_first": "Use the API center first when current structured public data is required.",
        "compute_first": "Use the compute center first when quantitative simulation or deterministic analysis is required.",
        "evidence_transfer": "GPTs must retrieve and verify complete upstream bodies and SHA values before creating the expert ticket.",
    },
}
file("open-model-market/expert-team-capabilities.json").write_text(
    json.dumps(capabilities, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)

for stale in (
    ".github/v5-paid-acceptance-request.json",
    ".github/v5-paid-acceptance-attestation.json",
):
    file(stale).unlink(missing_ok=True)

print("PR227 deterministic contract remediation applied")
