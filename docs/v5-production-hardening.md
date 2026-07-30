# V5 Production Hardening

## Decision

V5 must not replace V3 merely because the planner can build a valid graph. Production eligibility requires a deliverable-answer gate, a conservative pre-call cost gate, provider fault isolation, and zero-cost regression coverage.

This hardening changes the runtime failure unit from **the whole graph** to **one logical work package**. A graph may return a degraded but auditable answer when enough required work is covered. It still fails closed when coverage, answer usability, safety, or budget preflight is insufficient.

## R6 defects addressed

1. **Whole-graph single-point failure**
   - Previous behavior stopped after the first failed stage and raised whenever any node failed.
   - New behavior continues independent work, measures required-work coverage, and accepts a degraded result only above the configured coverage floor.

2. **No required/optional distinction**
   - Node criticality is now resolved as `final`, `required`, or `optional`.
   - Explicit `metadata.node_criticality` overrides are supported; otherwise final/synthesis functions are final, review/red-team/supplement functions are optional, and remaining work is required.

3. **No partial-success synthesis**
   - Successful final nodes are preferred.
   - If final synthesis fails but enough work survives, the executor produces a deterministic, clearly labelled degraded synthesis from usable node outputs.

4. **Provider 429 and upstream instability**
   - Transient failures open a per-endpoint circuit breaker.
   - Recovery prefers a different model/provider before any same-endpoint retry.
   - Provider concentration is rebalanced during preflight when quality-compatible alternatives exist.

5. **JSON truncation fragility**
   - Balanced JSON objects are extracted from fenced or prefixed output.
   - Long or invalid JSON can be retained as degraded evidence instead of destroying the graph, but it is not treated as a full quality-gate pass.

6. **Optimistic cost prediction**
   - Every call reserves a risk-adjusted cost envelope before execution.
   - Outstanding reservations and actual spend are reconciled correctly.
   - Tight-budget stages run serially to prevent concurrent overspend.
   - Optional low-value nodes may be pruned before any paid call.
   - A graph whose conservative envelope still exceeds the hard budget is rejected with zero model calls.

7. **Unbounded prompt growth**
   - Upstream answers are clipped per node and globally before being passed downstream.
   - This prevents graph depth from multiplying prompt cost and latency.

## New runtime policy

`GraphLimits` now includes:

- `min_required_work_coverage=0.66`
- `min_successful_nodes=1`
- `max_node_failure_probability=0.18`
- `cost_risk_multiplier=4.0`
- `max_provider_share=0.50`
- `max_provider_failures=1`
- `allow_degraded_success=True`
- bounded upstream context controls

These are safety defaults, not optimizer objectives. A future calibrated performance ledger may lower the cost multiplier only with sufficient endpoint-level evidence.

## Result semantics

The public `status` remains `success` or `failed` for compatibility. Successful results add:

- `completion_class=full_success`
- or `completion_class=degraded_success`
- `degraded=true|false`
- `required_work_coverage`
- `covered_work` / `missing_work`
- `preflight` substitutions, pruning and cost envelope
- `provider_circuit` state

A degraded result is not silently represented as full success.

## Production cutover gate

V3 remains the production fallback until all conditions below pass:

1. All zero-cost CI and deterministic fault-injection tests pass.
2. V5 completes at least three independent real tasks with no safety failure.
3. No task has required-work coverage below the configured floor.
4. At most one of the three tasks is degraded, and its blind quality is not below the V3 answer for that task.
5. V5 wins at least two of three blind comparisons.
6. V5 mean blind quality is at least 2% above V3.
7. V5 actual mean cost is no more than 25% above V3 and no strategy exceeds its hard budget.
8. Provider/model diversity evidence is valid.
9. Only then may the production entry be switched. V3 deletion requires a separate rollback observation period.

## Tests added

The regression suite verifies:

- one optional node can fail without losing the final answer;
- all-node failure remains a hard failure;
- an unsafe cost envelope stops before any paid call;
- HTTP 429 moves to a different provider endpoint;
- truncated invalid JSON is salvaged only as degraded output.
