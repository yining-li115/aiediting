"""Reference video understanding.

Shared by both reference modes:

    B1 (style)     N references -> aggregate grammar -> a planning prior
    B2 (template)  1 reference  -> slot sequence     -> a template

Both start here, with one ReferenceAnalysis per video.
"""

from .aggregate import bigrams, distribution, duration_stats, pace_of
from .analyze import (
    add_reference,
    analyze_reference,
    list_references,
    load_analysis,
)

__all__ = [
    "add_reference",
    "analyze_reference",
    "load_analysis",
    "list_references",
    "duration_stats",
    "pace_of",
    "bigrams",
    "distribution",
]
