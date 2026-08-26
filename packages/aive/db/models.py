"""SQLAlchemy models for every table in the plan.

SQLite for now, PostgreSQL before closed beta. No dialect-specific SQL is used
anywhere so that migration stays a configuration change: JSON columns go
through SQLAlchemy's portable ``JSON`` type, ids are strings, and timestamps
are timezone-aware UTC.

Full specs are stored as JSON blobs; the handful of fields needed for
querying and sorting are lifted out into real columns.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# --- core --------------------------------------------------------------------


class User(Base, TimestampMixin):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # dev | expert | consumer - expert and consumer preferences are never mixed.
    kind: Mapped[str] = mapped_column(String(32), default="consumer")


class Project(Base, TimestampMixin):
    __tablename__ = "projects"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(64), ForeignKey("users.id"))
    mode: Mapped[str] = mapped_column(String(32), default="auto")
    prompt: Mapped[str | None] = mapped_column(Text)
    intent_json: Mapped[dict | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(32), default="created")


class Asset(Base, TimestampMixin):
    """One uploaded source file.

    Storage keys, not paths: the row must stay valid when the backend moves
    from a local disk to a bucket. Resolving a key to something ffmpeg can
    open is the storage layer's job.
    """

    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"))
    storage_key: Mapped[str] = mapped_column(Text)
    proxy_key: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="video")
    original_name: Mapped[str | None] = mapped_column(Text)
    # Content hash: identifies duplicate uploads, which are common when people
    # re-import the same footage, and lets a re-ingest be a no-op.
    sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    duration: Mapped[float] = mapped_column(Float)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    fps: Mapped[float] = mapped_column(Float)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=True)


class Segment(Base):
    __tablename__ = "segments"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"))
    asset_id: Mapped[str] = mapped_column(String(64), ForeignKey("assets.id"))
    kind: Mapped[str] = mapped_column(String(16), default="video")
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    duration: Mapped[float] = mapped_column(Float)

    semantic: Mapped[str | None] = mapped_column(Text)
    semantic_role: Mapped[str] = mapped_column(String(64), default="unknown")
    shot_type: Mapped[str] = mapped_column(String(32), default="unknown")
    camera_motion: Mapped[str] = mapped_column(String(32), default="unknown")
    people: Mapped[int | None] = mapped_column(Integer)

    technical_quality: Mapped[float | None] = mapped_column(Float)
    sharpness: Mapped[float | None] = mapped_column(Float)
    exposure_score: Mapped[float | None] = mapped_column(Float)
    stability_score: Mapped[float | None] = mapped_column(Float)
    motion_quality: Mapped[float | None] = mapped_column(Float)
    composition_score: Mapped[float | None] = mapped_column(Float)
    interestingness: Mapped[float | None] = mapped_column(Float)

    emotion_json: Mapped[list | None] = mapped_column(JSON)
    has_speech: Mapped[bool] = mapped_column(Boolean, default=False)
    asr_text: Mapped[str | None] = mapped_column(Text)
    keyframe_keys_json: Mapped[list | None] = mapped_column(JSON)

    # float32 vector, brute-force cosine is fine at a few hundred segments.
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_dim: Mapped[int | None] = mapped_column(Integer)

    is_usable: Mapped[bool] = mapped_column(Boolean, default=True)
    reject_reason: Mapped[str | None] = mapped_column(String(128))


class Reference(Base, TimestampMixin):
    __tablename__ = "references_"  # "references" is reserved in some dialects
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"))
    storage_key: Mapped[str] = mapped_column(Text)
    duration: Mapped[float | None] = mapped_column(Float)
    analysis_json: Mapped[dict | None] = mapped_column(JSON)
    plan_id: Mapped[str | None] = mapped_column(String(64))


class MusicTrack(Base, TimestampMixin):
    __tablename__ = "music_tracks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    storage_key: Mapped[str] = mapped_column(Text)
    # Null for library tracks every project can use; set for a track uploaded
    # for one project only.
    project_id: Mapped[str | None] = mapped_column(String(64), index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    # Licence is a required field, not documentation: nothing without a clear
    # licence may end up in a rendered video.
    license: Mapped[str] = mapped_column(String(64))
    bpm: Mapped[float | None] = mapped_column(Float)
    beat_times_json: Mapped[list | None] = mapped_column(JSON)
    mood_tags_json: Mapped[list | None] = mapped_column(JSON)
    duration: Mapped[float | None] = mapped_column(Float)


# --- specs -------------------------------------------------------------------


class EditPlanSpecRow(Base, TimestampMixin):
    __tablename__ = "edit_plan_specs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"))
    mode: Mapped[str] = mapped_column(String(32), default="auto")
    candidate_index: Mapped[int] = mapped_column(Integer, default=0)
    reference_id: Mapped[str | None] = mapped_column(String(64))
    spec_json: Mapped[dict] = mapped_column(JSON)


class TimelineSpecRow(Base, TimestampMixin):
    __tablename__ = "timeline_specs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"))
    plan_id: Mapped[str | None] = mapped_column(String(64))
    duration: Mapped[float] = mapped_column(Float)
    spec_json: Mapped[dict] = mapped_column(JSON)
    is_feasible: Mapped[bool] = mapped_column(Boolean, default=True)


class CandidateScore(Base, TimestampMixin):
    __tablename__ = "candidate_scores"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timeline_id: Mapped[str] = mapped_column(String(64), ForeignKey("timeline_specs.id"))
    stage: Mapped[str] = mapped_column(String(32))  # structural | video
    scorer: Mapped[str] = mapped_column(String(128))
    overall: Mapped[float | None] = mapped_column(Float)
    dimensions_json: Mapped[dict | None] = mapped_column(JSON)


class Render(Base, TimestampMixin):
    __tablename__ = "renders"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    timeline_id: Mapped[str] = mapped_column(String(64), ForeignKey("timeline_specs.id"))
    quality: Mapped[str] = mapped_column(String(16))  # proxy | hq
    storage_key: Mapped[str | None] = mapped_column(Text)
    duration_sec: Mapped[float | None] = mapped_column(Float)
    render_time_ms: Mapped[int | None] = mapped_column(Integer)
    file_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    error: Mapped[str | None] = mapped_column(Text)


# --- operations --------------------------------------------------------------


class Job(Base, TimestampMixin):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    stage: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    result_json: Mapped[dict | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ApiCall(Base, TimestampMixin):
    """Cost observability has to exist from the first line of code.

    Retro-fitting it means the first real cost surprise arrives with no data
    to explain it. ``role`` is what makes per-role budgeting possible.
    """

    __tablename__ = "api_calls"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    seconds_processed: Mapped[float | None] = mapped_column(Float)
    cost_estimate: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    retries: Mapped[int] = mapped_column(Integer, default=0)


# --- preference --------------------------------------------------------------


class PreferencePair(Base, TimestampMixin):
    __tablename__ = "preference_pairs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(32), default="auto")
    reference_id: Mapped[str | None] = mapped_column(String(64))
    # Both layers are recorded: humans watch the render, the model reads the
    # structured timeline. Either one alone loses half the traceability.
    timeline_a: Mapped[str] = mapped_column(String(64))
    timeline_b: Mapped[str] = mapped_column(String(64))
    render_a: Mapped[str | None] = mapped_column(String(64))
    render_b: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(32), default="synthetic")


class PreferenceVote(Base, TimestampMixin):
    """One row per annotator per pair - the single source of truth.

    Aggregates are always computed, never stored. Storing only
    ``{A_votes, B_votes}`` would permanently discard disagreement, and
    disagreement between expert and consumer raters is signal about taste
    clusters, not noise to be cleaned away.
    """

    __tablename__ = "preference_votes"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    pair_id: Mapped[str] = mapped_column(String(64), ForeignKey("preference_pairs.id"))
    annotator_id: Mapped[str] = mapped_column(String(64))
    annotator_type: Mapped[str] = mapped_column(String(32))  # expert | consumer
    choice: Mapped[str] = mapped_column(String(8))  # A | B | tie
    reasons_json: Mapped[list | None] = mapped_column(JSON)
    would_post: Mapped[str | None] = mapped_column(String(32))
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class UserCorrection(Base, TimestampMixin):
    __tablename__ = "user_corrections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64))
    timeline_id: Mapped[str | None] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(64))
    target_slot_id: Mapped[str | None] = mapped_column(String(64))
    before_json: Mapped[dict | None] = mapped_column(JSON)
    after_json: Mapped[dict | None] = mapped_column(JSON)


# --- interaction layer -------------------------------------------------------


class ProjectVersionRow(Base, TimestampMixin):
    __tablename__ = "project_versions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64), ForeignKey("projects.id"))
    parent_version_id: Mapped[str | None] = mapped_column(String(64))
    turn_id: Mapped[str | None] = mapped_column(String(64))
    user_instruction: Mapped[str | None] = mapped_column(Text)
    plan_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    timeline_snapshot_id: Mapped[str | None] = mapped_column(String(64))
    preview_render_id: Mapped[str | None] = mapped_column(String(64))
    accepted: Mapped[bool | None] = mapped_column(Boolean)


class EditOperationRow(Base, TimestampMixin):
    __tablename__ = "edit_operations"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    version_id: Mapped[str | None] = mapped_column(String(64))
    turn_id: Mapped[str | None] = mapped_column(String(64))
    seq: Mapped[int] = mapped_column(Integer, default=0)
    operation: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[dict | None] = mapped_column(JSON)
    scope_json: Mapped[dict | None] = mapped_column(JSON)
    # chat | editor | agent_plan | system. The chat/editor split is exactly the
    # Manual-vs-Agent Edit Ratio metric, so it must be recorded honestly.
    origin: Mapped[str] = mapped_column(String(32), default="chat")
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)


class AgentTurn(Base, TimestampMixin):
    """Per-turn audit trail. Every agent metric is computed from this table."""

    __tablename__ = "agent_turns"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64))
    version_before: Mapped[str | None] = mapped_column(String(64))
    version_after: Mapped[str | None] = mapped_column(String(64))
    instruction: Mapped[str] = mapped_column(Text)
    route: Mapped[str] = mapped_column(String(8))  # fast | deep
    resolved_scope_json: Mapped[dict | None] = mapped_column(JSON)
    model: Mapped[str | None] = mapped_column(String(128))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    op_count: Mapped[int] = mapped_column(Integer, default=0)
    precheck_violations_json: Mapped[list | None] = mapped_column(JSON)
    # applied | rejected | rolled_back | undone
    outcome: Mapped[str] = mapped_column(String(32), default="applied")


class ElementLock(Base, TimestampMixin):
    __tablename__ = "element_locks"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(64))
    element_type: Mapped[str] = mapped_column(String(32))
    element_id: Mapped[str] = mapped_column(String(64))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
