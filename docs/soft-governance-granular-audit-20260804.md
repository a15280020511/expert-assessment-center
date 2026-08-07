# Expert Center Soft-Governance Granular Audit — 2026-08-04

## Historical scope

This document records an earlier audit of the V5 runtime. Some conclusions from that date were later superseded by the task-dynamic, gate-free expert architecture.

## Historical findings

The earlier runtime still contained cost-dependent rejection, local output clipping, fixed governance limits, exact Provider routing, company-uniqueness checks and other qualification logic. Those controls made operational telemetry capable of changing whether an otherwise executable expert task could run.

## Current superseding policy

The current architecture treats cost, Canary, model popularity, company identity, Provider metadata and Artifact revalidation as telemetry or diagnostics rather than expert qualification gates.

Current production rules are:

- expert count, roles, models, collaboration topology and recovery are task-dynamic;
- no fixed 4+4 or company-uniqueness requirement;
- no Top20/Top50-only, flagship or price qualification gate;
- no free-first or free-Canary prerequisite;
- no production/main SHA admission lock;
- no exact Provider `only`/`order`, ZDR or `require_parameters` routing gate;
- OpenRouter chooses the upstream Provider dynamically;
- OR-Tools may return a feasible solution or fall back to a bounded heuristic; global optimality is not an execution prerequisite;
- existing expert output may be published without waiting for an independent Artifact approval gate.

## Safety boundaries intentionally retained

Structural and security controls remain: authenticated repository control, Secret protection, repository isolation, no arbitrary expert tools, valid parseable task structures, finite acyclic execution graphs and bounded retry behavior needed to prevent infinite execution.

These controls do not restrict expert company, model family, Provider, price class, popularity rank or fixed team size.

## Verification policy

Compilation, unit tests, repository audit and zero-cost checks are used to detect defects. They do not grant or revoke expert-model eligibility. Historical fixed-gate test assertions must be updated whenever they conflict with the current dynamic contract.
