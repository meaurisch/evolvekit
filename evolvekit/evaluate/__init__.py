"""Staged evaluation: the product, per the redesign proposal's first principle."""

from evolvekit.evaluate.cascade import Cascade, archive_threshold, select_promoted
from evolvekit.evaluate.scoring import compute_score, penalty_terms, scale_value
from evolvekit.evaluate.stages import (
    build_argv,
    run_command_stage,
    run_static_stage,
    static_checks,
)
from evolvekit.evaluate.types import EvalResult, StageOutcome

__all__ = [
    "Cascade",
    "EvalResult",
    "StageOutcome",
    "archive_threshold",
    "build_argv",
    "compute_score",
    "penalty_terms",
    "run_command_stage",
    "run_static_stage",
    "scale_value",
    "select_promoted",
    "static_checks",
]
