# Paid Test Pause — 2026-07-30

## Current decision

Paid validation is paused after the V5 sub-cent operational micro canary passed.

The repository must preserve these boundaries:

- V5 operational chain: verified.
- V5 quality superiority over V3: not proven.
- Production cutover: forbidden.
- V3 production entry: retained.
- V3 deletion: forbidden.
- Automatic paid retries: stopped.
- Additional paid tests: not authorized.

## Enforced guard

The scheduled fixed 3+1 live canary requires the repository variable:

```text
PAID_TESTS_ENABLED=true
```

When the variable is missing or has any other value, the scheduled or manually dispatched paid canary job is skipped before checkout, secret access, catalog access, or model inference.

This guard does not apply to the V3 production workflow.

## Future reactivation

Paid testing may only be reactivated for a specifically approved comparison, normally the bounded three-task anonymous V5-versus-V3 blind evaluation. Reactivation must be explicit, temporary, cost-capped, call-capped, non-retrying, and must not change the production entrypoint.
