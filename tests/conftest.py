from __future__ import annotations

import shutil
import pytest

HAS_FFMPEG = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(not HAS_FFMPEG, reason="ffmpeg not installed")


@pytest.fixture(autouse=True)
def provider_test_safety(monkeypatch):
    """Tests inject model fixtures and never register runtime fallbacks."""

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
