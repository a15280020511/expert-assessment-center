"""Task-scoped runtime bindings for the history-free CP-SAT selector.

The legacy dynamic runtime installs process-wide wrappers. This module narrows
quorum enforcement to the RunConfig instance that owns the optimizer plan and
replaces candidate evidence ordering with current-task, history-free evidence.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

_BASE_RECOVER = None


def _expert_team_modules() -> list[Any]:
    modules = []
    for module in tuple(sys.modules.values()):
        path = str(getattr(module, "__file__", "") or "").replace("\\", "/")
        if path.endswith("/open-model-market/expert_team.py") or path.endswith("/expert_team.py"):
            if hasattr(module, "_recover_substantial_partials"):
                modules.append(module)
    return modules


def _unwrap_recover(function: Any) -> Any:
    stored = getattr(function, "_task_matrix_original_recover", None)
    if callable(stored):
        return stored
    for cell in getattr(function, "__closure__", ()) or ():
        try:
            candidate = cell.cell_contents
        except ValueError:
            continue
        if callable(candidate) and getattr(candidate, "__name__", "") == "_recover_substantial_partials":
            return _unwrap_recover(candidate)
    return function


def _bind_scoped_recovery(run: Any, expected: int) -> None:
    global _BASE_RECOVER
    object.__setattr__(run, "_task_matrix_expected_experts", int(expected))
    for module in _expert_team_modules():
        current = module._recover_substantial_partials
        if _BASE_RECOVER is None:
            _BASE_RECOVER = _unwrap_recover(current)
        base = _BASE_RECOVER

        def scoped_recover(active_run: Any, results: Sequence[Any], *, _base=base, _module=module):
            recovered = _base(active_run, results)
            required = getattr(active_run, "_task_matrix_expected_experts", None)
            if required is None:
                return recovered
            usable_statuses = getattr(_module, "USABLE_EXPERT_STATUSES", {"success_complete", "success_partial"})
            usable = [item for item in recovered if getattr(item, "status", "") in usable_statuses]
            if len(usable) != int(required):
                raise _module.ExpertTeamError(
                    f"Dynamic {required}+1 execution requires {required}/{required} usable expert answers; "
                    f"received {len(usable)}/{required}."
                )
            return recovered

        scoped_recover._dynamic_team_bound = True
        scoped_recover._task_matrix_original_recover = base
        module._recover_substantial_partials = scoped_recover


def _candidate_rows(
    ranked: Sequence[Any],
    profile: Any,
    run: Any,
    limit: int = 3,
) -> dict[str, list[dict[str, Any]]]:
    import seat_scoring as scoring
    import task_matrix_optimizer as optimizer

    path = Path(run.output_dir) / "team-optimization.json"
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}
    selected = plan.get("selected") if isinstance(plan, Mapping) else None
    if not isinstance(selected, Mapping):
        return {}
    pool = optimizer._eligible_pool(ranked, profile)
    configured = int(getattr(run, "candidate_pool_per_seat", limit) or limit)
    capped = max(1, min(3, int(limit), configured))
    evidence: dict[str, list[dict[str, Any]]] = {}
    for seat_key, selected_row in selected.items():
        if not isinstance(selected_row, Mapping):
            continue
        domain = str(selected_row.get("domain") or profile.primary_domain)
        chosen = str(selected_row.get("model") or "")
        kind = "adversarial" if seat_key == "red" else str(seat_key)
        rows = list(pool)
        if kind == "adversarial":
            maximum = max((scoring._term_fit(model, scoring.RISK_TERMS) for model in rows), default=0.0)
            risk_rows = [model for model in rows if maximum > 0 and scoring._term_fit(model, scoring.RISK_TERMS) == maximum]
            if risk_rows:
                rows = risk_rows
        rows.sort(
            key=lambda model: (
                -scoring._benchmark_score(model, domain),
                -scoring._domain_fit(model, domain),
                -optimizer._live_stability(model),
                model.blended_price_per_million if model.blended_price_per_million is not None else math.inf,
                model.id,
            )
        )
        chosen_model = next((model for model in pool if model.id == chosen), None)
        ordered = ([chosen_model] if chosen_model is not None else []) + [model for model in rows if model.id != chosen]
        evidence[str(seat_key)] = [
            scoring._candidate_row(model, index=index, domain=domain, selected=model.id == chosen)
            for index, model in enumerate(ordered[:capped], 1)
        ]
    return evidence


def bind(run: Any, profile: Any, ranked: Sequence[Any], experts: Sequence[Any], judge: Any) -> None:
    """Bind one optimizer result without leaking quorum state to unrelated runs."""
    del profile, ranked, judge
    _bind_scoped_recovery(run, len(experts))
    import direct_calls
    direct_calls.top_candidates_for_evidence = _candidate_rows
