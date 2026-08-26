"""Mode A end to end: footage in, MP4 out.

    material pool  ->  pool summary  ->  N EditPlanSpec
                                              |
                                     Material Matcher
                                              |
                                      N TimelineSpec
                                              |
                          Stage 1: structural critic (free, no render)
                                              |
                                         top-K survivors
                                              |
                        Stage 2: proxy render + video critic   <- block F
                                              |
                                       Stage 3: HQ render

Stages 2 and 3 are already split here even though the video critic is still a
passthrough. Doing the layering later would mean re-plumbing the cost path at
exactly the moment costs start mattering.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import settings
from .critic import StructuralScore
from .critic import rank as structural_rank
from .db.models import CandidateScore, EditPlanSpecRow, Render, Segment
from .db.models import TimelineSpecRow
from .db.session import session_scope
from .material import analyze as material_analyze
from .material.embedding import from_blob
from .matcher import build_candidates
from .planner import PlanningContext, generate_plans, parse_intent
from .providers import Role, get_provider
from .renderer import FFmpegRenderer
from .specs.base import new_id
from .specs.edit_plan import EditPlanSpec
from .specs.segment import MaterialSegment
from .specs.timeline import TimelineSpec
from .storage import RENDER, get_storage, project_key
from . import music as music_lib


@dataclass
class PipelineResult:
    project_id: str
    plans: list[EditPlanSpec]
    candidates: list[tuple[TimelineSpec, StructuralScore]]
    best: TimelineSpec | None = None
    render_path: Path | None = None
    render_key: str | None = None
    notes: list[str] = field(default_factory=list)


def load_pool(project_id: str) -> tuple[list[MaterialSegment], dict[str, np.ndarray]]:
    """Segments plus their vectors, straight from the database."""
    segments = material_analyze.load_segments(project_id)
    vectors: dict[str, np.ndarray] = {}
    with session_scope() as session:
        for row in session.query(Segment).filter_by(project_id=project_id).all():
            if row.embedding:
                vectors[row.id] = from_blob(row.embedding)
    return segments, vectors


def pool_summary(project_id: str, segments: list[MaterialSegment]) -> dict:
    return material_analyze.AnalysisResult(
        project_id=project_id, segments=segments
    ).summary()


async def generate(
    project_id: str,
    prompt: str | None = None,
    target_duration: float | None = None,
    candidates: int | None = None,
    top_k: int | None = None,
    quality: str = "hq",
    out_path: Path | None = None,
    seed: int | None = None,
    render: bool = True,
    keep_original_sound: bool = False,
) -> PipelineResult:
    count = candidates or settings.candidate_count
    keep = top_k or settings.top_k_proxy
    seed = seed if seed is not None else settings.seed

    segments, vectors = load_pool(project_id)
    usable = [s for s in segments if s.is_usable]
    if not usable:
        raise LookupError(
            f"project {project_id} has no usable segments; run analyze first"
        )

    summary = pool_summary(project_id, segments)
    intent = await parse_intent(
        prompt, get_provider(Role.PLANNER), target_duration=target_duration
    )

    # --- plan --------------------------------------------------------------
    context = PlanningContext(project_id, intent, summary)
    plans = generate_plans(context, count, seed)
    if keep_original_sound:
        # Ambient sound and speech carry a lot of a vlog's character, so this
        # is a real editorial choice rather than a debug switch.
        for plan in plans:
            plan.audio.keep_original_sound = True

    if plans[0].audio.use_music:
        track = music_lib.choose(
            intent.style,
            intent.target_duration,
            plans[0].rhythm.pace,
            project_id=project_id,
        )
        music = track.as_dict() if track else None
    else:
        music = None

    # --- match -------------------------------------------------------------
    built = build_candidates(plans, usable, vectors, music)
    if not built:
        raise ValueError("no candidate could be filled from this material pool")

    # --- stage 1: structural ranking, no rendering -------------------------
    by_id = {p.plan_id: p for p in plans}
    ranked = structural_rank(
        by_id,
        [timeline for timeline, _ in built],
        usable,
        vectors,
        beat_times=(music or {}).get("beat_times"),
    )
    survivors = ranked[:keep]

    _persist(plans, ranked)

    result = PipelineResult(
        project_id=project_id, plans=plans, candidates=ranked
    )
    result.notes.append(
        f"{len(plans)} plans -> {len(built)} feasible -> top {len(survivors)}"
    )
    if music is None:
        result.notes.append(
            "no music bed"
            + ("" if plans[0].audio.use_music else " (user asked for none)")
        )

    if not survivors:
        return result

    # --- stage 2 would render proxies and run the video critic here --------
    # The critic is a passthrough until block F, so the structural winner is
    # taken directly. The split stays visible so wiring the real critic in is
    # a local change.
    best, _ = survivors[0]
    result.best = best

    if render:
        sources = _sources(project_id, best)
        render_id = new_id("r")
        key = project_key(project_id, RENDER, f"{render_id}_{quality}.mp4")
        storage = get_storage()

        # Renders live in storage like everything else; an explicit --out is a
        # convenience copy for the user, not the system's copy of record.
        rendered = FFmpegRenderer().render(
            best, quality, storage.reserve_local(key), sources
        )
        storage.commit(key)
        result.render_key = key
        result.render_path = rendered.path
        if out_path is not None:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(rendered.path, out_path)
            result.render_path = out_path
        _record_render(best, rendered, quality, render_id, key)

    return result


def _sources(project_id: str, timeline: TimelineSpec) -> dict[str, Path]:
    from .db.models import Asset as AssetRow

    storage = get_storage()
    with session_scope() as session:
        rows = session.query(AssetRow).filter_by(project_id=project_id).all()
        # Render from the originals, never the analysis proxy: the proxy is
        # 540p and exists only so the CV passes see consistent input.
        # open_local is what makes this work unchanged when storage is remote.
        return {r.id: storage.open_local(r.storage_key) for r in rows}


def _persist(
    plans: list[EditPlanSpec], ranked: list[tuple[TimelineSpec, StructuralScore]]
) -> None:
    with session_scope() as session:
        for plan in plans:
            if session.get(EditPlanSpecRow, plan.plan_id) is None:
                session.add(
                    EditPlanSpecRow(
                        id=plan.plan_id,
                        project_id=plan.project_id,
                        mode=plan.mode,
                        candidate_index=plan.provenance.candidate_index,
                        spec_json=plan.model_dump(mode="json"),
                    )
                )
        for timeline, score in ranked:
            if session.get(TimelineSpecRow, timeline.timeline_id) is None:
                session.add(
                    TimelineSpecRow(
                        id=timeline.timeline_id,
                        project_id=timeline.project_id,
                        plan_id=timeline.plan_id,
                        duration=timeline.duration,
                        spec_json=timeline.model_dump(mode="json"),
                        is_feasible=score.overall > 0,
                    )
                )
            session.add(
                CandidateScore(
                    id=new_id("score"),
                    timeline_id=timeline.timeline_id,
                    stage="structural",
                    scorer="rules-v1",
                    overall=score.overall,
                    dimensions_json=score.as_dict(),
                )
            )


def _record_render(
    timeline: TimelineSpec, rendered, quality: str, render_id: str, key: str
) -> None:
    with session_scope() as session:
        session.add(
            Render(
                id=render_id,
                timeline_id=timeline.timeline_id,
                quality=quality,
                storage_key=key,
                duration_sec=rendered.duration_sec,
                render_time_ms=rendered.render_time_ms,
                file_size=rendered.file_size,
                status="succeeded",
            )
        )


def run_sync(**kwargs) -> PipelineResult:
    return asyncio.run(generate(**kwargs))
