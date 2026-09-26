"""Durable Shot -> analysis-window -> Moment understanding pipeline.

Physical Shot boundaries are never split to satisfy a model input limit. Long
shots are sampled through overlapping windows, while every persisted Moment
keeps the complete physical source span.
"""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..contracts.moments import (
    DetectedShot,
    MediaSourceSpan,
    MomentEvidence,
    ShotObservation,
    TranscriptEvidence,
)
from ..jobs import Stage
from ..material import keyframes, quality, split, usability
from ..material.embedding import segment_vector, to_blob
from ..providers import Role, capture_provenance, get_provider
from ..specs.base import new_id
from ..specs.segment import RawSegment
from ..storage import KEYFRAME, StorageProvider, project_key
from .analysis_repository import (
    AnalysisAssetRecord,
    MediaAnalysisRepository,
    MediaAnalysisTicket,
    PersistedMoment,
    PersistedTranscript,
)
from .audio import extract_audio_window
from .moment_grouping import assemble_groups, attach_transcripts
from .processing import ProgressReporter


TIMESCALE = 1_000
MAX_ANALYSIS_WINDOW_SECONDS = 6.0
ANALYSIS_WINDOW_OVERLAP_SECONDS = 2.0
MAX_ANALYSIS_WINDOWS = 12
# 16 kHz mono PCM is 32 KB/s; 225s remains below Qwen's 10 MB limit even
# after Base64 expansion and leaves room for the WAV/Data-URL envelope.
MAX_ASR_WINDOW_SECONDS = 225.0
SHOT_DETECTOR = "pyscenedetect-content-27-v1"


class MediaAnalysisBatchFailed(RuntimeError):
    pass


def analysis_windows(
    start: float,
    end: float,
    *,
    max_seconds: float = MAX_ANALYSIS_WINDOW_SECONDS,
    overlap_seconds: float = ANALYSIS_WINDOW_OVERLAP_SECONDS,
    cap: int = MAX_ANALYSIS_WINDOWS,
) -> tuple[tuple[float, float], ...]:
    """Return bounded overlapping sampling windows without changing the Shot."""
    if end <= start:
        raise ValueError("analysis range end must follow start")
    if max_seconds <= 0 or overlap_seconds < 0 or overlap_seconds >= max_seconds:
        raise ValueError("analysis window and overlap are invalid")
    if cap < 1:
        raise ValueError("analysis window cap must be positive")
    if end - start <= max_seconds:
        return ((start, end),)

    stride = max_seconds - overlap_seconds
    starts: list[float] = []
    cursor = start
    last_start = end - max_seconds
    while cursor < last_start:
        starts.append(cursor)
        cursor += stride
    starts.append(last_start)

    if len(starts) > cap:
        step = (len(starts) - 1) / (cap - 1) if cap > 1 else 0
        indices = [round(index * step) for index in range(cap)]
        starts = [starts[index] for index in indices]
    return tuple((value, min(value + max_seconds, end)) for value in starts)


def audio_windows(
    start: float,
    end: float,
    *,
    max_seconds: float = MAX_ASR_WINDOW_SECONDS,
) -> tuple[tuple[float, float], ...]:
    """Split only the ASR input, while preserving the physical Shot boundary."""
    if end <= start:
        raise ValueError("audio range end must follow start")
    if max_seconds <= 0:
        raise ValueError("ASR window must be positive")
    windows: list[tuple[float, float]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + max_seconds, end)
        windows.append((cursor, window_end))
        cursor = window_end
    return tuple(windows)


def _provider(role: Role, project_id: str) -> Any:
    return get_provider(role, project_id=project_id)


