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
    AudioEvidence,
    AudioUnderstanding,
    DetectedShot,
    MediaEvidence,
    MediaSourceSpan,
    MomentEvidence,
    ShotObservation,
    SourceUnderstanding,
    TranscriptEvidence,
)
from ..jobs import Stage
from ..material import keyframes, split
from ..material.embedding import segment_vector, to_blob
from ..providers import Role, capture_provenance, get_provider
from ..ids import new_id
from ..storage import ANALYSIS_AUDIO, KEYFRAME, StorageProvider, project_key
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
        shots: list[DetectedShot] = []

        for ordinal, (start, end) in enumerate(spans):
            shot_id = f"{record.asset_id}_shot_{ordinal:04d}"
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

        transcripts = await self._transcribe_asset(
            record,
            tuple(shots),
            handle,
        )
        material = self._provider_factory(Role.MATERIAL, record.project_id)
        embedding = self._provider_factory(Role.EMBEDDING, record.project_id)
        audio_understanding = (
            self._provider_factory(Role.AUDIO, record.project_id)
            if record.kind == "video" and record.has_audio
            else None
        )
        shot_moments: list[PersistedMoment] = []
        for shot in shots:
            start = shot.source.start / shot.source.timescale
            end = shot.source.end / shot.source.timescale
            shot_moments.append(
                await self._analyze_shot(
                    record,
                    source,
                    shot,
                    f"{record.asset_id}_moment_{shot.ordinal:04d}",
                    start,
                    end,
                    transcripts,
                    material,
                    embedding,
                    audio_understanding,
                )
            )

        moments = await self._group_asset_moments(
            record,
            tuple(shot_moments),
            transcripts,
            material,
        )
        for moment in moments:
            handle.emit(
                "moment_analyzed",
                asset_id=record.asset_id,
                moment_id=moment.evidence.moment_id,
                worth_using=moment.evidence.understanding.worth_using,
                confidence=moment.evidence.understanding.confidence,
            )
        return tuple(shots), moments, transcripts, []

    async def _transcribe_asset(
        self,
        record: AnalysisAssetRecord,
        shots: tuple[DetectedShot, ...],
        handle: ProgressReporter,
    ) -> tuple[PersistedTranscript, ...]:
        if record.kind != "video" or not record.has_audio:
            return ()

        source = self._storage.open_local(record.storage_key)
        provider = self._provider_factory(Role.ASR, record.project_id)
        transcripts: list[PersistedTranscript] = []
        ordinal = 0
        window_ordinal = 0
        for shot in shots:
            shot_start = shot.source.start / shot.source.timescale
            shot_end = shot.source.end / shot.source.timescale
            for window_start, window_end in audio_windows(shot_start, shot_end):
                temporary = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                audio_path = Path(temporary.name)
                temporary.close()
                published_key: str | None = None
                try:
                    await asyncio.to_thread(
                        self._audio_extractor,
                        source,
                        window_start,
                        window_end,
                        audio_path,
                    )
                    if getattr(provider, "requires_remote_url", False):
                        published_key = project_key(
                            record.project_id,
                            ANALYSIS_AUDIO,
                            (
                                f"{handle.job_id}-{record.asset_id}-"
                                f"{window_ordinal:04d}.wav"
                            ),
                        )
                        self._storage.put(audio_path, published_key)
                        source_url = self._storage.url(published_key)
                        if source_url is None:
                            raise MediaAnalysisBatchFailed(
                                "the selected ASR provider requires a remotely "
                                "fetchable audio URL, but the configured storage "
                                "backend cannot publish one; configure private TOS "
                                "with short-lived signed URLs"
                            )
                        with capture_provenance() as provenance:
                            drafts = await provider.transcribe(
                                audio_path,
                                source_url=source_url,
                            )
                    else:
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
                finally:
                    if published_key is not None:
                        self._storage.delete(published_key)
                    audio_path.unlink(missing_ok=True)
                    window_ordinal += 1
        return tuple(transcripts)

    async def _group_asset_moments(
        self,
        record: AnalysisAssetRecord,
        shot_moments: tuple[PersistedMoment, ...],
        transcripts: tuple[PersistedTranscript, ...],
        material: Any,
    ) -> tuple[PersistedMoment, ...]:
        transcript_enriched_shots = attach_transcripts(shot_moments, transcripts)
        if len(shot_moments) <= 1:
            return transcript_enriched_shots
        if not hasattr(material, "group_moments"):
            raise MediaAnalysisBatchFailed(
                f"material provider cannot group Moments for {record.asset_id!r}"
            )

        observations = tuple(
            ShotObservation(
                shot_id=moment.evidence.shot_ids[0],
                source=moment.evidence.source,
                understanding=moment.evidence.understanding,
                transcript=(enriched.evidence.asr_text or "")[:10_000] or None,
            )
            for moment, enriched in zip(shot_moments, transcript_enriched_shots)
        )
        with capture_provenance() as provenance:
            decisions = await material.group_moments(observations)
        return assemble_groups(
            record.asset_id,
            decisions,
            shot_moments,
            transcripts,
            provenance,
        )

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
        transcripts: tuple[PersistedTranscript, ...],
        material: Any,
        embedding: Any,
        audio_understanding: Any | None,
    ) -> PersistedMoment:
        windows = analysis_windows(start, end)
        frame_paths, frame_keys = await self._representative_frames(
            record, source, moment_id, windows
        )
        related = tuple(
            item
            for item in transcripts
            if item.evidence.source.end > shot.source.start
            and item.evidence.source.start < shot.source.end
        )
        evidence_packet = MediaEvidence(
            evidence_id=f"{moment_id}_evidence",
            project_id=record.project_id,
            asset_id=record.asset_id,
            asset_kind=record.kind,
            source=shot.source,
            shot_ids=(shot.shot_id,),
            sample_ids=frame_keys,
            transcript_segment_ids=tuple(
                item.evidence.transcript_id for item in related
            ),
            transcript_text=_transcript_text(related),
            width=record.width,
            height=record.height,
            fps=record.fps,
            has_audio=record.has_audio,
        )
        audio_findings = await self._understand_audio_span(
            record,
            shot,
            related,
            audio_understanding,
        )
        with capture_provenance() as provenance:
            understanding = await material.understand_source(
                evidence_packet,
                frame_paths,
                None,
            )
        if audio_findings:
            understanding = understanding.model_copy(
                update={"audio_understanding": audio_findings}
            )
        _validate_understanding(understanding, evidence_packet)

        frame_vectors = await embedding.embed_images(frame_paths)
        vector = segment_vector(frame_vectors)
        vector_blob = to_blob(vector) if vector.size else None
        vector_dim = int(vector.size) if vector.size else None

        moment_evidence = MomentEvidence(
            moment_id=moment_id,
            project_id=record.project_id,
            asset_id=record.asset_id,
            ordinal=shot.ordinal,
            shot_ids=(shot.shot_id,),
            source=shot.source,
            analysis_window_count=len(windows),
            keyframe_count=len(frame_keys),
            understanding=understanding,
            transcript_segment_ids=evidence_packet.transcript_segment_ids,
            has_speech=bool(related),
            asr_text=evidence_packet.transcript_text,
        )
        return PersistedMoment(
            evidence=moment_evidence,
            keyframe_keys=frame_keys,
            raw_response=provenance.get("raw_response"),
            embedding=vector_blob,
            embedding_dim=vector_dim,
            analyzed_at=datetime.now(timezone.utc),
        )

    async def _understand_audio_span(
        self,
        record: AnalysisAssetRecord,
        shot: DetectedShot,
        transcripts: tuple[PersistedTranscript, ...],
        provider: Any | None,
    ) -> tuple[AudioUnderstanding, ...]:
        if provider is None:
            return ()
        source = self._storage.open_local(record.storage_key)
        start = shot.source.start / shot.source.timescale
        end = shot.source.end / shot.source.timescale
        temporary = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        audio_path = Path(temporary.name)
        temporary.close()
        try:
            await asyncio.to_thread(
                self._audio_extractor,
                source,
                start,
                end,
                audio_path,
            )
            evidence = AudioEvidence(
                evidence_id=f"{shot.shot_id}_audio_evidence",
                project_id=record.project_id,
                asset_id=record.asset_id,
                source=shot.source,
                transcript_segment_ids=tuple(
                    item.evidence.transcript_id for item in transcripts
                ),
                transcript_text=_transcript_text(transcripts),
            )
            finding = await provider.understand_audio(evidence, audio_path)
            return (finding,)
        finally:
            audio_path.unlink(missing_ok=True)

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


