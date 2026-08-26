"""Photos as material.

A phone camera roll mixes photos and video, so the failure mode that matters
most here is not "photos are unsupported" - it is "photos are accepted and
then quietly disappear". Several of these tests exist specifically to catch a
silent drop.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aive.material.quality import measure_photo
from aive.material.usability import judge
from aive.matcher import fit, score_pair
from aive.specs.edit_plan import Intent, Slot
from aive.specs.segment import MaterialSegment

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


@pytest.fixture
def photo(tmp_path: Path) -> Path:
    """A still with real texture, not a flat colour."""
    path = tmp_path / "photo.jpg"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=540x960",
            "-frames:v", "1", str(path),
        ],
        check=True, capture_output=True,
    )
    return path


def make_photo_segment(quality: float = 0.85) -> MaterialSegment:
    return MaterialSegment(
        segment_id="p1", project_id="p", source_id="a1", kind="photo",
        start=0.0, end=3.0, semantic_role="detail", shot_type="close_up",
        camera_motion="static", technical_quality=quality,
    )


# --- the silent-drop failures ----------------------------------------------


@requires_ffmpeg
def test_photos_are_not_filtered_out_of_an_import(tmp_path, monkeypatch, photo):
    """A camera roll export must not lose its photos on the way in."""
    import aive.db.session as session_mod
    from aive.config import settings
    from aive.material import ingest
    from aive.storage import LocalStorage, set_storage

    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path/'p.db'}")
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)
    session_mod.init_db()
    set_storage(LocalStorage(tmp_path / "storage"))

    folder = tmp_path / "roll"
    folder.mkdir()
    shutil.copy(photo, folder / "a.jpg")
    shutil.copy(photo, folder / "b.png")
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=160x284:r=30:d=2",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(folder / "clip.mp4"),
        ],
        check=True, capture_output=True,
    )

    result = ingest(folder, "proj_roll")
    assert result.photos == 2
    assert result.videos == 1
    set_storage(None)


@requires_ffmpeg
def test_unsupported_files_are_reported_not_dropped(tmp_path, monkeypatch, photo):
    """Reporting "30 imported" while dropping 15 files is data loss wearing a
    success message."""
    import aive.db.session as session_mod
    from aive.config import settings
    from aive.material import ingest
    from aive.storage import LocalStorage, set_storage

    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path/'p2.db'}")
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)
    session_mod.init_db()
    set_storage(LocalStorage(tmp_path / "storage"))

    folder = tmp_path / "roll2"
    folder.mkdir()
    shutil.copy(photo, folder / "a.jpg")
    (folder / "notes.txt").write_text("not media")
    (folder / "Thumbs.db").write_text("junk")

    result = ingest(folder, "proj_roll2")
    skipped = {p.name for p, _ in result.skipped}
    assert skipped == {"notes.txt", "Thumbs.db"}
    set_storage(None)


@requires_ffmpeg
def test_photo_quality_excludes_metrics_that_do_not_apply(photo) -> None:
    """A still has no camera to be steady. Scoring that as zero would drag
    every photo below the usability floor and delete the user's photos."""
    metrics = measure_photo(str(photo))

    assert metrics.stability_score is None
    assert metrics.motion_quality is None
    assert judge(3.0, metrics, "photo") == (True, None)

    # The same numbers under the video weighting, with the two inapplicable
    # metrics counted as failures:
    as_video = 0.35 * metrics.sharpness + 0.25 * metrics.exposure_score
    assert as_video < metrics.technical_quality, (
        "video weights systematically under-score photos, which loses them "
        "the S_quality term in matching even when they survive the floor"
    )


def test_duration_floor_does_not_apply_to_stills() -> None:
    """A photo has no length to be too short - applying the video floor would
    reject every photo for a property it cannot have."""
    from aive.material.quality import QualityMetrics

    metrics = QualityMetrics(0.8, 0.9, None, None, 0.85, 0.0)
    assert judge(0.1, metrics, "photo") == (True, None)
    assert judge(0.1, metrics, "video")[0] is False


