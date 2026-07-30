# V5 Production Readiness R7

## Current decision

V5 remains a candidate implementation. V3 remains the production entrypoint until every cutover gate below passes.

## R6 defects addressed

| R6 defect | R7 control |
|---|---|
| One failed node invalidated the whole graph | Later stages continue; the final node may synthesize from available upstream outputs |
| No final answer after partial success | A quality-gated, explicitly labelled degraded delivery is allowed only at >=50% required-work coverage |
| Every node inherited strict JSON | Strict machine-readable output is deferred to synthesis/delivery nodes |
| 10000-token allowance became the effective request size | Each node receives a dynamic lower maximum; 10000 remains only the absolute allowance |
| Upstream outputs inflated final context and cost | Upstream content is quality-ordered and bounded per node and per graph stage |
| Estimated cost did not stop an expensive strategy | Candidate costs are converted to conservative P95 reservations before CP-SAT and checked again before execution |
| Actual cost could cross the strategy ceiling under concurrency | Budgeted execution is serialized and reconciled after every call |
| Formal entrypoints retained early aliases to the legacy executor | CLI and benchmark aliases are rebound when the formal V5 safety unit is installed |

## Delivery semantics

- `success`: at least one final delivery node passes its quality contract.
- `degraded_success`: no final node passes, but quality-gated support outputs cover at least 50% of required work. The report must disclose incomplete coverage.
- `failed`: no usable delivery, invalid graph, or a hard cost ceiling is exceeded.

A degraded result is usable evidence but is not sufficient for automatic production cutover.

## Cost policy

1. Expected candidate cost is retained as `raw_expected_cost_usd`.
2. A reliability- and role-adjusted conservative reservation is used by CP-SAT.
3. The complete selected graph is checked against the hard strategy budget before the first model call.
4. Retry and replacement calls share the same graph-wide ledger.
5. The request uses a finite node-specific output maximum not exceeding 10000 tokens.

## Production cutover gates

V5 may replace V3 only after all of the following are true:

1. All zero-cost repository, integrity, V5, and expert-team CI workflows pass.
2. One V5-only micro Canary succeeds within its hard call and cost ceilings.
3. A three-task anonymous comparison achieves 100% V5 valid-delivery rate.
4. V5 wins at least two of three tasks and improves mean blind quality by at least 0.02.
5. V5 relative cost is no more than 25% above V3.
6. No tool-use, provider-routing, JSON-closure, truncation, or budget safety failure occurs.
7. Production entrypoint change is performed in a separate reversible PR.
8. V3 is retained through a rollback observation period and is not deleted in the cutover PR.

## Remaining work after this PR

- Run zero-cost CI and resolve all regressions.
- Run one controlled micro Canary only after CI passes.
- Run the three-task anonymous comparison only after the Canary passes.
- Add provider-history calibration from R6/R7 artifacts to the model endpoint ledger.
- Switch the production entrypoint only through a separate PR after the cutover gate returns true.