class MediaAnalysisApplication:
    """Derives replaceable evidence from ready Assets; never edits ProjectDoc."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        repository: MediaAnalysisRepository,
        shot_detector: Callable[[Path], list[tuple[float, float]]] = split.detect_boundaries,
        video_quality: Callable[[str, float, float], quality.QualityMetrics] = quality.measure,
        photo_quality: Callable[[str], quality.QualityMetrics] = quality.measure_photo,
        keyframe_extractor: Callable[
            [str, list[float], list[Path]], list[Path]
        ] = keyframes.extract_at_offsets,
        audio_extractor: Callable[[Path, float, float, Path], Path] = (
            extract_audio_window
        ),
        provider_factory: Callable[[Role, str], Any] = _provider,
        id_factory: Callable[[str], str] = new_id,
    ) -> None:
        self._storage = storage
        self._repository = repository
        self._shot_detector = shot_detector
        self._video_quality = video_quality
        self._photo_quality = photo_quality
        self._keyframe_extractor = keyframe_extractor
        self._audio_extractor = audio_extractor
        self._provider_factory = provider_factory
        self._id_factory = id_factory

    def enqueue(
        self, project_id: str, asset_ids: tuple[str, ...]
    ) -> MediaAnalysisTicket:
        return self._repository.enqueue_analysis(
            project_id,
            asset_ids,
            self._id_factory("job"),
        )

    def list_moments(self, project_id: str) -> tuple[MomentEvidence, ...]:
        return self._repository.list_moments(project_id)

    def list_transcripts(
        self, project_id: str
    ) -> tuple[TranscriptEvidence, ...]:
        return self._repository.list_transcripts(project_id)

    def job_targets(self, job_id: str) -> tuple[str, tuple[str, ...]]:
        records = self._repository.list_job_assets(job_id)
        if not records:
            raise MediaAnalysisBatchFailed(
                f"media analysis job {job_id!r} owns no assets"
            )
        project_ids = {record.project_id for record in records}
        if len(project_ids) != 1:
            raise MediaAnalysisBatchFailed(
                f"media analysis job {job_id!r} spans multiple projects"
            )
        return next(iter(project_ids)), tuple(record.asset_id for record in records)

    async def process(self, handle: ProgressReporter) -> dict[str, Any]:
        records = self._repository.list_job_assets(handle.job_id)
        if not records:
            raise MediaAnalysisBatchFailed(
                f"media analysis job {handle.job_id!r} owns no assets"
            )

        pending = tuple(
            record
            for record in records
            if record.analysis_readiness == "processing"
        )
        failures: list[tuple[str, str]] = []
        partial_count = 0
        moment_count = 0
        transcript_count = 0
        for index, record in enumerate(pending):
            handle.update(stage=Stage.ANALYZING, progress=index / len(pending))
            try:
                shots, moments, transcripts, errors = await self._analyze_asset(
                    record, handle
                )
                readiness = "partial" if errors else "ready"
                partial_count += readiness == "partial"
                moment_count += len(moments)
                transcript_count += len(transcripts)
                self._repository.replace_asset_analysis(
                    project_id=record.project_id,
                    asset_id=record.asset_id,
                    job_id=handle.job_id,
                    shots=shots,
                    moments=moments,
                    transcripts=transcripts,
                    readiness=readiness,
                    error="; ".join(errors) if errors else None,
                )
                handle.emit(
                    "asset_analyzed",
                    asset_id=record.asset_id,
                    moment_count=len(moments),
                    transcript_count=len(transcripts),
                    readiness=readiness,
                )
            except Exception as exc:  # noqa: BLE001 - per-asset worker boundary
                message = str(exc) or exc.__class__.__name__
                self._repository.mark_analysis_failed(
                    project_id=record.project_id,
                    asset_id=record.asset_id,
                    job_id=handle.job_id,
                    error=message,
                )
                failures.append((record.asset_id, message))
                handle.emit("asset_analysis_failed", asset_id=record.asset_id, error=message)
            handle.update(progress=(index + 1) / len(pending))

        if failures:
            failed_ids = ", ".join(asset_id for asset_id, _ in failures)
            raise MediaAnalysisBatchFailed(f"media analysis failed for {failed_ids}")
        return {
            "asset_count": len(pending),
            "moment_count": moment_count,
            "transcript_count": transcript_count,
            "partial_count": partial_count,
        }

    async def _analyze_asset(
        self,
        record: AnalysisAssetRecord,
        handle: ProgressReporter,
    ) -> tuple[
        tuple[DetectedShot, ...],
        tuple[PersistedMoment, ...],
        tuple[PersistedTranscript, ...],
        list[str],
    ]:
        if record.readiness != "ready":
            raise MediaAnalysisBatchFailed(
                f"asset {record.asset_id!r} is not ready for analysis"
            )
        source = self._storage.open_local(record.proxy_key or record.storage_key)
        spans = await self._detect_spans(record, source)
        material = self._provider_factory(Role.MATERIAL, record.project_id)
        embedding = self._provider_factory(Role.EMBEDDING, record.project_id)
        shots: list[DetectedShot] = []
        shot_moments: list[PersistedMoment] = []
        errors: list[str] = []

        for ordinal, (start, end) in enumerate(spans):
            shot_id = f"{record.asset_id}_shot_{ordinal:04d}"
            moment_id = f"{record.asset_id}_moment_{ordinal:04d}"
            source_span = _source_span(start, end)
            shot = DetectedShot(
                shot_id=shot_id,
                project_id=record.project_id,
                asset_id=record.asset_id,
                ordinal=ordinal,
                source=source_span,
                detector="still-image-v1" if record.kind == "photo" else SHOT_DETECTOR,
            )
            shots.append(shot)
            handle.emit(
                "shot_detected",
                asset_id=record.asset_id,
                shot_id=shot_id,
                start=start,
                end=end,
            )
            moment, moment_errors = await self._analyze_shot(
                record,
                source,
                shot,
                moment_id,
                start,
                end,
                material,
                embedding,
            )
            shot_moments.append(moment)
            errors.extend(moment_errors)

        transcripts, transcript_errors = await self._transcribe_asset(
            record,
            tuple(shots),
            handle,
        )
        errors.extend(transcript_errors)
        moments, grouping_errors = await self._group_asset_moments(
            record,
            tuple(shot_moments),
            transcripts,
            material,
        )
        errors.extend(grouping_errors)
        for moment in moments:
            handle.emit(
                "moment_analyzed",
                asset_id=record.asset_id,
                moment_id=moment.evidence.moment_id,
                semantic_status=moment.evidence.semantic_status,
            )
        return tuple(shots), moments, transcripts, errors

    async def _transcribe_asset(
        self,
        record: AnalysisAssetRecord,
        shots: tuple[DetectedShot, ...],
        handle: ProgressReporter,
    ) -> tuple[tuple[PersistedTranscript, ...], list[str]]:
        if record.kind != "video" or not record.has_audio:
            return (), []

        source = self._storage.open_local(record.storage_key)
        provider = self._provider_factory(Role.ASR, record.project_id)
        transcripts: list[PersistedTranscript] = []
        errors: list[str] = []
        ordinal = 0
        for shot in shots:
            shot_start = shot.source.start / shot.source.timescale
            shot_end = shot.source.end / shot.source.timescale
            for window_start, window_end in audio_windows(shot_start, shot_end):
                temporary = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                audio_path = Path(temporary.name)
                temporary.close()
                try:
                    await asyncio.to_thread(
                        self._audio_extractor,
                        source,
                        window_start,
                        window_end,
                        audio_path,
                    )
                    with capture_provenance() as provenance:
                        drafts = await provider.transcribe(audio_path)
                    window_duration = window_end - window_start
                    for draft in drafts:
                        relative_start = min(
                            max(draft.source.start / draft.source.timescale, 0.0),
                            window_duration,
                        )
                        relative_end = min(
                            max(draft.source.end / draft.source.timescale, 0.0),
                            window_duration,
                        )
                        if relative_end <= relative_start:
                            continue
                        transcript_id = f"{record.asset_id}_transcript_{ordinal:04d}"
                        evidence = TranscriptEvidence(
                            transcript_id=transcript_id,
                            project_id=record.project_id,
                            asset_id=record.asset_id,
                            source=_source_span(
                                window_start + relative_start,
                                window_start + relative_end,
                            ),
                            text=draft.text,
                            language=draft.language,
                            speaker=draft.speaker,
                            emotion=draft.emotion,
                            confidence=draft.confidence,
                            provider=provenance.get("provider"),
                            model=provenance.get("model"),
                        )
                        transcripts.append(
                            PersistedTranscript(
                                evidence=evidence,
                                raw_response=provenance.get("raw_response"),
                                analyzed_at=datetime.now(timezone.utc),
                            )
                        )
                        ordinal += 1
                        handle.emit(
                            "transcript_segment",
                            asset_id=record.asset_id,
                            transcript_id=transcript_id,
                            start=evidence.source.start,
                            end=evidence.source.end,
                        )
                except Exception as exc:  # retain visual analysis when ASR fails
                    message = (
                        f"ASR {record.asset_id} {window_start:.3f}-{window_end:.3f}: "
                        f"{type(exc).__name__}: {exc}"
                    )
                    errors.append(message[:2_000])
                finally:
                    audio_path.unlink(missing_ok=True)
        return tuple(transcripts), errors

    async def _group_asset_moments(
        self,
        record: AnalysisAssetRecord,
        shot_moments: tuple[PersistedMoment, ...],
        transcripts: tuple[PersistedTranscript, ...],
        material: Any,
    ) -> tuple[tuple[PersistedMoment, ...], list[str]]:
        fallback = attach_transcripts(shot_moments, transcripts)
        if len(shot_moments) <= 1:
            return fallback, []
        if not hasattr(material, "group_moments"):
            return fallback, [
                f"Moment grouping {record.asset_id}: provider does not support grouping"
            ]

        observations = tuple(
            ShotObservation(
                shot_id=moment.evidence.shot_ids[0],
                source=moment.evidence.source,
                visual_semantic=moment.evidence.semantic,
                semantic_role=moment.evidence.semantic_role,
                transcript=(enriched.evidence.asr_text or "")[:10_000] or None,
            )
            for moment, enriched in zip(shot_moments, fallback)
        )
        try:
            with capture_provenance() as provenance:
                decisions = await material.group_moments(observations)
            return (
                assemble_groups(
                    record.asset_id,
                    decisions,
                    shot_moments,
                    transcripts,
                    provenance,
                ),
                [],
            )
        except Exception as exc:  # valid singleton Moments remain searchable
            message = (
                f"Moment grouping {record.asset_id}: {type(exc).__name__}: {exc}"
            )
            return fallback, [message[:2_000]]

    async def _detect_spans(
        self, record: AnalysisAssetRecord, source: Path
    ) -> tuple[tuple[float, float], ...]:
        if record.kind == "photo":
            return ((0.0, record.duration_seconds),)
        detected = await asyncio.to_thread(self._shot_detector, source)
        clean: list[tuple[float, float]] = []
        for start, end in detected:
            bounded_start = max(0.0, min(float(start), record.duration_seconds))
            bounded_end = max(0.0, min(float(end), record.duration_seconds))
            if bounded_end > bounded_start:
                clean.append((bounded_start, bounded_end))
        return tuple(clean or ((0.0, record.duration_seconds),))

    async def _analyze_shot(
        self,
        record: AnalysisAssetRecord,
        source: Path,
        shot: DetectedShot,
        moment_id: str,
        start: float,
        end: float,
        material: Any,
        embedding: Any,
    ) -> tuple[PersistedMoment, list[str]]:
        metrics = await asyncio.to_thread(
            self._photo_quality if record.kind == "photo" else self._video_quality,
            str(source),
            *(() if record.kind == "photo" else (start, end)),
        )
        is_usable, reject_reason = usability.judge(
            end - start, metrics, record.kind
        )
        windows = analysis_windows(start, end)
        frame_paths, frame_keys = await self._representative_frames(
            record, source, moment_id, windows
        )
        semantic_status = "skipped" if not is_usable else "ready"
        semantic_error = None
        semantic = None
        semantic_role = "unknown"
        shot_type = "unknown"
        people = None
        emotion: tuple[str, ...] = ()
        interestingness = metrics.technical_quality
        composition_score = None
        provenance: dict[str, Any] = {}
        errors: list[str] = []

        raw = RawSegment(
            segment_id=moment_id,
            project_id=record.project_id,
            asset_id=record.asset_id,
            kind=record.kind,
            start=start,
            end=end,
        )
        if is_usable:
            try:
                with capture_provenance() as evidence:
                    analyzed = await material.analyze_segment(raw, frame_paths, None)
                provenance = dict(evidence)
                semantic = analyzed.semantic
                semantic_role = analyzed.semantic_role
                shot_type = analyzed.shot_type
                people = analyzed.people
                emotion = tuple(analyzed.emotion)
                interestingness = analyzed.interestingness
                composition_score = analyzed.composition_score
                if analyzed.reject_reason:
                    is_usable = False
                    reject_reason = analyzed.reject_reason
            except Exception as exc:  # model failure preserves deterministic evidence
                semantic_status = "failed"
                semantic_error = f"{type(exc).__name__}: {exc}"[:2_000]
                errors.append(f"{moment_id}: {semantic_error}")

        vector_blob = None
        vector_dim = None
        if frame_paths:
            try:
                frame_vectors = await embedding.embed_images(frame_paths)
                vector = segment_vector(frame_vectors)
                if vector.size:
                    vector_blob = to_blob(vector)
                    vector_dim = int(vector.size)
            except Exception as exc:  # retrieval may be retried without losing Moments
                message = f"embedding {moment_id}: {type(exc).__name__}: {exc}"
                errors.append(message[:2_000])

        evidence = MomentEvidence(
            moment_id=moment_id,
            project_id=record.project_id,
            asset_id=record.asset_id,
            ordinal=shot.ordinal,
            shot_ids=(shot.shot_id,),
            source=shot.source,
            analysis_window_count=len(windows),
            keyframe_count=len(frame_keys),
            technical_quality=metrics.technical_quality,
            sharpness=metrics.sharpness,
            exposure_score=metrics.exposure_score,
            stability_score=metrics.stability_score,
            motion_quality=metrics.motion_quality,
            camera_motion=metrics.camera_motion,
            is_usable=is_usable,
            reject_reason=reject_reason,
            semantic_status=semantic_status,
            semantic=semantic,
            semantic_role=semantic_role,
            shot_type=shot_type,
            people=people,
            emotion=emotion,
            interestingness=interestingness,
            composition_score=composition_score,
            semantic_error=semantic_error,
            provider=provenance.get("provider"),
            model=provenance.get("model"),
            prompt_version=provenance.get("prompt_version"),
        )
        return PersistedMoment(
            evidence=evidence,
            keyframe_keys=frame_keys,
            raw_response=provenance.get("raw_response"),
            embedding=vector_blob,
            embedding_dim=vector_dim,
            analyzed_at=datetime.now(timezone.utc) if semantic_status == "ready" else None,
        ), errors

    async def _representative_frames(
        self,
        record: AnalysisAssetRecord,
        source: Path,
        moment_id: str,
        windows: tuple[tuple[float, float], ...],
    ) -> tuple[list[Path], tuple[str, ...]]:
        if record.kind == "photo":
            return [source], (record.proxy_key or record.storage_key,)
        keys = tuple(
            project_key(
                record.project_id,
                KEYFRAME,
                f"{moment_id}_window_{index:03d}.jpg",
            )
            for index in range(len(windows))
        )
        targets = [self._storage.reserve_local(key) for key in keys]
        offsets = [(start + end) / 2 for start, end in windows]
        paths = await asyncio.to_thread(
            self._keyframe_extractor,
            str(source),
            offsets,
            targets,
        )
        path_to_key = {path: key for path, key in zip(targets, keys)}
        committed_keys: list[str] = []
        for path in paths:
            key = path_to_key.get(path)
            if key is not None and path.exists():
                self._storage.commit(key)
                committed_keys.append(key)
        return paths, tuple(committed_keys)


def _source_span(start: float, end: float) -> MediaSourceSpan:
    start_tick = max(0, round(start * TIMESCALE))
    end_tick = max(start_tick + 1, round(end * TIMESCALE))
    return MediaSourceSpan(start=start_tick, end=end_tick, timescale=TIMESCALE)
