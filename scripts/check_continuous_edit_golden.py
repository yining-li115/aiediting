#!/usr/bin/env python3
"""Run the real-model Continuous Editing Golden Run in an isolated project.

This is intentionally not part of the ordinary pytest suite: it calls the
configured Director provider and therefore consumes external quota.  All
project state, media, revisions, and previews are written below one isolated
work directory so the developer database is never touched.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "packages"))

from aive.agent.conversation_application import ConversationApplication  # noqa: E402
from aive.agent.conversation_repository import ConversationRepository  # noqa: E402
from aive.agent.conversation_workflow import ConversationWorkflow  # noqa: E402
from aive.agent.harness import VideoAgentHarness  # noqa: E402
from aive.contracts.conversation import (  # noqa: E402
    AgentTurn,
    CreateAgentTurnRequest,
    CreateConversationRequest,
    WorkspaceSelection,
)
from aive.contracts.project_doc import (  # noqa: E402
    AssetRef,
    AudioClip,
    AudioTrack,
    DependencyRef,
    DependencySet,
    FrameRange,
    OutputSettings,
    ProjectDoc,
    ProjectIntent,
    Rational,
    SourceRange,
    Timeline,
    VideoClip,
    VideoTrack,
)
from aive.contracts.rendering import CreateRenderRequest  # noqa: E402
from aive.db.analysis_repository import (  # noqa: E402
    SqlAlchemyMediaAnalysisRepository,
)
from aive.db.conversation_repository import (  # noqa: E402
    SqlAlchemyConversationRepository,
)
from aive.db.media_repository import SqlAlchemyMediaAssetRepository  # noqa: E402
from aive.db.models import (  # noqa: E402
    Asset,
    Base,
    MomentRow,
    Project,
    ProjectRevisionRow,
)
from aive.db.project_repository import SqlAlchemyRevisionRepository  # noqa: E402
from aive.db.render_repository import SqlAlchemyRenderRepository  # noqa: E402
from aive.jobs import JobRunner  # noqa: E402
from aive.project.application import ProjectRevisionApplication  # noqa: E402
from aive.project.capabilities import UnconfiguredCapabilityValidator  # noqa: E402
from aive.project.hashing import canonical_model_hash  # noqa: E402
from aive.providers import Role, get_provider  # noqa: E402
from aive.providers.base import CostRecord, set_cost_sink  # noqa: E402
from aive.renderer.ffmpeg import probe_duration  # noqa: E402
from aive.rendering.application import RevisionRenderApplication  # noqa: E402
from aive.rendering.ffmpeg import ProjectDocFFmpegRenderer  # noqa: E402
from aive.storage import LocalStorage  # noqa: E402


PROJECT_ID = "project_continuous_golden"
INITIAL_REVISION_ID = "revision_golden_initial"
FPS = 30
NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


@dataclass(frozen=True)
class GoldenStack:
    conversations: ConversationApplication
    conversation_repository: ConversationRepository
    workflow: ConversationWorkflow
    projects: ProjectRevisionApplication
    rendering: RevisionRenderApplication
    jobs: JobRunner
    initial_document: ProjectDoc


def _require_media_tools() -> None:
    missing = [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]
    if missing:
        raise RuntimeError(f"missing required media tools: {', '.join(missing)}")


def _make_source(path: Path, *, color: str, frequency: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s=160x284:r={FPS}:d=2",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={frequency}:sample_rate=48000:d=2",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "30",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "64k",
            "-ac",
            "2",
            str(path),
        ],
        check=True,
        capture_output=True,
    )


def _initial_document() -> ProjectDoc:
    visual_clips = tuple(
        VideoClip(
            clip_id=f"clip_visual_{index}",
            asset_id=f"asset_{index}",
            source=SourceRange(start=0, end=2_000, timescale=1_000),
            output=FrameRange(start_frame=(index - 1) * 60, duration_frames=60),
        )
        for index in range(1, 4)
    )
    audio_clips = tuple(
        AudioClip(
            clip_id=f"clip_audio_{index}",
            asset_id=f"asset_{index}",
            source=SourceRange(start=0, end=2_000, timescale=1_000),
            output=FrameRange(start_frame=(index - 1) * 60, duration_frames=60),
        )
        for index in range(1, 4)
    )
    return ProjectDoc(
        project_id=PROJECT_ID,
        revision_id=INITIAL_REVISION_ID,
        intent=ProjectIntent(
            task_mode="autonomous",
            target_duration_frames=180,
            prompt="Create a concise three-shot investor-demo sequence",
        ),
        assets=tuple(
            AssetRef(asset_id=f"asset_{index}", kind="video")
            for index in range(1, 4)
        ),
        timeline=Timeline(
            duration_frames=180,
            output=OutputSettings(
                width=160,
                height=284,
                fps=Rational(num=FPS, den=1),
            ),
            tracks=(
                VideoTrack(
                    track_id="track_primary_video",
                    role="primary",
                    clips=visual_clips,
                ),
                AudioTrack(
                    track_id="track_source_audio",
                    role="source",
                    clips=audio_clips,
                ),
            ),
        ),
        dependencies=DependencySet(
            renderer=DependencyRef(name="ffmpeg", version="1.0.0")
        ),
    )


def _build_stack(workdir: Path, costs: list[CostRecord]) -> GoldenStack:
    media_dir = workdir / "source"
    storage = LocalStorage(workdir / "storage")
    sources = (
        (1, "red", 330, "Opening red shot"),
        (2, "green", 550, "Middle green shot"),
        (3, "blue", 770, "Closing blue shot"),
    )
    stored: dict[int, tuple[str, int, str]] = {}
    for index, color, frequency, _ in sources:
        path = media_dir / f"asset_{index}.mp4"
        _make_source(path, color=color, frequency=frequency)
        key = f"{PROJECT_ID}/original/asset_{index}.mp4"
        record = storage.put(path, key)
        stored[index] = (key, record.size, record.sha256)

    engine = create_engine(f"sqlite:///{workdir / 'golden.db'}", future=True)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    document = _initial_document()
    with sessions.begin() as session:
        session.add(Project(id=PROJECT_ID, status="created"))
        session.add(
            ProjectRevisionRow(
                id=document.revision_id,
                project_id=PROJECT_ID,
                parent_revision_id=None,
                commit_id="commit_golden_initial",
                document_json=document.model_dump(mode="json"),
                document_hash=canonical_model_hash(document),
                provenance_json={"kind": "initial", "origin": "agent"},
                created_at=NOW,
            )
        )
        for index, _, _, semantic in sources:
            key, size, digest = stored[index]
            session.add(
                Asset(
                    id=f"asset_{index}",
                    project_id=PROJECT_ID,
                    storage_key=key,
                    proxy_key=key,
                    readiness="ready",
                    analysis_readiness="ready",
                    kind="video",
                    original_name=f"asset_{index}.mp4",
                    sha256=digest,
                    size_bytes=size,
                    duration=2.0,
                    width=160,
                    height=284,
                    fps=float(FPS),
                    has_audio=True,
                )
            )
            session.add(
                MomentRow(
                    id=f"moment_{index}",
                    project_id=PROJECT_ID,
                    asset_id=f"asset_{index}",
                    ordinal=0,
                    shot_ids_json=[f"shot_{index}"],
                    start_tick=0,
                    end_tick=2_000,
                    timescale=1_000,
                    analysis_window_count=1,
                    keyframe_keys_json=[],
                    technical_quality=0.95,
                    semantic_status="ready",
                    semantic=semantic,
                    semantic_role=(
                        "opening" if index == 1 else "closing" if index == 3 else "body"
                    ),
                    shot_type="wide",
                    camera_motion="static",
                    emotion_json=[],
                    is_usable=True,
                    transcript_segment_ids_json=[],
                    has_speech=False,
                    analyzed_at=NOW,
                )
            )
    with sessions.begin() as session:
        project = session.get(Project, PROJECT_ID)
        assert project is not None
        project.current_revision_id = INITIAL_REVISION_ID

    revision_repository = SqlAlchemyRevisionRepository(sessions)
    asset_repository = SqlAlchemyMediaAssetRepository(sessions)
    projects = ProjectRevisionApplication(
        revision_repository,
        UnconfiguredCapabilityValidator(),
        asset_authorizer=asset_repository,
    )
    rendering = RevisionRenderApplication(
        revisions=revision_repository,
        assets=asset_repository,
        artifacts=SqlAlchemyRenderRepository(sessions),
        storage=storage,
        renderer=ProjectDocFFmpegRenderer(),
    )
    conversation_repository = SqlAlchemyConversationRepository(sessions)
    provider = get_provider(Role.DIRECTOR, project_id=PROJECT_ID)
    conversations = ConversationApplication(
        repository=conversation_repository,
        projects=projects,
        analysis=SqlAlchemyMediaAnalysisRepository(sessions),
        rendering=rendering,
        harness=VideoAgentHarness(provider=provider),
    )
    jobs = JobRunner(sessions)
    set_cost_sink(costs.append)
    return GoldenStack(
        conversations=conversations,
        conversation_repository=conversation_repository,
        workflow=ConversationWorkflow(
            jobs=jobs,
            conversations=conversations,
            rendering=rendering,
        ),
        projects=projects,
        rendering=rendering,
        jobs=jobs,
        initial_document=document,
    )


def _timeline_state(document: ProjectDoc) -> dict[str, Any]:
    video = next(track for track in document.timeline.tracks if track.kind == "video")
    audio = next(track for track in document.timeline.tracks if track.kind == "audio")
    return {
        "duration_frames": document.timeline.duration_frames,
        "video": {
            clip.clip_id: {
                "asset": clip.asset_id,
                "source": (clip.source.start, clip.source.end),
                "output": (clip.output.start_frame, clip.output.duration_frames),
            }
            for clip in video.clips
        },
        "audio": {
            clip.clip_id: {
                "asset": clip.asset_id,
                "source": (clip.source.start, clip.source.end),
                "output": (clip.output.start_frame, clip.output.duration_frames),
            }
            for clip in audio.clips
        },
    }


def _assert_audio_video_sync(document: ProjectDoc) -> None:
    state = _timeline_state(document)
    video = state["video"]
    audio = state["audio"]
    assert len(video) == len(audio), "source audio and visual clip counts diverged"
    for visual_id, visual in video.items():
        index = visual_id.removeprefix("clip_visual_")
        matching = audio.get(f"clip_audio_{index}")
        assert matching is not None, f"missing source audio for {visual_id}"
        assert matching == visual, f"audio mapping diverged for {visual_id}"
    ordered = sorted(video.values(), key=lambda value: value["output"][0])
    cursor = 0
    for clip in ordered:
        start, duration = clip["output"]
        assert start == cursor, "primary video track contains a gap or overlap"
        cursor += duration
    assert cursor == state["duration_frames"], "timeline duration is not closed"


def _probe_streams(path: Path) -> set[str]:
    completed = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(completed.stdout)
    return {stream["codec_type"] for stream in payload["streams"]}


def _assert_preview(stack: GoldenStack, turn: AgentTurn | None, revision_id: str) -> dict:
    artifacts = stack.rendering.list_revision(PROJECT_ID, revision_id)
    ready = [artifact for artifact in artifacts if artifact.status == "ready"]
    assert len(ready) == 1, f"revision {revision_id} has no unique ready preview"
    artifact = ready[0]
    if turn is not None:
        assert turn.render_artifact_id == artifact.artifact_id
    delivery = stack.rendering.delivery(PROJECT_ID, artifact.artifact_id)
    assert delivery.path.exists() and delivery.path.stat().st_size > 0
    assert _probe_streams(delivery.path) == {"audio", "video"}
    document = stack.projects.get_revision(PROJECT_ID, revision_id).document
    expected_seconds = document.timeline.duration_frames / FPS
    actual_seconds = probe_duration(delivery.path)
    assert abs(actual_seconds - expected_seconds) <= 0.15, (
        f"preview duration {actual_seconds:.3f}s != timeline {expected_seconds:.3f}s"
    )
    return {
        "artifact_id": artifact.artifact_id,
        "path": str(delivery.path),
        "duration_seconds": actual_seconds,
        "streams": sorted(_probe_streams(delivery.path)),
    }


async def _render_initial(stack: GoldenStack) -> dict:
    ticket = stack.rendering.enqueue(
        CreateRenderRequest(
            request_id="request_golden_initial_preview",
            project_id=PROJECT_ID,
            revision_id=INITIAL_REVISION_ID,
            idempotency_key="render_golden_initial_preview",
            kind="preview",
            quality="proxy",
        )
    )
    await stack.jobs.run(ticket.artifact.job_id, stack.rendering.process)
    return _assert_preview(stack, None, INITIAL_REVISION_ID)


async def _run_turn(
    stack: GoldenStack,
    *,
    conversation_id: str,
    name: str,
    instruction: str,
    playhead_frame: int | None,
) -> AgentTurn:
    base = stack.projects.get_current_revision(PROJECT_ID)
    ticket = stack.conversations.enqueue_turn(
        CreateAgentTurnRequest(
            request_id=f"request_golden_{name}",
            project_id=PROJECT_ID,
            conversation_id=conversation_id,
            base_revision_id=base.document.revision_id,
            idempotency_key=f"turn_golden_{name}",
            instruction=instruction,
            workspace=WorkspaceSelection(
                revision_id=base.document.revision_id,
                playhead_frame=playhead_frame,
                preview_frame=playhead_frame,
            ),
        )
    )
    await stack.workflow.run(ticket.turn.job_id)
    turn = stack.conversations.get_turn(PROJECT_ID, ticket.turn.turn_id)
    assert turn is not None
    job = stack.jobs.get(turn.job_id)
    if turn.status != "completed":
        detail = turn.error or (job or {}).get("error") or "unknown turn failure"
        raise AssertionError(f"{name} turn failed: {detail}")
    return turn


def _assert_state(document: ProjectDoc, expected: dict[str, Any]) -> None:
    _assert_audio_video_sync(document)
    state = _timeline_state(document)
    assert state["duration_frames"] == expected["duration_frames"]
    assert state["video"] == expected["video"]
    assert state["audio"] == expected["audio"]


def _paired_expected(
    duration_frames: int,
    items: tuple[tuple[int, int, int, int], ...],
) -> dict[str, Any]:
    video: dict[str, Any] = {}
    audio: dict[str, Any] = {}
    for index, source_start, source_end, output_start in items:
        duration = round((source_end - source_start) * FPS / 1_000)
        value = {
            "asset": f"asset_{index}",
            "source": (source_start, source_end),
            "output": (output_start, duration),
        }
        video[f"clip_visual_{index}"] = value
        audio[f"clip_audio_{index}"] = value
    return {
        "duration_frames": duration_frames,
        "video": video,
        "audio": audio,
    }


async def _golden_run(workdir: Path) -> dict[str, Any]:
    costs: list[CostRecord] = []
    stack = _build_stack(workdir, costs)
    report: dict[str, Any] = {
        "project_id": PROJECT_ID,
        "workdir": str(workdir),
        "steps": [],
    }
    try:
        initial_preview = await _render_initial(stack)
        report["steps"].append(
            {
                "name": "initial",
                "revision_id": INITIAL_REVISION_ID,
                "preview": initial_preview,
            }
        )
        print(f"PASS initial: {INITIAL_REVISION_ID}", flush=True)
        conversation = stack.conversations.create_conversation(
            CreateConversationRequest(
                request_id="request_golden_conversation",
                project_id=PROJECT_ID,
            )
        ).conversation

        scenarios = (
            {
                "name": "trim",
                "instruction": (
                    "把播放位置所在的镜头从结尾裁掉 1 秒，并让后面的镜头和所有对应"
                    "音频向前补齐，保持音画同步。"
                ),
                "playhead": 75,
                "expected": _paired_expected(
                    150,
                    ((1, 0, 2_000, 0), (2, 0, 1_000, 60), (3, 0, 2_000, 90)),
                ),
            },
            {
                "name": "move",
                "instruction": (
                    "把第三个镜头移到最开头，其他镜头按原顺序依次后移，所有对应"
                    "音频一起移动，时间轴总长度不变。"
                ),
                "playhead": None,
                "expected": _paired_expected(
                    150,
                    ((3, 0, 2_000, 0), (1, 0, 2_000, 60), (2, 0, 1_000, 120)),
                ),
            },
            {
                "name": "delete",
                "instruction": (
                    "删除播放位置所在的整个镜头，让后面的镜头和对应音频自动前移，"
                    "保持音画同步。"
                ),
                "playhead": 75,
                "expected": _paired_expected(
                    90,
                    ((3, 0, 2_000, 0), (2, 0, 1_000, 60)),
                ),
            },
        )

        revision_ids = [INITIAL_REVISION_ID]
        for scenario in scenarios:
            prior_revision_id = revision_ids[-1]
            turn = await _run_turn(
                stack,
                conversation_id=conversation.conversation_id,
                name=scenario["name"],
                instruction=scenario["instruction"],
                playhead_frame=scenario["playhead"],
            )
            assert turn.patch is not None, f"{scenario['name']} did not produce a Patch"
            assert turn.result_revision_id is not None
            current = stack.projects.get_current_revision(PROJECT_ID)
            assert current.document.revision_id == turn.result_revision_id
            assert current.document.parent_revision_id == prior_revision_id
            _assert_state(current.document, scenario["expected"])
            preview = _assert_preview(stack, turn, current.document.revision_id)
            revision_ids.append(current.document.revision_id)
            report["steps"].append(
                {
                    "name": scenario["name"],
                    "base_revision_id": prior_revision_id,
                    "revision_id": current.document.revision_id,
                    "operations": [operation.op for operation in turn.patch.operations],
                    "tool_steps": [step.call.tool for step in turn.steps],
                    "preview": preview,
                }
            )
            print(
                f"PASS {scenario['name']}: {current.document.revision_id}",
                flush=True,
            )

        before_ambiguous = revision_ids[-1]
        ambiguous = await _run_turn(
            stack,
            conversation_id=conversation.conversation_id,
            name="ambiguous",
            instruction="把这个镜头调整一下。",
            playhead_frame=None,
        )
        assert ambiguous.patch is None
        assert ambiguous.result_revision_id is None
        assert ambiguous.render_job_id is None
        assert stack.projects.get_current_revision(PROJECT_ID).document.revision_id == (
            before_ambiguous
        )
        chat = stack.conversation_repository.get(PROJECT_ID, conversation.conversation_id)
        assert chat is not None
        response = next(
            message.content
            for message in reversed(chat.messages)
            if message.agent_turn_id == ambiguous.turn_id and message.role == "assistant"
        )
        assert "?" in response or "？" in response, (
            "ambiguous instruction did not produce a clarification question"
        )
        report["steps"].append(
            {
                "name": "ambiguous",
                "base_revision_id": before_ambiguous,
                "revision_id": None,
                "tool_steps": [step.call.tool for step in ambiguous.steps],
                "response": response,
            }
        )
        print("PASS ambiguous: clarification without Revision", flush=True)

        history = stack.projects.list_revision_history(PROJECT_ID)
        history_ids = [revision.document.revision_id for revision in history]
        # The fixture seeds its initial immutable row directly, without a
        # commit-attempt row.  Revision history intentionally lists committed
        # attempts, so it contains the three Agent commits; the initial row is
        # verified explicitly with get_revision below.
        assert history_ids == revision_ids[1:], (
            f"committed history {history_ids!r} != Agent revisions "
            f"{revision_ids[1:]!r}"
        )
        for revision_id in revision_ids:
            stack.projects.get_revision(PROJECT_ID, revision_id)
            _assert_preview(stack, None, revision_id)
        report["revision_chain"] = revision_ids
        report["cost_records"] = [record.as_dict() for record in costs]
        report["models_used"] = list(
            dict.fromkeys(record.model for record in costs if record.model)
        )
        return report
    finally:
        set_cost_sink(None)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workdir",
        type=Path,
        help="isolated output directory; defaults to a retained /tmp directory",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    _require_media_tools()
    workdir = (
        args.workdir.expanduser().resolve()
        if args.workdir
        else Path(tempfile.mkdtemp(prefix="aive-continuous-golden-"))
    )
    workdir.mkdir(parents=True, exist_ok=True)
    report_path = workdir / "golden-report.json"
    print(f"Golden Run workdir: {workdir}", flush=True)
    try:
        report = asyncio.run(_golden_run(workdir))
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}", flush=True)
        print(f"Artifacts retained at: {workdir}", flush=True)
        return 1
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    for step in report["steps"]:
        revision = step.get("revision_id") or "unchanged"
        print(f"PASS {step['name']}: {revision}", flush=True)
    print(f"PASS revision chain: {' -> '.join(report['revision_chain'])}", flush=True)
    print(f"Models used: {', '.join(report['models_used'])}", flush=True)
    print(f"Report: {report_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
