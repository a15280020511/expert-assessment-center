"""Compatibility entrypoint for V5 company-preserving candidate pruning."""
from __future__ import annotations

from typing import Sequence

from v5_company_diversity import company_preserving_pareto_prune
from v5_model_company import MINIMUM_CANDIDATES_PER_WORK
from v5_planner import CandidateNode


def diversity_preserving_pareto_prune(
    candidates: Sequence[CandidateNode],
    maximum_per_group: int = MINIMUM_CANDIDATES_PER_WORK,
) -> list[CandidateNode]:
    """Keep distinct-company alternatives before model/Pareto supplements."""
    return company_preserving_pareto_prune(
        candidates,
        maximum_per_group=maximum_per_group,
    )
