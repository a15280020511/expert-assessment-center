# Expert Team Delegation Contract

## Authority boundary

GPTs may submit, monitor, retrieve and faithfully relay an expert-team task. GPTs do not choose concrete models, providers, roles or execution edges.

The active production selector is deterministic Python code:

```text
eligible exact endpoints
→ estimated task cost ascending
→ globally distinct model companies
→ 3–6 experts, default 4
→ NetworkX DAG validation
```

Claude and GPT governance-selection calls are disabled and must remain exactly zero in admission, execution, audit and Artifact evidence.

## Team organization

- `N-2` independent analysis nodes run first and may execute in parallel.
- One cross-review node consumes all independent outputs.
- One final-synthesis node consumes the original task, all independent outputs and the cross-review.
- The best official intelligence rank inside the selected lowest-cost set receives final synthesis; the second-best receives cross-review.
- Recovery candidates are preselected within the approved reserve and remain globally company-distinct.

## Input contract

The ticket contains the task, optional requirements and language, optional verified evidence, and an approved call/recovery envelope. Concrete model IDs, providers, tools and hidden prompts are not user-selectable ticket fields.

## Evidence boundary

Experts may use only the ticket, supplied evidence and legal prior outputs from the same task. Experts cannot browse, use tools, call plugins or APIs, execute code, query databases or directly contact other centers.

## Completion

An accepted ticket, queued workflow, successful job or uploaded Artifact is not completion. Completion requires an authoritative final status backed by:

- exact provider-locked request audit;
- complete call and cost ledger;
- price-ranked selection receipt;
- final report and quality integrity evidence;
- frozen Artifact manifest;
- independent Artifact revalidation;
- final attestation bound to the same SHA, Run and Artifact digest.
