"""Material understanding: user footage into a candidate segment pool.

Deterministic work (splitting, quality, keyframes, usability) is separated
from the single model call (semantics), so most of this module can be built,
evaluated and run before the model stack is frozen.
"""

from .analyze import AnalysisResult, IngestResult, analyze_project, ingest, load_segments
from .quality import QualityMetrics, measure
from .split import detect_boundaries, split_asset
from .usability import judge

__all__ = [
    "ingest",
    "analyze_project",
    "load_segments",
    "IngestResult",
    "AnalysisResult",
    "split_asset",
    "detect_boundaries",
    "measure",
    "QualityMetrics",
    "judge",
]
