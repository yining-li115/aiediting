"""Reference video -> ReferenceAnalysis.

Shot-first, per section 0.5: a deterministic detector finds the cuts and the
model is only asked what is in each shot. Timing is exactly computable, so
asking a model for it would trade accuracy and reproducibility for nothing -
and the sampling rates typical of video understanding lose fast cuts, which is
precisely what a vlog reference is full of.

    reference video
        -> shot boundaries        (PySceneDetect, deterministic)
        -> transition types       (frame-difference profile, deterministic)
        -> per-shot semantics     (the one model call)
        -> music relationship     (librosa; the audio is deleted afterwards)
        -> aggregate statistics   (arithmetic)
        -> ReferenceAnalysis

The uploaded video is kept - the user gave it to us. Its frames, its audio and
its caption text never reach a rendered output; only the grammar does.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from ..db.models import Reference as ReferenceRow
from ..db.session import session_scope
from ..material import keyframes as kf
from ..material import probe, split
from ..providers import Role, get_provider
from ..specs.base import new_id
from ..specs.reference import ReferenceAnalysis, ReferenceShot, ReferenceText
from ..specs.segment import RawSegment
from ..storage import KEYFRAME, REFERENCE, get_storage, project_key, safe_name
from . import aggregate, audio, transitions

# TODO(review): concurrency for per-shot analysis. A reference is one video
# with tens of shots, so this is bounded work, but a rate-limited model will
# cap it lower anyway (see material/analyze.py).
DEFAULT_CONCURRENCY = 4


def add_reference(
    path: Path, project_id: str, reference_id: str | None = None
) -> tuple[str, str]:
    """Store an uploaded reference video. Returns (reference_id, storage_key).

    Only accepts a file. There is deliberately no URL ingestion: a product
    with a "paste a link" box has built a scraping channel into itself, which
    contradicts the data-sourcing position in section 16.1.
    """
    if not path.exists():
        raise FileNotFoundError(path)

    reference_id = reference_id or new_id("ref")
    storage = get_storage()
    key = project_key(
        project_id, REFERENCE, safe_name(path.name, reference_id)
    )
    storage.put(path, key)

    with session_scope() as session:
        if session.get(ReferenceRow, reference_id) is None:
            session.add(
                ReferenceRow(
                    id=reference_id, project_id=project_id, storage_key=key
                )
            )
    return reference_id, key


async def analyze_reference(
    reference_id: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    on_shot=None,
) -> ReferenceAnalysis:
    storage = get_storage()

    with session_scope() as session:
        row = session.get(ReferenceRow, reference_id)
        if row is None:
            raise LookupError(f"no reference {reference_id}")
        key, project_id = row.storage_key, row.project_id

    local = storage.open_local(key)
    meta = probe.probe_asset(local, project_id or "", reference_id, storage_key=key)

    # --- deterministic: where the cuts are -------------------------------
    spans = split.detect_boundaries(local)
    fps = meta.fps or 30.0

    shots: list[ReferenceShot] = []
    for index, (start, end) in enumerate(spans):
        transition, _ = (
            transitions.classify(str(local), end, fps)
            if index < len(spans) - 1
            else ("hard_cut", 0.0)
        )
        shots.append(
            ReferenceShot(
                index=index,
                start=round(start, 3),
                end=round(end, 3),
                transition_out=transition,
            )
        )

    # --- the one model call: what is in each shot -------------------------
    frames = _extract_frames(str(local), shots, project_id or "", reference_id)
    failures = await _label(
        shots, frames, project_id, reference_id, concurrency, on_shot
    )

    # Frames existed only to ask the model a question. Keeping reference
    # pixels around invites reusing them, which is the line section 16.1 draws.
    _discard_frames(frames)

    # --- music relationship, then the audio is gone -----------------------
    cut_points = [s.end for s in shots[:-1]]
    music = audio.analyse(local, cut_points)

    # --- arithmetic --------------------------------------------------------
    durations = [s.duration for s in shots]
    stats = aggregate.duration_stats(durations)
    density = aggregate.transition_density(shots)

    analysis = ReferenceAnalysis(
        reference_id=reference_id,
        project_id=project_id,
        duration=meta.duration,
        shot_count=len(shots),
        shots=shots,
        shot_duration=stats,
        pace=aggregate.pace_of(stats.median),
        transition_density=density,
        music=music,
        # TODO(review): caption detection needs OCR, which is not wired up.
        # Reported as unknown rather than "none" so a remake does not silently
        # drop a style layer we simply did not look at.
        text=ReferenceText(detected=False, subtitle_density="unknown"),
        shot_scale_sequence=[s.shot_type for s in shots],
        semantic_structure=[s.semantic_role for s in shots],
        style_tags=aggregate.style_tags(shots, stats.median, density),
    )
    analysis.provenance.extra = {
        "unlabelled_shots": sum(
            1 for s in shots if s.semantic_role == "unknown"
        ),
        "failures": [f"shot {shot.index}: {reason}" for shot, reason in failures],
    }

    with session_scope() as session:
        row = session.get(ReferenceRow, reference_id)
        if row is not None:
            row.duration = analysis.duration
            row.analysis_json = analysis.model_dump(mode="json")

    return analysis


def _extract_frames(
    video: str, shots: list[ReferenceShot], project_id: str, reference_id: str
) -> dict[int, list[Path]]:
    storage = get_storage()
    out: dict[int, list[Path]] = {}
    for shot in shots:
        keys = [
            project_key(
                project_id, KEYFRAME, f"{reference_id}_s{shot.index:03d}_{i}.jpg"
            )
            for i in range(len(kf.FRACTIONS))
        ]
        paths = kf.extract(
            video, shot.start, shot.end, [storage.reserve_local(k) for k in keys]
        )
        out[shot.index] = [p for p in paths if p.exists()]
    return out


def _discard_frames(frames: dict[int, list[Path]]) -> None:
    for paths in frames.values():
        for path in paths:
            path.unlink(missing_ok=True)


async def _label(
    shots: list[ReferenceShot],
    frames: dict[int, list[Path]],
    project_id: str | None,
    reference_id: str,
    concurrency: int,
    on_shot,
) -> list[tuple[ReferenceShot, str]]:
    provider = get_provider(Role.REFERENCE, project_id=project_id)
    semaphore = asyncio.Semaphore(max(1, concurrency))

    card = getattr(provider, "card", None)
    rpm = getattr(card, "requests_per_minute", None) if card else None
    if rpm:
        semaphore = asyncio.Semaphore(max(1, min(concurrency, rpm)))

    async def one(shot: ReferenceShot) -> None:
        paths = frames.get(shot.index, [])
        raw = RawSegment(
            segment_id=f"{reference_id}_s{shot.index:03d}",
            project_id=project_id or "",
            asset_id=reference_id,
            start=shot.start,
            end=shot.end,
        )
        async with semaphore:
            analysed = await provider.analyze_segment(raw, paths)
        shot.shot_type = analysed.shot_type
        shot.semantic_role = analysed.semantic_role
        shot.motion = analysed.camera_motion
        shot.semantic = analysed.semantic
        if analysed.interestingness is not None:
            shot.energy = analysed.interestingness
        if on_shot:
            on_shot(shot)

    # One failed shot must not lose the whole analysis: a reference with a few
    # unlabelled shots is still usable, an aborted analysis is not. But the
    # failures have to surface - an analysis where every shot is "unknown"
    # looks like a working analysis of a featureless video.
    outcomes = await asyncio.gather(
        *(one(shot) for shot in shots), return_exceptions=True
    )
    return [
        (shots[i], f"{type(o).__name__}: {o}")
        for i, o in enumerate(outcomes)
        if isinstance(o, BaseException)
    ]


def load_analysis(reference_id: str) -> ReferenceAnalysis | None:
    with session_scope() as session:
        row = session.get(ReferenceRow, reference_id)
        if row is None or not row.analysis_json:
            return None
        return ReferenceAnalysis.model_validate(row.analysis_json)


def list_references(project_id: str) -> list[tuple[str, str, bool]]:
    with session_scope() as session:
        rows = session.query(ReferenceRow).filter_by(project_id=project_id).all()
        return [(r.id, r.storage_key, bool(r.analysis_json)) for r in rows]
