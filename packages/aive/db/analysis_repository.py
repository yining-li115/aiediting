"""SQLAlchemy adapter for media-analysis jobs, Shots, and Moments."""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from ..contracts.moments import (
    DetectedShot,
    MediaSourceSpan,
    MomentEvidence,
    TranscriptEvidence,
)
from ..media.analysis_repository import (
    AnalysisAssetRecord,
    MediaAnalysisConflict,
    MediaAnalysisTicket,
    PersistedMoment,
    PersistedTranscript,
)
from ..media.repository import ProjectAssetNotFound
from ..project.repository import ProjectNotFound
from .models import (
    Asset,
    Job,
    MediaShotRow,
    MomentRow,
    Project,
    TranscriptRow,
    utcnow,
)


class SqlAlchemyMediaAnalysisRepository:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self.session_factory = sessions

    def enqueue_analysis(
        self,
        project_id: str,
        asset_ids: tuple[str, ...],
        job_id: str,
    ) -> MediaAnalysisTicket:
        ordered_ids = tuple(dict.fromkeys(asset_ids))
        if not ordered_ids:
            return MediaAnalysisTicket(job_id=None, asset_ids=(), created=False)

        with self.session_factory.begin() as session:
            if session.get(Project, project_id) is None:
                raise ProjectNotFound(f"project {project_id!r} does not exist")
            rows = session.scalars(
                select(Asset).where(
                    Asset.project_id == project_id,
                    Asset.id.in_(ordered_ids),
                )
            ).all()
            by_id = {row.id: row for row in rows}
            missing = tuple(asset_id for asset_id in ordered_ids if asset_id not in by_id)
            if missing:
                raise ProjectAssetNotFound(
                    f"assets {missing!r} are not registered to project {project_id!r}"
                )
            # Audio-only music/voiceover/SFX Assets are already ready for the
            # renderer after media processing. Shot/Moment analysis is visual
            # and must not fail the whole upload batch for those files.
            ordered = tuple(
                by_id[asset_id]
                for asset_id in ordered_ids
                if by_id[asset_id].kind != "audio"
            )
            if not ordered:
                return MediaAnalysisTicket(job_id=None, asset_ids=(), created=False)
            not_ready = tuple(row.id for row in ordered if row.readiness != "ready")
            if not_ready:
                raise MediaAnalysisConflict(
                    f"assets {not_ready!r} are not ready for analysis"
                )

            pending = tuple(
                row
                for row in ordered
                if row.analysis_readiness != "ready"
            )
            if not pending:
                existing_ids = {
                    row.analysis_job_id for row in ordered if row.analysis_job_id
                }
                return MediaAnalysisTicket(
                    job_id=next(iter(existing_ids)) if len(existing_ids) == 1 else None,
                    asset_ids=(),
                    created=False,
                )

            active_ids = {
                row.analysis_job_id
                for row in pending
                if row.analysis_readiness == "processing" and row.analysis_job_id
            }
            if active_ids:
                if len(active_ids) == 1 and all(
                    row.analysis_readiness == "processing"
                    and row.analysis_job_id in active_ids
                    for row in pending
                ):
                    return MediaAnalysisTicket(
                        job_id=next(iter(active_ids)),
                        asset_ids=tuple(row.id for row in pending),
                        created=False,
                    )
                raise MediaAnalysisConflict(
                    "assets are already owned by a different analysis job"
                )

            if session.get(Job, job_id) is not None:
                raise MediaAnalysisConflict(f"job id {job_id!r} already exists")
            session.add(
                Job(
                    id=job_id,
                    project_id=project_id,
                    kind="media_analysis",
                    status="queued",
                    stage="analyzing",
                    progress=0.0,
                )
            )
            for row in pending:
                row.analysis_readiness = "processing"
                row.analysis_job_id = job_id
                row.analysis_error = None
                row.analyzed_at = None
            session.flush()
            return MediaAnalysisTicket(
                job_id=job_id,
                asset_ids=tuple(row.id for row in pending),
                created=True,
            )

    def list_job_assets(self, job_id: str) -> tuple[AnalysisAssetRecord, ...]:
        with self.session_factory() as session:
            rows = session.scalars(
                select(Asset)
                .where(Asset.analysis_job_id == job_id)
                .order_by(Asset.created_at, Asset.id)
            ).all()
            return tuple(_asset_record(row) for row in rows)

    def replace_asset_analysis(
        self,
        *,
        project_id: str,
        asset_id: str,
        job_id: str,
        shots: tuple[DetectedShot, ...],
        moments: tuple[PersistedMoment, ...],
        transcripts: tuple[PersistedTranscript, ...],
        readiness: str,
        error: str | None = None,
    ) -> None:
        if readiness not in {"ready", "partial"}:
            raise ValueError("successful analysis readiness must be ready or partial")
        with self.session_factory.begin() as session:
            asset = _owned_analysis_row(session, project_id, asset_id, job_id)
            session.execute(delete(MomentRow).where(MomentRow.asset_id == asset_id))
            session.execute(
                delete(TranscriptRow).where(TranscriptRow.asset_id == asset_id)
            )
            session.execute(delete(MediaShotRow).where(MediaShotRow.asset_id == asset_id))
            session.add_all(
                MediaShotRow(
                    id=shot.shot_id,
                    project_id=shot.project_id,
                    asset_id=shot.asset_id,
                    ordinal=shot.ordinal,
                    start_tick=shot.source.start,
                    end_tick=shot.source.end,
                    timescale=shot.source.timescale,
                    detector=shot.detector,
                )
                for shot in shots
            )
            for ordinal, record in enumerate(transcripts):
                transcript = record.evidence
                session.add(
                    TranscriptRow(
                        id=transcript.transcript_id,
                        project_id=transcript.project_id,
                        asset_id=transcript.asset_id,
                        ordinal=ordinal,
                        start_tick=transcript.source.start,
                        end_tick=transcript.source.end,
                        timescale=transcript.source.timescale,
                        text=transcript.text,
                        language=transcript.language,
                        speaker=transcript.speaker,
                        emotion=transcript.emotion,
                        confidence=transcript.confidence,
                        provider=transcript.provider,
                        model=transcript.model,
                        raw_response=record.raw_response,
                        analyzed_at=record.analyzed_at,
                    )
                )
            for record in moments:
                moment = record.evidence
                session.add(
                    MomentRow(
                        id=moment.moment_id,
                        project_id=moment.project_id,
                        asset_id=moment.asset_id,
                        ordinal=moment.ordinal,
                        shot_ids_json=list(moment.shot_ids),
                        start_tick=moment.source.start,
                        end_tick=moment.source.end,
                        timescale=moment.source.timescale,
                        analysis_window_count=moment.analysis_window_count,
                        keyframe_keys_json=list(record.keyframe_keys),
                        technical_quality=moment.technical_quality,
                        sharpness=moment.sharpness,
                        exposure_score=moment.exposure_score,
                        stability_score=moment.stability_score,
                        motion_quality=moment.motion_quality,
                        camera_motion=moment.camera_motion,
                        is_usable=moment.is_usable,
                        reject_reason=moment.reject_reason,
                        semantic_status=moment.semantic_status,
                        semantic=moment.semantic,
                        semantic_role=moment.semantic_role,
                        shot_type=moment.shot_type,
                        people=moment.people,
                        emotion_json=list(moment.emotion),
                        interestingness=moment.interestingness,
                        composition_score=moment.composition_score,
                        semantic_error=moment.semantic_error,
                        provider=moment.provider,
                        model=moment.model,
                        prompt_version=moment.prompt_version,
                        raw_response=record.raw_response,
                        embedding=record.embedding,
                        embedding_dim=record.embedding_dim,
                        transcript_segment_ids_json=list(
                            moment.transcript_segment_ids
                        ),
                        has_speech=moment.has_speech,
                        asr_text=moment.asr_text,
                        analyzed_at=record.analyzed_at,
                    )
                )
            asset.analysis_readiness = readiness
            asset.analysis_error = error[:2_000] if error else None
            asset.analyzed_at = utcnow()
            session.flush()

    def mark_analysis_failed(
        self,
        *,
        project_id: str,
        asset_id: str,
        job_id: str,
        error: str,
    ) -> None:
        with self.session_factory.begin() as session:
            asset = _owned_analysis_row(session, project_id, asset_id, job_id)
            asset.analysis_readiness = "failed"
            asset.analysis_error = error[:2_000]
            asset.analyzed_at = utcnow()

    def list_moments(self, project_id: str) -> tuple[MomentEvidence, ...]:
        with self.session_factory() as session:
            if session.get(Project, project_id) is None:
                raise ProjectNotFound(f"project {project_id!r} does not exist")
            rows = session.scalars(
                select(MomentRow)
                .where(MomentRow.project_id == project_id)
                .order_by(MomentRow.asset_id, MomentRow.ordinal)
            ).all()
            return tuple(_moment_evidence(row) for row in rows)

    def list_transcripts(
        self, project_id: str
    ) -> tuple[TranscriptEvidence, ...]:
        with self.session_factory() as session:
            if session.get(Project, project_id) is None:
                raise ProjectNotFound(f"project {project_id!r} does not exist")
            rows = session.scalars(
                select(TranscriptRow)
                .where(TranscriptRow.project_id == project_id)
                .order_by(TranscriptRow.asset_id, TranscriptRow.ordinal)
            ).all()
            return tuple(_transcript_evidence(row) for row in rows)


