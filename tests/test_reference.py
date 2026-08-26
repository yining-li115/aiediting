"""Reference analysis - the foundation both reference modes share.

The copyright boundary is enforced here, not only documented: structure and
timing transfer, pixels and audio do not. Those tests are as important as the
ones that check the numbers.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from aive.reference import aggregate
from aive.specs.reference import ReferenceShot

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


def shots(scales: list[str], durations: list[float],
          transitions: list[str] | None = None) -> list[ReferenceShot]:
    out, cursor = [], 0.0
    for i, (scale, duration) in enumerate(zip(scales, durations)):
        out.append(
            ReferenceShot(
                index=i, start=cursor, end=cursor + duration,
                shot_type=scale,
                transition_out=(transitions or ["hard_cut"] * len(scales))[i],
            )
        )
        cursor += duration
    return out


# --- aggregation -----------------------------------------------------------


def test_duration_stats_describe_the_spread() -> None:
    stats = aggregate.duration_stats([0.5, 1.0, 1.0, 1.5, 4.0])
    assert stats.median == 1.0
    assert stats.mean == pytest.approx(1.6)
    assert stats.p90 > stats.median  # the long tail is visible


def test_pace_ladder_matches_the_planners() -> None:
    """A reference's pace and a plan's pace must mean the same thing, or
    'match this reference' cannot be expressed as a plan."""
    from aive.planner.plan import _pace_for

    for duration in (0.6, 1.1, 1.6, 3.0):
        assert aggregate.pace_of(duration) == _pace_for(duration)


def test_transition_density_counts_only_effects() -> None:
    assert aggregate.transition_density(
        shots(["wide"] * 5, [1.0] * 5, ["hard_cut"] * 5)
    ) == 0.0
    assert aggregate.transition_density(
        shots(["wide"] * 5, [1.0] * 5,
              ["dissolve", "hard_cut", "dissolve", "hard_cut", "hard_cut"])
    ) == 0.5


def test_bigrams_capture_grammar_not_sequence() -> None:
    """Sequences from different videos cannot be averaged; transition
    probabilities can, and that is what editing grammar is."""
    grammar = aggregate.bigrams(["wide", "medium", "close_up", "medium", "wide"])
    assert grammar["wide->medium"] == pytest.approx(0.25)
    assert sum(grammar.values()) == pytest.approx(1.0)
    assert aggregate.bigrams(["wide"]) == {}


def test_style_tags_are_derived_not_invented() -> None:
    fast = aggregate.style_tags(shots(["wide"] * 3, [0.8] * 3), 0.8, 0.02)
    slow = aggregate.style_tags(shots(["wide"] * 3, [3.0] * 3), 3.0, 0.4)
    assert "energetic" in fast and "minimal" in fast
    assert "cinematic" in slow and "minimal" not in slow


# --- the copyright boundary ------------------------------------------------


@requires_ffmpeg
def test_reference_audio_is_measured_then_deleted(tmp_path: Path) -> None:
    """Tempo and beat positions transfer; the track does not.

    The extracted audio must not survive the call - a file left behind is a
    file something can later decide to use.
    """
    import glob
    import tempfile

    from aive.reference import audio

    video = tmp_path / "ref.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=160x284:r=30:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100:d=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(video),
        ],
        check=True, capture_output=True,
    )

    before = set(glob.glob(str(Path(tempfile.gettempdir()) / "*.wav")))
    music = audio.analyse(video, [1.0, 2.0, 3.0])
    after = set(glob.glob(str(Path(tempfile.gettempdir()) / "*.wav")))

    assert after == before, "extracted reference audio was left on disk"
    # Only abstract features come back.
    assert set(music.model_dump()) == {
        "bpm", "beat_count", "beat_sync_strength",
        "cut_to_beat_offset_ms", "has_speech",
    }


def test_analysis_carries_no_pixels_or_audio() -> None:
    """The whole point of ReferenceAnalysis: grammar, not content.

    A field holding a frame path or an audio key would be an invitation to
    reuse the reference's own material in an output.
    """
    from aive.specs.reference import ReferenceAnalysis

    fields = set(ReferenceAnalysis.model_fields)
    forbidden = {"frames", "keyframe_keys", "audio_key", "music_key",
                 "storage_key", "thumbnail", "waveform"}
    assert not (fields & forbidden)

    shot_fields = set(ReferenceShot.model_fields)
    assert not (shot_fields & forbidden)


@requires_ffmpeg
def test_reference_keyframes_are_removed_after_analysis(tmp_path, monkeypatch):
    """Frames exist to ask a model a question, and not for a moment longer."""
    import asyncio

    import aive.db.session as session_mod
    from aive.config import settings
    from aive.reference import add_reference, analyze_reference
    from aive.storage import LocalStorage, get_storage, set_storage

    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path/'r.db'}")
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)
    session_mod.init_db()
    set_storage(LocalStorage(tmp_path / "storage"))

    video = tmp_path / "ref.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=160x284:r=30:d=6",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(video),
        ],
        check=True, capture_output=True,
    )

    reference_id, key = add_reference(video, "proj_ref")
    asyncio.run(analyze_reference(reference_id))

    storage = get_storage()
    leftover = [k for k in storage.iter_keys("proj_ref/") if "keyframe" in k]
    assert leftover == [], f"reference frames left in storage: {leftover}"
    # The upload itself is kept - the user gave it to us.
    assert storage.exists(key)
    set_storage(None)


@requires_ffmpeg
def test_analysis_produces_real_structure(tmp_path, monkeypatch) -> None:
    import asyncio

    import aive.db.session as session_mod
    from aive.config import settings
    from aive.reference import add_reference, analyze_reference
    from aive.storage import LocalStorage, set_storage

    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path/'r2.db'}")
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)
    session_mod.init_db()
    set_storage(LocalStorage(tmp_path / "storage"))

    # Three visually distinct sections so the detector has real cuts to find.
    parts = []
    for index, colour in enumerate(["red", "green", "blue"]):
        part = tmp_path / f"p{index}.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c={colour}:s=160x284:r=30:d=2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                str(part),
            ],
            check=True, capture_output=True,
        )
        parts.append(part)
    listing = tmp_path / "list.txt"
    listing.write_text("".join(f"file '{p}'\n" for p in parts))
    video = tmp_path / "ref.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy", str(video),
        ],
        check=True, capture_output=True,
    )

    reference_id, _ = add_reference(video, "proj_ref2")
    analysis = asyncio.run(analyze_reference(reference_id))

    assert analysis.shot_count >= 3
    assert analysis.duration == pytest.approx(6.0, abs=0.3)
    assert len(analysis.shot_scale_sequence) == analysis.shot_count
    assert analysis.shot_duration.median > 0
    # Every shot got labelled, and the failure count says so honestly.
    assert analysis.provenance.extra.get("unlabelled_shots") == 0
    set_storage(None)
