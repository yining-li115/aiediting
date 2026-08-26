"""Music: library, per-project uploads, and beat-aligned cutting."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aive.matcher.beats import BeatGrid, align, build_grid, offsets_to_beats
from aive.specs.edit_plan import EditPlanSpec, Intent, Section, Slot, Story
from aive.specs.enums import MIN_CLIP_DURATION
from aive.specs.timeline import VideoClip

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def make_plan(count: int, shot: float = 2.0, sync: float = 1.0) -> EditPlanSpec:
    slots = [
        Slot(
            slot_id=f"slot_{i + 1:02d}",
            order=i,
            section="opening" if i == 0 else "body",
            target_duration=shot,
            duration_range=(shot * 0.7, shot * 1.4),
        )
        for i in range(count)
    ]
    plan = EditPlanSpec(
        project_id="p",
        intent=Intent(target_duration=shot * count),
        slots=slots,
        story=Story(
            sections=[
                Section(name="opening", target_duration=shot, slot_ids=["slot_01"]),
                Section(
                    name="body",
                    target_duration=shot * (count - 1),
                    slot_ids=[s.slot_id for s in slots[1:]],
                ),
            ]
        ),
    )
    plan.rhythm.music_sync_strength = sync
    return plan


def make_clips(count: int, shot: float = 2.0) -> list[VideoClip]:
    clips = []
    cursor = 0.0
    for i in range(count):
        clips.append(
            VideoClip(
                clip_id=f"c_{i:03d}",
                slot_id=f"slot_{i + 1:02d}",
                source_id=f"a_{i:03d}",
                source_in=1.0,
                source_out=1.0 + shot,
                timeline_start=cursor,
                timeline_end=cursor + shot,
            )
        )
        cursor += shot
    return clips


def grid_at(bpm: float, count: int, offset: float = 0.0) -> BeatGrid:
    step = 60.0 / bpm
    return build_grid([offset + i * step for i in range(count)], 0.0, count * step)


# --- the grid --------------------------------------------------------------


def test_grid_uses_detected_beats_not_tempo_arithmetic() -> None:
    """Real beat grids drift; the interval must come from the data."""
    uneven = [0.557, 1.115, 1.649, 2.206, 2.763, 3.32, 3.855, 4.389]
    grid = build_grid(uneven, 0.0, 5.0)
    assert grid.times == uneven
    assert grid.interval == pytest.approx(0.557, abs=0.01)


def test_grid_is_shifted_into_timeline_time() -> None:
    """Beats are music-file times; cuts are timeline times."""
    grid = build_grid([10.0, 10.5, 11.0, 11.5], music_in=10.0, duration=2.0)
    assert grid.times == [0.0, 0.5, 1.0, 1.5]


def test_bars_are_every_fourth_beat() -> None:
    grid = grid_at(120, 9)
    assert grid.bar_times == pytest.approx([0.0, 2.0, 4.0])


# --- alignment -------------------------------------------------------------


def test_cuts_move_onto_the_beat() -> None:
    plan = make_plan(4, shot=2.0)
    clips = make_clips(4, shot=2.0)
    # 130 bpm: beats near but not on the 2.0s boundaries.
    grid = grid_at(130, 30)
    moved = align(clips, plan, grid)

    assert moved > 0
    offsets = offsets_to_beats(clips, grid)
    assert max(abs(o) for o in offsets) < 0.05


def test_zero_sync_strength_changes_nothing() -> None:
    """An edit that was never meant to sit on the grid must not be forced onto
    it."""
    plan = make_plan(4, sync=0.0)
    clips = make_clips(4)
    before = [(c.timeline_start, c.timeline_end) for c in clips]
    assert align(clips, plan, grid_at(130, 30)) == 0
    assert [(c.timeline_start, c.timeline_end) for c in clips] == before


def test_alignment_never_leaves_a_gap() -> None:
    plan = make_plan(6)
    clips = make_clips(6)
    align(clips, plan, grid_at(97, 60))
    for previous, current in zip(clips, clips[1:]):
        assert previous.timeline_end == pytest.approx(current.timeline_start, abs=1e-6)


def test_alignment_keeps_the_speed_invariant() -> None:
    """Changing a clip's length without changing source_out silently breaks
    the timeline, and the renderer would refuse it."""
    plan = make_plan(5)
    clips = make_clips(5)
    for clip in clips:
        clip.speed = 1.5
        clip.source_out = clip.source_in + clip.timeline_duration * 1.5
    align(clips, plan, grid_at(113, 60))
    for clip in clips:
        assert clip.source_duration == pytest.approx(
            clip.timeline_duration * clip.speed, abs=1e-3
        )


def test_snapping_respects_the_slots_duration_range() -> None:
    """Missing a beat is inaudible; a visibly wrong shot length is not."""
    plan = make_plan(5, shot=2.0)
    for slot in plan.slots:
        slot.duration_range = (1.95, 2.05)  # almost no room
    clips = make_clips(5, shot=2.0)
    align(clips, plan, grid_at(71, 60))  # beats far from the boundaries
    for clip in clips[:-1]:
        assert 1.9 <= clip.timeline_duration <= 2.1


def test_no_clip_falls_below_the_minimum_length() -> None:
    plan = make_plan(6, shot=0.6)
    clips = make_clips(6, shot=0.6)
    align(clips, plan, grid_at(150, 60))
    for clip in clips:
        assert clip.timeline_duration >= MIN_CLIP_DURATION - 1e-6


def test_last_clip_is_not_moved() -> None:
    """It defines the total length, and the plan asked for that length."""
    plan = make_plan(4)
    clips = make_clips(4)
    end_before = clips[-1].timeline_end
    align(clips, plan, grid_at(133, 60))
    assert clips[-1].timeline_end == pytest.approx(end_before, abs=0.5)


def test_empty_grid_is_a_no_op() -> None:
    plan = make_plan(3)
    clips = make_clips(3)
    assert align(clips, plan, BeatGrid(times=[], bar_times=[])) == 0


# --- library ---------------------------------------------------------------


@requires_ffmpeg
def test_uploaded_track_is_copied_into_storage(tmp_path, monkeypatch) -> None:
    import aive.db.session as session_mod
    from aive.config import settings
    from aive.music import register
    from aive.storage import get_storage

    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path/'m.db'}")
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)
    session_mod.init_db()

    source = tmp_path / "song.wav"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=2",
            "-c:a", "pcm_s16le", str(source),
        ],
        check=True, capture_output=True,
    )

    track = register(source, license="CC0-1.0", project_id="proj_1")
    source.unlink()

    assert track.storage_key.startswith("proj_1/music/")
    assert get_storage().open_local(track.storage_key).exists()
    assert track.license == "CC0-1.0"