def _asset_record(row: Asset) -> AnalysisAssetRecord:
    if row.kind not in {"video", "photo"}:
        raise MediaAnalysisConflict(f"asset {row.id!r} has unsupported kind")
    if row.analysis_readiness not in {
        "pending", "processing", "ready", "partial", "failed"
    }:
        raise MediaAnalysisConflict(
            f"asset {row.id!r} has unsupported analysis readiness"
        )
    return AnalysisAssetRecord(
        asset_id=row.id,
        project_id=row.project_id,
        storage_key=row.storage_key,
        proxy_key=row.proxy_key,
        kind=row.kind,
        duration_seconds=row.duration,
        width=row.width,
        height=row.height,
        fps=row.fps,
        has_audio=row.has_audio,
        readiness=row.readiness,
        analysis_readiness=row.analysis_readiness,
        analysis_job_id=row.analysis_job_id,
        analysis_error=row.analysis_error,
    )


def _owned_analysis_row(
    session: Session, project_id: str, asset_id: str, job_id: str
) -> Asset:
    row = session.get(Asset, asset_id)
    if row is None or row.project_id != project_id:
        raise ProjectAssetNotFound(
            f"asset {asset_id!r} is not registered to project {project_id!r}"
        )
    if row.analysis_job_id != job_id:
        raise MediaAnalysisConflict(
            f"asset {asset_id!r} is no longer owned by job {job_id!r}"
        )
    return row


