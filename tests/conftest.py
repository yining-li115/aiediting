from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aive.specs import (
    EditPlanSpec,
    Intent,
    RenderConstraints,
    Slot,
    SubtitleTrack,
    TimelineSpec,
    VideoClip,
    VideoTrack,
)

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def offline_stack(monkeypatch):
    """Pin every role to its offline implementation for the whole suite.

    ACTIVE_STACK points at real vendors, and a test that silently starts
    calling one would need a key, cost money, and fail differently depending
    on whose machine it runs on. Tests that want a vendor adapter construct it
    explicitly with a mock transport.
    """
    from aive.roles import Role

    for role in Role:
        default = "passthrough" if role is Role.CRITIC else "mock"
        monkeypatch.setenv(f"AIVE_ROLE_{role.name}_MODEL", default)
    monkeypatch.delenv("AIVE_ROLE_DIRECTOR_FALLBACK_MODELS", raising=False)

    # Tests must not write into the real call log: a log that mixes fixture
    # traffic with real runs is useless for diagnosing a real run.
    monkeypatch.setenv("AIVE_LOG_CALLS", "0")


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    """Give each test its own storage root.

    Sharing one would let tests see each other's uploads, and a test that
    writes into the developer's real storage is a test that can destroy work.
    """
    from aive.storage import LocalStorage, set_storage

    set_storage(LocalStorage(tmp_path / "storage"))
    yield
    set_storage(None)


@pytest.fixture
def plan() -> EditPlanSpec:
    spec = EditPlanSpec(
        project_id="proj_test",
        intent=Intent(target_duration=6.0, duration_tolerance=0.15),
        slots=[
            Slot(slot_id="slot_01", order=0, semantic_role="opening_environment",
                 shot_type="wide", target_duration=1.6),
            Slot(slot_id="slot_02", order=1, semantic_role="person_activity",
                 shot_type="medium", target_duration=1.6),
            Slot(slot_id="slot_03", order=2, semantic_role="detail",
                 shot_type="close_up", target_duration=2.8),
        ],
    )
    spec.rhythm.shot_duration_pattern = [s.target_duration for s in spec.slots]
    return spec


def _clip(index: int, start: float, end: float, speed: float = 1.0) -> VideoClip:
    return VideoClip(
        clip_id=f"c_{index:03d}",
        slot_id=f"slot_{index:02d}",
        source_id=f"asset_{index:03d}",
        source_in=1.0,
        source_out=1.0 + (end - start) * speed,
        timeline_start=start,
        timeline_end=end,
        speed=speed,
    )


@pytest.fixture
def timeline() -> TimelineSpec:
    return TimelineSpec(
        timeline_id="tl_test",
        plan_id=None,
        project_id="proj_test",
        duration=6.0,
        tracks=[
            VideoTrack(
                clips=[
                    _clip(1, 0.0, 1.6),
                    _clip(2, 1.6, 3.2, speed=1.5),
                    _clip(3, 3.2, 6.0),
                ]
            ),
            SubtitleTrack(),
        ],
        render_constraints=RenderConstraints(),
    )


@pytest.fixture(scope="session")
def media_dir(tmp_path_factory) -> Path:
    """Three short synthetic clips, generated once per test session."""
    if not HAS_FFMPEG:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media")
    for index, color in enumerate(["red", "green", "blue"], start=1):
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c={color}:s=360x640:r=30:d=8",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=8",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "34",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "64k", "-ac", "2",
                str(out / f"asset_{index:03d}.mp4"),
            ],
            check=True,
            capture_output=True,
        )
    return out