# --- placement --------------------------------------------------------------


def test_a_photo_fits_any_slot_length() -> None:
    """A still holds for exactly as long as asked, which makes it unusually
    easy to place. That is correct, not something to compensate for."""
    short = Slot(slot_id="s1", order=0, target_duration=0.8,
                 duration_range=(0.6, 1.0))
    long = Slot(slot_id="s2", order=1, target_duration=4.0,
                duration_range=(3.0, 5.0))
    segment = make_photo_segment()
    intent = Intent()

    assert score_pair(short, segment, intent).total == pytest.approx(
        score_pair(long, segment, intent).total
    )


def test_fitting_a_photo_needs_no_trim_or_speed_change() -> None:
    slot = Slot(slot_id="s1", order=0, target_duration=2.5)
    fitted = fit(slot, make_photo_segment())

    assert fitted.speed == 1.0
    assert fitted.timeline_duration == pytest.approx(2.5)
    assert fitted.source_out - fitted.source_in == pytest.approx(2.5)
    # Still recorded, so the log explains every clip in the timeline.
    assert any(r.action == "extend" for r in fitted.records)


def test_same_photo_can_fill_slots_of_different_lengths() -> None:
    segment = make_photo_segment()
    lengths = [
        fit(Slot(slot_id=f"s{i}", order=i, target_duration=d), segment)
        .timeline_duration
        for i, d in enumerate([1.0, 2.5, 4.0])
    ]
    assert lengths == pytest.approx([1.0, 2.5, 4.0])


# --- rendering --------------------------------------------------------------


def test_compiler_loops_a_still_instead_of_seeking_into_it(tmp_path) -> None:
    from aive.renderer.compiler import clip_command, is_photo
    from aive.renderer.profiles import resolve_profile
    from aive.specs.timeline import RenderConstraints, VideoClip

    photo_path = tmp_path / "still.jpg"
    photo_path.write_bytes(b"fake")
    assert is_photo(photo_path)

    clip = VideoClip(
        clip_id="c1", source_id="a1", source_in=0.0, source_out=2.0,
        timeline_start=0.0, timeline_end=2.0, source_audio_gain=1.0,
    )
    cmd = clip_command(
        clip, photo_path, tmp_path / "out.mp4",
        resolve_profile(RenderConstraints(), "proxy"),
        source_has_audio=True,
    )

    assert "-loop" in cmd and cmd[cmd.index("-loop") + 1] == "1"
    assert "-ss" not in cmd  # nothing to seek into
    # A still carries no audio to keep, whatever the clip's gain says.
    assert "anullsrc" in " ".join(cmd)


@requires_ffmpeg
def test_a_mixed_timeline_renders(tmp_path, photo) -> None:
    from aive.renderer import FFmpegRenderer, probe_duration
    from aive.specs.timeline import (
        SubtitleTrack, TimelineSpec, VideoClip, VideoTrack,
    )

    video = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=160x284:r=30:d=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(video),
        ],
        check=True, capture_output=True,
    )

    timeline = TimelineSpec(
        project_id="p", duration=4.0,
        tracks=[
            VideoTrack(clips=[
                VideoClip(clip_id="c1", source_id="photo", source_in=0.0,
                          source_out=1.5, timeline_start=0.0, timeline_end=1.5),
                VideoClip(clip_id="c2", source_id="video", source_in=0.5,
                          source_out=2.5, timeline_start=1.5, timeline_end=3.5),
                VideoClip(clip_id="c3", source_id="photo", source_in=0.0,
                          source_out=0.5, timeline_start=3.5, timeline_end=4.0),
            ]),
            SubtitleTrack(),
        ],
    )
    out = tmp_path / "mixed.mp4"
    FFmpegRenderer().render(
        timeline, "proxy", out, {"photo": photo, "video": video}
    )
    assert probe_duration(out) == pytest.approx(4.0, abs=0.15)