def _moment_evidence(row: MomentRow) -> MomentEvidence:
    return MomentEvidence(
        moment_id=row.id,
        project_id=row.project_id,
        asset_id=row.asset_id,
        ordinal=row.ordinal,
        shot_ids=tuple(row.shot_ids_json),
        source=MediaSourceSpan(
            start=row.start_tick,
            end=row.end_tick,
            timescale=row.timescale,
        ),
        analysis_window_count=row.analysis_window_count,
        keyframe_count=len(row.keyframe_keys_json),
        technical_quality=row.technical_quality,
        sharpness=row.sharpness,
        exposure_score=row.exposure_score,
        stability_score=row.stability_score,
        motion_quality=row.motion_quality,
        camera_motion=row.camera_motion,
        is_usable=row.is_usable,
        reject_reason=row.reject_reason,
        semantic_status=row.semantic_status,
        semantic=row.semantic,
        semantic_role=row.semantic_role,
        shot_type=row.shot_type,
        people=row.people,
        emotion=tuple(row.emotion_json or ()),
        interestingness=row.interestingness,
        composition_score=row.composition_score,
        semantic_error=row.semantic_error,
        provider=row.provider,
        model=row.model,
        prompt_version=row.prompt_version,
        transcript_segment_ids=tuple(row.transcript_segment_ids_json or ()),
        has_speech=row.has_speech,
        asr_text=row.asr_text,
    )


def _transcript_evidence(row: TranscriptRow) -> TranscriptEvidence:
    return TranscriptEvidence(
        transcript_id=row.id,
        project_id=row.project_id,
        asset_id=row.asset_id,
        source=MediaSourceSpan(
            start=row.start_tick,
            end=row.end_tick,
            timescale=row.timescale,
        ),
        text=row.text,
        language=row.language,
        speaker=row.speaker,
        emotion=row.emotion,
        confidence=row.confidence,
        provider=row.provider,
        model=row.model,
    )
