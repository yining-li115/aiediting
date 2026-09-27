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
    for decision in decisions:
        cited = {
            shot_id
            for citation in decision.understanding.evidence
            for shot_id in citation.shot_ids
        }
        if not cited or not cited.issubset(set(decision.shot_ids)):
            raise InvalidMomentGrouping(
                "Moment understanding must cite only Shots in its group"
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
            understanding=decision.understanding.model_copy(
                update={
                    "audio_understanding": tuple(
                        audio
                        for member in members
                        for audio in member.evidence.understanding.audio_understanding
                    )
                }
            ),
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
