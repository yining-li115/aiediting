"""Candidate ranking.

Stage 1 (structural) is rules only and runs on every candidate.
Stage 2 (video taste) needs a proxy render and only runs on the survivors -
see docs/TECHNICAL_PLAN.md section 9 for the cost reasoning.
"""

from .structural import StructuralScore, rank, score

__all__ = ["score", "rank", "StructuralScore"]
