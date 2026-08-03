# Expert Center Soft-Governance Granular Audit — 2026-08-04

## Scope

Baseline: `main@f2731f2c29ae9298b33306d550ca90d7abd721f3`.

The audit covered the machine-readable constitution, ticket admission, GPT proposal and synthesis, Claude red-team review, proposal materialization, expert request construction, recovery, runtime scheduling, cost accounting, evidence normalization, native audit, independent artifact recomputation, GitHub Actions, and repository residue.

## Findings before remediation

1. The base runtime still contained reachable cost-denial and result-invalidation branches. The production wrapper bypassed most of them, but the shared runtime remained unsafe for direct or future reuse.
2. Setting a cost advisory forced expert-stage concurrency to one worker, so cost still changed execution behavior.
3. The independent artifact recomputation and native evidence audit could fail an otherwise valid result solely because actual cost crossed the configured threshold.
4. Expert upstream answers were locally clipped to 6,000 characters per node and 24,000 characters in total.
5. Claude requests still sent a local `max_tokens=512`, truncated long tasks, and rejected total input/output by local character ceilings.
6. GPT governance rejected the complete catalog, request, or proposal on local aggregate character ceilings.
7. The proposal materializer still generated provider output-token fields before a later softening layer removed them.
8. Ticket/schema language described the cost value as a stop guard, and status artifacts exposed the legacy `max_cost_usd` alias.
9. A date-bound one-time paid-acceptance workflow remained in the repository with fixed Token and dollar hard gates after its diagnostic purpose had expired.
10. Claude prompt integrity was self-referential: the expected digest was recomputed from the same prompt at import time, so it could not detect an unintended prompt edit.
11. `v5_constitutional_runtime.py` exposed an unused alternative runtime factory, duplicating the production construction path.

## Remediation

- Converted the base `BudgetController` to advisory-only cost accounting. Cost exceedance is recorded once and never denies reservation, stops execution, changes quality status, or invalidates output.
- Removed cost-dependent serialization. Worker count now depends only on configured concurrency and stage size.
- Converted native audit, normalized evidence, and independent recomputation from cost gates to consistency checks plus advisory telemetry.
- Removed local clipping of expert upstream answers.
- Removed Claude `max_tokens`, local aggregate input/output ceilings, and task truncation. Structural JSON bounds and provider-native compatibility remain.
- Removed GPT aggregate catalog/input/output character rejection while retaining structural graph bounds, identifiers, call limits, and provider locks.
- Stopped proposal materialization from emitting `max_tokens` or `max_completion_tokens` fields.
- Canonicalized ticket status to `prompt_led_soft_governance`; the former policy string remains accepted only as a migration alias.
- Removed the `max_cost_usd` status/output alias and rewrote schema/capability descriptions as advisory-only.
- Removed the obsolete date-bound paid-acceptance workflow and its stale hard-resource contract.
- Pinned the Claude prompt digest to a literal SHA-256 value.
- Removed the unused alternative constitutional runtime factory.
- Added end-to-end anti-regression tests for base runtime cost behavior, concurrency independence, full upstream preservation, Claude/GPT no-ceiling behavior, independent recomputation, and workflow residue.

## Boundaries intentionally retained

The following remain hard because they are structural or safety controls rather than Token/cost budgets: total model-call ceiling, finite recovery count, single Claude red-team pass, provider lock, no fallback, no external tools, timeout, ticket isolation, model-company uniqueness, schema validity, required delivery fields, and provider-native context/output capacity.

## Local verification

- Python compilation: PASS.
- Unit/integration tests excluding the Hypothesis-only property module unavailable in the local runner: `363 passed, 100 subtests passed`.
- Dedicated soft-governance regression module: `7 passed`.
- Repository line audit: 147 files, 32,161 lines; Critical/High/Medium/Low/Info all zero; orphan candidates zero.
- Model calls: 0.
- Model cost: USD 0.

The pull-request CI must install the pinned development dependencies and run the complete property suite before merge.
