"""Candidate-diversity layer for the bounded V5 low-cost pilot."""
from __future__ import annotations

from pathlib import Path

import v5_candidate_diversity
import v5_low_cost_pilot_v2


def run(config_path: str | Path, suite_path: str | Path, output_dir: str | Path) -> int:
    v5_candidate_diversity.install()
    return v5_low_cost_pilot_v2.run(config_path, suite_path, output_dir)
