"""Material matcher: binds a plan to real footage and adapts the fit.

This module makes editorial decisions. The renderer's compiler does not - see
docs/TECHNICAL_PLAN.md section 2 for why that line matters.
"""

from .adapt import fit, redistribute
from .assign import Assignment, assign
from .build import build_candidates, build_timeline
from .score import Score, Weights, score_pair

__all__ = [
    "assign",
    "Assignment",
    "build_timeline",
    "build_candidates",
    "fit",
    "redistribute",
    "score_pair",
    "Score",
    "Weights",
]