def _transcript_text(
    transcripts: tuple[PersistedTranscript, ...],
) -> str | None:
    text = " ".join(item.evidence.text.strip() for item in transcripts).strip()
    return text[:20_000] or None


def _validate_understanding(
    understanding: SourceUnderstanding,
    supplied: MediaEvidence,
) -> None:
    allowed_shots = set(supplied.shot_ids)
    for citation in understanding.evidence:
        if citation.evidence_id != supplied.evidence_id:
            raise MediaAnalysisBatchFailed(
                "source model cited evidence that was not supplied"
            )
        if set(citation.shot_ids) - allowed_shots:
            raise MediaAnalysisBatchFailed(
                "source model cited a Shot that was not supplied"
            )
        if citation.source is not None and (
            citation.source.timescale != supplied.source.timescale
            or citation.source.start < supplied.source.start
            or citation.source.end > supplied.source.end
        ):
            raise MediaAnalysisBatchFailed(
                "source model cited a range outside the supplied evidence"
            )
    for audio in understanding.audio_understanding:
        if audio.source.timescale != supplied.source.timescale or (
            audio.source.start < supplied.source.start
            or audio.source.end > supplied.source.end
        ):
            raise MediaAnalysisBatchFailed(
                "audio model cited a range outside the supplied evidence"
            )
