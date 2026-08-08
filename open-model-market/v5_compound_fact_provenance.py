"""Allow task-authoritative compound fact lists without weakening provenance.

Live run #396 showed that models often compress several exact task facts into one
fact-labelled sentence separated by semicolons. The existing semantic matcher is
intentionally strict and can reject the whole compound even when every local clause
is independently task-authoritative. This layer accepts a compound only when the
original validator accepts *every* non-empty local clause. Swapped, invented, or
unsupported clauses therefore remain rejected.
"""
from __future__ import annotations

import re

import v5_deterministic_answer_normalization as deterministic_normalization
import v5_task_constraints as task_constraints

try:
    import v5_production_answer_normalization as production_normalization
except ImportError:  # pragma: no cover - optional compatibility surface
    production_normalization = None

_ORIGINAL_FACT_CLAIM_SUPPORTED = task_constraints.fact_claim_supported
_LOCAL_CLAUSE_RE = re.compile(r"[；;。]+")


def compound_fact_claim_supported(task: str, claim: str) -> bool:
    if _ORIGINAL_FACT_CLAIM_SUPPORTED(task, claim):
        return True
    clauses = [
        value.strip(" \t\n-—：:")
        for value in _LOCAL_CLAUSE_RE.split(str(claim or ""))
        if value.strip(" \t\n-—：:")
    ]
    if len(clauses) <= 1:
        return False
    return all(_ORIGINAL_FACT_CLAIM_SUPPORTED(task, clause) for clause in clauses)


def install_compound_fact_provenance() -> None:
    task_constraints.fact_claim_supported = compound_fact_claim_supported
    deterministic_normalization.fact_claim_supported = compound_fact_claim_supported
    if production_normalization is not None and hasattr(
        production_normalization, "fact_claim_supported"
    ):
        production_normalization.fact_claim_supported = compound_fact_claim_supported


__all__ = ["compound_fact_claim_supported", "install_compound_fact_provenance"]
