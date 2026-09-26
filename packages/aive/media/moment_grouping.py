"""Validation and deterministic assembly of semantic multi-Shot Moments."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np

from ..contracts.moments import MomentEvidence, MomentGroupDecision
from ..material.embedding import from_blob, segment_vector, to_blob
from .analysis_repository import PersistedMoment, PersistedTranscript


class InvalidMomentGrouping(ValueError):
    pass


def validate_grouping(
    decisions: tuple[MomentGroupDecision, ...],
    moments: tuple[PersistedMoment, ...],
) -> None:
    expected = [moment.evidence.shot_ids[0] for moment in moments]
    actual = [shot_id for decision in decisions for shot_id in decision.shot_ids]
    if actual != expected:
        raise InvalidMomentGrouping(
            "Moment groups must cover every Shot exactly once in source order"
        )


def attach_transcripts(
    moments: tuple[PersistedMoment, ...],
    transcripts: tuple[PersistedTranscript, ...],
) -> tuple[PersistedMoment, ...]:
    return tuple(
        _with_transcript(moment, _overlapping(moment.evidence, transcripts))
        for moment in moments
    )


def assemble_groups(
    asset_id: str,
    decisions: tuple[MomentGroupDecision, ...],
    moments: tuple[PersistedMoment, ...],
    transcripts: tuple[PersistedTranscript, ...],
    provenance: dict,
) -> tuple[PersistedMoment, ...]:
    validate_grouping(decisions, moments)
    by_shot = {
        moment.evidence.shot_ids[0]: moment for moment in moments
    }
    assembled: list[PersistedMoment] = []
    for ordinal, decision in enumerate(decisions):
        members = tuple(by_shot[shot_id] for shot_id in decision.shot_ids)
        first = members[0].evidence
        last = members[-1].evidence
        moment_id = (
            f"{asset_id}_moment_{first.ordinal:04d}"
            if first.ordinal == last.ordinal
            else f"{asset_id}_moment_{first.ordinal:04d}_{last.ordinal:04d}"
        )
        related = _overlapping_span(
            first.source.start,
            last.source.end,
            transcripts,
        )
        evidence = MomentEvidence(
            moment_id=moment_id,
            project_id=first.project_id,
            asset_id=first.asset_id,
            ordinal=ordinal,
            shot_ids=decision.shot_ids,
            source=first.source.model_copy(update={"end": last.source.end}),
            analysis_window_count=sum(
                member.evidence.analysis_window_count for member in members
            ),
            keyframe_count=sum(member.evidence.keyframe_count for member in members),
            technical_quality=_weighted(members, "technical_quality"),
            sharpness=_weighted(members, "sharpness"),
            exposure_score=_weighted(members, "exposure_score"),
            stability_score=_weighted(members, "stability_score"),
            motion_quality=_weighted(members, "motion_quality"),
            camera_motion=_same_or_unknown(
                tuple(member.evidence.camera_motion for member in members)
            ),
            is_usable=any(member.evidence.is_usable for member in members),
            reject_reason=(
                None
                if any(member.evidence.is_usable for member in members)
                else "; ".join(
                    filter(
                        None,
                        (member.evidence.reject_reason for member in members),
                    )
                )[:256]
                or None
            ),
            semantic_status="ready",
            semantic=decision.semantic,
            semantic_role=decision.semantic_role,
            shot_type=_same_or_unknown(
                tuple(member.evidence.shot_type for member in members)
            ),
            people=max(
                (
                    member.evidence.people
                    for member in members
                    if member.evidence.people is not None
                ),
                default=None,
            ),
            emotion=tuple(
                dict.fromkeys(
                    emotion
                    for member in members
                    for emotion in member.evidence.emotion
                )
            ),
            interestingness=_weighted(members, "interestingness"),
            composition_score=_weighted(members, "composition_score"),
            provider=provenance.get("provider"),
            model=provenance.get("model"),
            prompt_version=provenance.get("prompt_version"),
            transcript_segment_ids=tuple(
                item.evidence.transcript_id for item in related
            ),
            has_speech=bool(related),
            asr_text=_transcript_text(related),
        )
        vector = _combined_vector(members)
        assembled.append(
            PersistedMoment(
                evidence=evidence,
                keyframe_keys=tuple(
                    key for member in members for key in member.keyframe_keys
                ),
                raw_response=provenance.get("raw_response"),
                embedding=to_blob(vector) if vector is not None else None,
                embedding_dim=int(vector.size) if vector is not None else None,
                analyzed_at=datetime.now(timezone.utc),
            )
        )
    return tuple(assembled)


def _with_transcript(
    moment: PersistedMoment,
    transcripts: tuple[PersistedTranscript, ...],
) -> PersistedMoment:
    evidence = moment.evidence.model_copy(
        update={
            "transcript_segment_ids": tuple(
                item.evidence.transcript_id for item in transcripts
            ),
            "has_speech": bool(transcripts),
            "asr_text": _transcript_text(transcripts),
        }
    )
    return PersistedMoment(
        evidence=evidence,
        keyframe_keys=moment.keyframe_keys,
        raw_response=moment.raw_response,
        embedding=moment.embedding,
        embedding_dim=moment.embedding_dim,
        analyzed_at=moment.analyzed_at,
    )


def _overlapping(
    moment: MomentEvidence,
    transcripts: tuple[PersistedTranscript, ...],
) -> tuple[PersistedTranscript, ...]:
    return _overlapping_span(moment.source.start, moment.source.end, transcripts)


def _overlapping_span(
    start: int,
    end: int,
    transcripts: tuple[PersistedTranscript, ...],
) -> tuple[PersistedTranscript, ...]:
    return tuple(
        item
        for item in transcripts
        if item.evidence.source.end > start and item.evidence.source.start < end
    )


def _transcript_text(
    transcripts: tuple[PersistedTranscript, ...],
) -> str | None:
    text = " ".join(item.evidence.text.strip() for item in transcripts).strip()
    return text[:20_000] or None


def _weighted(
    moments: tuple[PersistedMoment, ...], field: str
) -> float | None:
    values: list[tuple[float, int]] = []
    for moment in moments:
        value = getattr(moment.evidence, field)
        if value is not None:
            duration = moment.evidence.source.end - moment.evidence.source.start
            values.append((float(value), duration))
    if not values:
        return None
    total = sum(duration for _, duration in values)
    return round(sum(value * duration for value, duration in values) / total, 4)


def _same_or_unknown(values: tuple[str, ...]) -> str:
    return values[0] if values and len(set(values)) == 1 else "unknown"


def _combined_vector(
    moments: tuple[PersistedMoment, ...],
) -> np.ndarray | None:
    vectors = [
        from_blob(moment.embedding)
        for moment in moments
        if moment.embedding is not None
    ]
    if not vectors or len({vector.size for vector in vectors}) != 1:
        return None
    return segment_vector(np.stack(vectors))
