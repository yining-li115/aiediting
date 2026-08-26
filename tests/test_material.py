"""Material understanding.

The deterministic half is tested exhaustively because it decides what ever
reaches the candidate pool: a segmenter that silently drops footage, or a
quality metric that cannot tell a sharp shot from a blurred one, poisons every
later stage while looking like it works.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from aive.material.quality import exposure_of, measure, sharpness_of
from aive.material.split import apply_length_rules
from aive.material.usability import judge
from aive.material.quality import QualityMetrics

requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not installed"
)


# --- shot length rules -----------------------------------------------------


def test_long_shot_is_split_into_usable_pieces() -> None:
    """A 30s continuous take is not one candidate; the matcher needs choices."""
    out = apply_length_rules([(0.0, 30.0)])
    assert len(out) > 1
    assert all(e - s <= 6.0 + 1e-6 for s, e in out)
    assert out[0][0] == 0.0 and out[-1][1] == pytest.approx(30.0)


def test_short_shot_is_merged_not_dropped() -> None:
    """Segments must still tile the asset - dropping loses footage silently."""
    out = apply_length_rules([(0.0, 2.0), (2.0, 2.3), (2.3, 5.0)])
    assert out == [(0.0, 2.3), (2.3, 5.0)]
    assert out[0][0] == 0.0 and out[-1][1] == 5.0


def test_leading_runt_is_absorbed_forward() -> None:
    out = apply_length_rules([(0.0, 0.2), (0.2, 3.0)])
    assert out == [(0.0, 3.0)]


def test_segments_always_tile_the_source() -> None:
    spans = [(0.0, 0.3), (0.3, 12.0), (12.0, 12.4), (12.4, 20.0)]
    out = apply_length_rules(spans)
    assert out[0][0] == pytest.approx(0.0)
    assert out[-1][1] == pytest.approx(20.0)
    for (_, prev_end), (next_start, _) in zip(out, out[1:]):
        assert prev_end == pytest.approx(next_start)


# --- quality metrics -------------------------------------------------------


def test_sharpness_separates_sharp_from_blurred() -> None:
    import cv2

    rng = np.random.default_rng(0)
    sharp = (rng.random((256, 256)) * 255).astype(np.uint8)
    blurred = cv2.GaussianBlur(sharp, (31, 31), 12)
    assert sharpness_of(sharp) > sharpness_of(blurred) + 0.2


def test_exposure_penalises_black_and_white_frames() -> None:
    rng = np.random.default_rng(7)
    # Mid-brightness with real texture: well exposed and not featureless.
    textured = np.clip(
        128 + rng.normal(0, 30, (128, 128)), 0, 255
    ).astype(np.uint8)
    black = np.zeros((128, 128), dtype=np.uint8)
    white = np.full((128, 128), 255, dtype=np.uint8)

    good_score, good_flat = exposure_of(textured)
    black_score, black_flat = exposure_of(black)
    white_score, white_flat = exposure_of(white)

    assert good_score > 0.9 and not good_flat
    assert black_score < 0.1 and black_flat
    assert white_score < 0.1 and white_flat


def test_featureless_frame_is_flat_at_any_brightness() -> None:
    """A perfectly uniform frame means a covered lens, however well exposed."""
    uniform_mid = np.full((128, 128), 128, dtype=np.uint8)
    score, is_flat = exposure_of(uniform_mid)
    assert score > 0.9  # exposure itself is fine
    assert is_flat  # but there is nothing in the frame


def test_all_metrics_are_bounded() -> None:
    rng = np.random.default_rng(1)
    noisy = (rng.random((128, 128)) * 255).astype(np.uint8)
    assert 0.0 <= sharpness_of(noisy) <= 1.0
    score, _ = exposure_of(noisy)
    assert 0.0 <= score <= 1.0


@requires_ffmpeg
def test_measure_on_real_video(tmp_path: Path) -> None:
    path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=s=320x568:r=30:d=4",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
        capture_output=True,
    )
    metrics = measure(str(path), 0.5, 3.5)
    for value in metrics.as_dict().values():
        assert 0.0 <= value <= 1.0
    assert metrics.technical_quality > 0.3  # a clean synthetic clip is usable


def test_unreadable_range_scores_zero_not_neutral() -> None:
    """A range we cannot read must be rejected, not left competing at 0.5."""
    metrics = measure("/nonexistent/file.mp4", 0.0, 1.0)
    assert metrics.technical_quality == 0.0
    assert metrics.flat_frame_ratio == 1.0
    assert judge(2.0, metrics)[0] is False


# --- usability -------------------------------------------------------------


def _metrics(**kw) -> QualityMetrics:
    base = dict(
        sharpness=0.8, exposure_score=0.9, stability_score=0.9,
        motion_quality=0.9, technical_quality=0.85, flat_frame_ratio=0.0,
    )
    base.update(kw)
    return QualityMetrics(**base)


@pytest.mark.parametrize(
    "duration,metrics,expected_reason",
    [
        (0.3, _metrics(), "too_short"),
        (2.0, _metrics(flat_frame_ratio=0.9), "blank_frames"),
        (2.0, _metrics(sharpness=0.05), "out_of_focus"),
        (2.0, _metrics(technical_quality=0.2), "low_quality"),
    ],
)
def test_rejections_carry_a_reason(duration, metrics, expected_reason) -> None:
    """Rejected footage must be explainable, not silently absent."""
    usable, reason = judge(duration, metrics)
    assert usable is False
    assert reason is not None and reason.split(":")[0] == expected_reason


def test_good_segment_is_accepted() -> None:
    assert judge(2.0, _metrics()) == (True, None)


# --- identity --------------------------------------------------------------


@requires_ffmpeg
def test_asset_ids_do_not_collide_across_projects(tmp_path, monkeypatch) -> None:
    """Two projects importing a first clip must not share an asset row.

    Without project-scoped ids the idempotent insert keeps the older row, and
    the newer project silently ends up cutting from the wrong footage.
    """
    import aive.db.session as session_mod
    from aive.config import settings
    from aive.material import ingest

    from aive.storage import LocalStorage, set_storage

    monkeypatch.setattr(settings, "db_url", f"sqlite:///{tmp_path/'ids.db'}")
    monkeypatch.setattr(session_mod, "_engine", None)
    monkeypatch.setattr(session_mod, "_SessionFactory", None)
    session_mod.init_db()
    set_storage(LocalStorage(tmp_path / "storage"))

    def make(directory: Path, color: str) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        out = directory / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", f"color=c={color}:s=160x284:r=30:d=2",
                "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                str(out),
            ],
            check=True, capture_output=True,
        )
        return out

    make(tmp_path / "a", "red")
    make(tmp_path / "b", "blue")

    first = ingest(tmp_path / "a", "proj_one")
    second = ingest(tmp_path / "b", "proj_two")

    assert len(first.assets) == 1 and len(second.assets) == 1
    assert first.assets[0].asset_id != second.assets[0].asset_id
    assert first.assets[0].storage_key != second.assets[0].storage_key
    # And the footage is now owned by the project rather than referenced in
    # place: deleting the user's source directory must not break the project.
    shutil.rmtree(tmp_path / "a")
    from aive.storage import get_storage

    assert get_storage().open_local(first.assets[0].storage_key).exists()

    set_storage(None)
