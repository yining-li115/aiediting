"""Material understanding pipeline: files in, candidate pool out.

    asset -> proxy -> shot split -> quality -> keyframes
                                        |
                                        +-> VLM semantics (the only model call)
                                        +-> embedding
                                        -> MaterialSegment -> DB

The split between deterministic and model-derived work is the point. Timing,
sharpness, exposure and shake are computed from pixels; only "what is this and
is it worth using" goes to a model. That keeps the expensive, non-reproducible
part as small as possible, and it means everything except semantics can be
built and evaluated before the model stack is frozen.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ..config import settings
from ..db.models import Asset as AssetRow
from ..db.models import Project, Segment
from ..db.session import session_scope
from ..events import publish
from ..providers import Role, get_provider
from ..specs.base import new_id
from ..storage import (
    KEYFRAME,
    ORIGINAL,
    PROXY,
    get_storage,
    project_key,
    safe_name,
    sha256_of,
)
from ..specs.segment import Asset, MaterialSegment, RawSegment
from . import keyframes as kf
from . import probe, quality, split, usability
from .embedding import segment_vector, to_blob

from ..specs.enums import PHOTO_SUFFIXES, VIDEO_SUFFIXES

MEDIA_SUFFIXES = VIDEO_SUFFIXES | PHOTO_SUFFIXES

# Bounded so a 100-clip project cannot open hundreds of model connections at
# once; raise it once real provider rate limits are known.
DEFAULT_CONCURRENCY = 8


@dataclass
class IngestResult:
    project_id: str
    assets: list[Asset]
    skipped: list[tuple[Path, str]]

    @property
    def photos(self) -> int:
        return sum(1 for a in self.assets if a.kind == "photo")

    @property
    def videos(self) -> int:
        return sum(1 for a in self.assets if a.kind == "video")


@dataclass
class AnalysisResult:
    project_id: str
    segments: list[MaterialSegment]
    # Segments whose semantic pass failed. They keep their deterministic
    # fields and stay in the pool as "unknown" rather than vanishing.
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def usable(self) -> list[MaterialSegment]:
        return [s for s in self.segments if s.is_usable]

    def summary(self) -> dict:
        """Aggregate pool description - what the planner is allowed to see.

        The planner reads this, never the full clip list: a summary keeps the
        prompt small and forces planning to be about structure rather than
        about picking specific files.
        """
        usable = self.usable
        by_role: dict[str, dict[str, float]] = {}
        for segment in usable:
            entry = by_role.setdefault(
                segment.semantic_role, {"count": 0, "total_duration": 0.0}
            )
            entry["count"] += 1
            entry["total_duration"] += segment.duration
        return {
            "project_id": self.project_id,
            "segment_count": len(self.segments),
            "usable_count": len(usable),
            "rejected_count": len(self.segments) - len(usable),
            "total_usable_duration": round(
                sum(s.duration for s in usable), 2
            ),
            "by_semantic_role": {
                role: {
                    "count": int(v["count"]),
                    "total_duration": round(v["total_duration"], 2),
                }
                for role, v in sorted(by_role.items())
            },
            "by_shot_type": {
                shot: sum(1 for s in usable if s.shot_type == shot)
                for shot in sorted({s.shot_type for s in usable})
            },
            "unlabelled_count": sum(
                1 for s in usable if s.semantic_role == "unknown"
            ),
            "reject_reasons": {
                reason: sum(
                    1
                    for s in self.segments
                    if s.reject_reason and s.reject_reason.split(":")[0] == reason
                )
                for reason in sorted(
                    {
                        s.reject_reason.split(":")[0]
                        for s in self.segments
                        if s.reject_reason
                    }
                )
            },
        }


# --- ingest ----------------------------------------------------------------


def ingest(
    material_dir: Path, project_id: str | None = None, prompt: str | None = None
) -> IngestResult:
    """Probe every video in a directory and register it, with a proxy copy."""
    project_id = project_id or new_id("proj")
    candidates = sorted(p for p in material_dir.iterdir() if p.is_file())
    files = [p for p in candidates if p.suffix.lower() in MEDIA_SUFFIXES]

    # Anything we do not recognise is reported, never dropped in silence.
    # A user exporting a camera roll sees "30 imported" and has no way to know
    # the other 15 files vanished; that is data loss wearing a success message.
    skipped: list[tuple[Path, str]] = [
        (p, f"unsupported file type {p.suffix.lower() or '(none)'}")
        for p in candidates
        if p.suffix.lower() not in MEDIA_SUFFIXES and not p.name.startswith(".")
    ]

    if not files:
        raise FileNotFoundError(
            f"no video or photo files under {material_dir}"
            + (f" ({len(skipped)} unsupported files found)" if skipped else "")
        )

    assets: list[Asset] = []
    storage = get_storage()

    for index, path in enumerate(files, start=1):
        try:
            # Asset ids must be unique across projects, not just within one:
            # a bare "asset_001" collides with every other project's first
            # clip, and the idempotent insert below would then silently keep
            # the older row - quietly cutting one project's video from another
            # project's footage.
            asset_id = f"{project_id}_a{index:03d}"
            key = project_key(project_id, ORIGINAL, safe_name(path.name, asset_id))

            # Copy into storage rather than recording where the user happened
            # to leave the file. A project must not break because someone
            # tidied their downloads folder.
            stored = storage.put(path, key)
            local = storage.open_local(key)

            asset = probe.probe_asset(local, project_id, asset_id, storage_key=key)
            asset.original_name = path.name
            asset.sha256 = stored.sha256
            asset.size_bytes = stored.size

            if asset.kind == "photo":
                proxy_key = project_key(project_id, PROXY, f"{asset_id}.jpg")
                probe.make_photo_proxy(
                    asset, local, storage.reserve_local(proxy_key)
                )
            else:
                proxy_key = project_key(project_id, PROXY, f"{asset_id}.mp4")
                probe.make_proxy(asset, local, storage.reserve_local(proxy_key))
            storage.commit(proxy_key)
            asset.proxy_key = proxy_key

            assets.append(asset)
        except probe.ProbeError as exc:
            # One unreadable file must not abort an 80-clip import.
            skipped.append((path, str(exc)))

    with session_scope() as session:
        if session.get(Project, project_id) is None:
            session.add(Project(id=project_id, prompt=prompt, status="ingested"))
        for asset in assets:
            if session.get(AssetRow, asset.asset_id) is None:
                session.add(
                    AssetRow(
                        id=asset.asset_id,
                        project_id=project_id,
                        storage_key=asset.storage_key,
                        proxy_key=asset.proxy_key,
                        kind=asset.kind,
                        original_name=asset.original_name,
                        sha256=asset.sha256,
                        size_bytes=asset.size_bytes,
                        duration=asset.duration,
                        width=asset.width,
                        height=asset.height,
                        fps=asset.fps,
                        has_audio=asset.has_audio,
                    )
                )

    return IngestResult(project_id=project_id, assets=assets, skipped=skipped)


# --- analyse ---------------------------------------------------------------


def _deterministic_pass(
    asset: Asset, raw: RawSegment, storage
) -> tuple[MaterialSegment, list[Path]]:
    """Everything that needs no model: quality, usability, keyframes."""
    source = str(storage.open_local(asset.proxy_key or asset.storage_key))
    if asset.kind == "photo":
        metrics = quality.measure_photo(source)
    else:
        metrics = quality.measure(source, raw.start, raw.end)
    is_usable, reason = usability.judge(raw.duration, metrics, asset.kind)

    segment = MaterialSegment(
        segment_id=raw.segment_id,
        project_id=raw.project_id,
        source_id=raw.asset_id,
        kind=asset.kind,
        start=raw.start,
        end=raw.end,
        is_usable=is_usable,
        reject_reason=reason,
        **metrics.as_dict(),
    )
    # Interestingness has no deterministic estimator; until a VLM scores it,
    # technical quality is an honest placeholder rather than a fabricated one.
    segment.interestingness = metrics.technical_quality

    # Rejected segments get no keyframes: no point paying for pixels, model
    # calls or disk on footage that will never enter the pool.
    paths: list[Path] = []
    if is_usable and asset.kind == "photo":
        # The photo is already the frame the model should see; extracting one
        # from it would just be a copy.
        segment.keyframe_keys = [asset.proxy_key or asset.storage_key]
        paths = [Path(source)]
        segment.camera_motion = "static"
    elif is_usable:
        keys = [
            project_key(
                raw.project_id, KEYFRAME, f"{raw.segment_id}_{i}.jpg"
            )
            for i in range(len(kf.FRACTIONS))
        ]
        paths = kf.extract(
            source, raw.start, raw.end, [storage.reserve_local(k) for k in keys]
        )
        segment.keyframe_keys = [
            key for key, path in zip(keys, paths) if path.exists()
        ]
        for key in segment.keyframe_keys:
            storage.commit(key)
    return segment, paths


async def _semantic_pass(
    provider,
    segment: MaterialSegment,
    raw: RawSegment,
    frames: list[Path],
    semaphore: asyncio.Semaphore,
    on_done=None,
) -> MaterialSegment:
    """The one model call: what is in this shot, and is it worth using.

    The provider is passed in rather than constructed here: a local model would
    otherwise be reloaded once per segment.
    """
    async with semaphore:
        analysed = await provider.analyze_segment(raw, frames)

    # Only the semantic fields are taken from the model. Keyframe keys,
    # timing and quality already belong to the deterministic pass, and letting
    # a provider overwrite them would make the pool depend on which model ran.
    for field in (
        "semantic",
        "semantic_role",
        "shot_type",
        "camera_motion",
        "people",
        "emotion",
    ):
        setattr(segment, field, getattr(analysed, field))
    if analysed.interestingness is not None:
        segment.interestingness = analysed.interestingness
    if analysed.composition_score is not None:
        segment.composition_score = analysed.composition_score
    if on_done:
        on_done(segment)
    return segment


async def _embed(
    project_id: str,
    segments: list[MaterialSegment],
    frames: dict[str, list[Path]],
) -> dict[str, np.ndarray]:
    # project_id is threaded through so embedding cost lands in api_calls under
    # the right project. It is free for the local model and will not be for a
    # cloud one, and per-project cost has to be right from the start.
    provider = get_provider(Role.EMBEDDING, project_id=project_id)
    vectors: dict[str, np.ndarray] = {}
    for segment in segments:
        paths = frames.get(segment.segment_id, [])
        if not paths:
            continue
        frame_vectors = await provider.embed_images(paths)
        vectors[segment.segment_id] = segment_vector(frame_vectors)
    return vectors


async def analyze_project(
    project_id: str,
    concurrency: int = DEFAULT_CONCURRENCY,
    progress=None,
    on_segment=None,
    limit: int | None = None,
    job_id: str | None = None,
) -> AnalysisResult:
    """Split, measure, label and embed every asset in a project.

    ``limit`` caps how many segments reach the semantic model. Splitting and
    the quality pass are free and always run on everything; only the paid,
    rate-limited step is sampled. Useful for judging a new model on a handful
    of segments before committing a whole project's quota to it.
    """
    with session_scope() as session:
        rows = session.query(AssetRow).filter_by(project_id=project_id).all()
        assets = [
            Asset(
                asset_id=r.id, project_id=r.project_id, kind=r.kind,
                storage_key=r.storage_key, proxy_key=r.proxy_key,
                original_name=r.original_name, sha256=r.sha256,
                size_bytes=r.size_bytes, duration=r.duration,
                width=r.width, height=r.height,
                fps=r.fps, has_audio=r.has_audio,
            )
            for r in rows
        ]
    if not assets:
        raise LookupError(f"project {project_id} has no assets; run ingest first")

    storage = get_storage()
    segments: list[MaterialSegment] = []
    raws: dict[str, RawSegment] = {}
    frames: dict[str, list[Path]] = {}

    for index, asset in enumerate(assets, start=1):
        for raw in split.split_asset(asset):
            segment, paths = _deterministic_pass(asset, raw, storage)
            segments.append(segment)
            raws[segment.segment_id] = raw
            frames[segment.segment_id] = paths
        if progress:
            progress(index / len(assets) * 0.6)

    usable = [s for s in segments if s.is_usable]
    material = get_provider(Role.MATERIAL, project_id=project_id)
    failures: list[tuple[str, str]] = []

    # A rate-limited model gains nothing from high concurrency: the limiter
    # would just make eight coroutines queue behind each other. Matching the
    # two keeps progress reporting honest about what is actually happening.
    card = getattr(material, "card", None)
    rpm = getattr(card, "requests_per_minute", None) if card else None
    if rpm:
        concurrency = max(1, min(concurrency, rpm))
    semaphore = asyncio.Semaphore(concurrency)
    # return_exceptions: analysing 80 clips must not be all-or-nothing. One
    # segment that exhausts its retries - a transient 503, a rejected frame -
    # should cost that segment's labels, not the whole project's work.
    if limit is not None:
        # Sample the best-looking segments: a sample meant to judge the model
        # should not consist of the footage the pool would rank last.
        usable = sorted(
            usable, key=lambda s: s.technical_quality or 0.0, reverse=True
        )[:limit]

    done = {"n": 0}

    def _tick(segment: MaterialSegment) -> None:
        done["n"] += 1
        if on_segment:
            on_segment(done["n"], len(usable), segment)
        # The same callback feeds the CLI's printout and the browser's live
        # list; neither knows about the other.
        publish(
            job_id,
            "segment",
            done=done["n"],
            total=len(usable),
            segment_id=segment.segment_id,
            shot_type=segment.shot_type,
            semantic_role=segment.semantic_role,
            semantic=segment.semantic,
            keyframe_key=(segment.keyframe_keys or [None])[0],
        )

    outcomes = await asyncio.gather(
        *(
            _semantic_pass(
                material,
                s,
                raws[s.segment_id],
                frames[s.segment_id],
                semaphore,
                on_done=_tick,
            )
            for s in usable
        ),
        return_exceptions=True,
    )
    for segment, outcome in zip(usable, outcomes):
        if isinstance(outcome, BaseException):
            failures.append((segment.segment_id, f"{type(outcome).__name__}: {outcome}"))
    if progress:
        progress(0.85)

    vectors = await _embed(project_id, usable, frames)
    for segment in usable:
        vector = vectors.get(segment.segment_id)
        if vector is not None and vector.size:
            segment.embedding_dim = int(vector.size)

    _persist(segments, vectors)
    if progress:
        progress(1.0)
    return AnalysisResult(
        project_id=project_id, segments=segments, failed=failures
    )


def _persist(
    segments: list[MaterialSegment], vectors: dict[str, np.ndarray]
) -> None:
    with session_scope() as session:
        for segment in segments:
            vector = vectors.get(segment.segment_id)
            row = session.get(Segment, segment.segment_id)
            values = dict(
                project_id=segment.project_id,
                asset_id=segment.source_id,
                kind=segment.kind,
                start=segment.start,
                end=segment.end,
                duration=segment.duration,
                semantic=segment.semantic,
                semantic_role=segment.semantic_role,
                shot_type=segment.shot_type,
                camera_motion=segment.camera_motion,
                people=segment.people,
                technical_quality=segment.technical_quality,
                sharpness=segment.sharpness,
                exposure_score=segment.exposure_score,
                stability_score=segment.stability_score,
                motion_quality=segment.motion_quality,
                composition_score=segment.composition_score,
                interestingness=segment.interestingness,
                emotion_json=segment.emotion,
                has_speech=segment.has_speech,
                asr_text=segment.asr_text,
                keyframe_keys_json=segment.keyframe_keys,
                embedding=to_blob(vector) if vector is not None else None,
                embedding_dim=segment.embedding_dim,
                is_usable=segment.is_usable,
                reject_reason=segment.reject_reason,
            )
            if row is None:
                session.add(Segment(id=segment.segment_id, **values))
            else:
                for key, value in values.items():
                    setattr(row, key, value)


def load_segments(project_id: str) -> list[MaterialSegment]:
    with session_scope() as session:
        rows = session.query(Segment).filter_by(project_id=project_id).all()
        return [
            MaterialSegment(
                segment_id=r.id, project_id=r.project_id, source_id=r.asset_id,
                kind=r.kind, start=r.start, end=r.end, semantic=r.semantic,
                semantic_role=r.semantic_role, shot_type=r.shot_type,
                camera_motion=r.camera_motion, people=r.people,
                technical_quality=r.technical_quality, sharpness=r.sharpness,
                exposure_score=r.exposure_score, stability_score=r.stability_score,
                motion_quality=r.motion_quality, composition_score=r.composition_score,
                interestingness=r.interestingness, emotion=r.emotion_json or [],
                has_speech=r.has_speech, asr_text=r.asr_text,
                keyframe_keys=r.keyframe_keys_json or [],
                embedding_dim=r.embedding_dim, is_usable=r.is_usable,
                reject_reason=r.reject_reason,
            )
            for r in rows
        ]
