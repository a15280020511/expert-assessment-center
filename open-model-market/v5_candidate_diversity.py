"""Compatibility entrypoint for V5 company-preserving candidate pruning."""
from __future__ import annotations

from typing import Sequence

from v5_company_diversity import company_preserving_pareto_prune
from v5_planner import CandidateNode

_INSTALLED = False


def diversity_preserving_pareto_prune(
    candidates: Sequence[CandidateNode],
    maximum_per_group: int = 24,
) -> list[CandidateNode]:
    """Keep distinct-company alternatives before model/Pareto supplements."""
    return company_preserving_pareto_prune(
        candidates,
        maximum_per_group=maximum_per_group,
    )


def install() -> None:
    """Deprecated compatibility no-op; use the function explicitly."""
    return None
