"""Music library.

Two rules, both non-negotiable:

* every track needs an explicit licence recorded in the database - "we found
  it somewhere" is not a licence, and this is content that ships inside a
  user's published video;
* a reference video's audio is **never** reused. Its beat and tempo are
  extracted as features to inform the cut, and nothing else crosses over.

Beats are precomputed once at import, not per project: a 60-second track is
analysed in about a second and the result never changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .db.models import MusicTrack
from .db.session import session_scope
from .storage import MUSIC, get_storage, project_key, safe_name, shared_key

DEFAULT_LIBRARY = Path("data/music")


@dataclass
class Track:
    music_id: str
    storage_key: str
    title: str | None
    license: str
    bpm: float | None
    beat_times: list[float]
    mood_tags: list[str]
    duration: float | None
    project_id: str | None = None

    def as_dict(self) -> dict:
        return {
            "music_id": self.music_id,
            "storage_key": self.storage_key,
            "bpm": self.bpm,
            "beat_times": self.beat_times,
            "mood_tags": self.mood_tags,
        }


# TODO(review): music analysis currently stops at tempo and a beat grid.
#
# That is enough for what the matcher does today - move cut points onto the
# beat (see matcher/beats.py) - and it is deliberately deterministic: tempo and
# onsets are exactly computable, so they must not be handed to a model.
#
# What is missing is everything above the beat:
#
#   * section structure (intro / verse / chorus / outro, and where the drop is)
#     -> librosa.segment can find boundaries; naming them needs more.
#     Without it we cannot express real editing grammar like "hold the shot
#     just before the chorus" or "change section when the music does".
#   * an energy curve over time, to match the plan's own energy arc against the
#     track instead of only matching average tempo to average pace.
#   * choosing WHERE in a track to start. `AudioMix.music_in` exists and is
#     always 0.0, so a 45-second cut always uses the first 45 seconds of a
#     three-minute song rather than its best 45 seconds.
#
# The coordination part is what makes this bigger than a DSP task: picking a
# track, picking the segment of it to use, and shaping the edit's structure are
# one decision, not three independent ones. That is agent work (block E) built
# on top of deterministic features, not a replacement for them - the same split
# as everywhere else in this project.


def analyse(path: Path) -> tuple[float | None, list[float], float | None]:
    """Tempo, beat grid and duration.

    librosa is imported lazily so the core package does not depend on it; a
    missing install degrades to "no beat information" rather than failing,
    because beat sync is an improvement to the cut, not a prerequisite for it.
    """
    try:
        import librosa  # noqa: PLC0415
    except ImportError:
        return None, [], None

    y, sr = librosa.load(str(path), mono=True)
    duration = float(len(y) / sr)
    tempo, beats = librosa.beat.beat_track(y=y, sr=sr, units="time")
    bpm = float(tempo) if not hasattr(tempo, "__len__") else float(tempo[0])
    return bpm, [round(float(t), 3) for t in beats], duration


def register(
    path: Path,
    license: str,
    title: str | None = None,
    mood_tags: list[str] | None = None,
    project_id: str | None = None,
    music_id: str | None = None,
) -> Track:
    """Add one track, copying it into storage and computing its beat grid once.

    ``project_id`` set means the user uploaded this for one project;
    ``None`` means it belongs to the library every project can choose from.
    """
    storage = get_storage()
    music_id = music_id or path.stem
    filename = safe_name(path.name, music_id)
    key = (
        project_key(project_id, MUSIC, filename)
        if project_id
        else shared_key(MUSIC, filename)
    )
    storage.put(path, key)
    local = storage.open_local(key)

    bpm, beats, duration = analyse(local)
    track = Track(
        music_id=music_id,
        storage_key=key,
        title=title or path.stem,
        license=license,
        bpm=bpm,
        beat_times=beats,
        mood_tags=mood_tags or [],
        duration=duration,
        project_id=project_id,
    )
    with session_scope() as session:
        row = session.get(MusicTrack, music_id)
        values = dict(
            storage_key=track.storage_key,
            project_id=project_id,
            title=track.title,
            license=track.license,
            bpm=track.bpm,
            beat_times_json=track.beat_times,
            mood_tags_json=track.mood_tags,
            duration=track.duration,
        )
        if row is None:
            session.add(MusicTrack(id=music_id, **values))
        else:
            for key, value in values.items():
                setattr(row, key, value)
    return track


def import_library(directory: Path = DEFAULT_LIBRARY) -> list[Track]:
    """Import every audio file plus its sidecar licence metadata.

    A track without a ``.json`` sidecar declaring its licence is skipped, not
    imported with a guess.
    """
    if not directory.exists():
        return []
    tracks: list[Track] = []
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() not in {".mp3", ".m4a", ".wav", ".aac", ".flac"}:
            continue
        sidecar = path.with_suffix(".json")
        if not sidecar.exists():
            continue
        meta = json.loads(sidecar.read_text(encoding="utf-8"))
        if not meta.get("license"):
            continue
        tracks.append(
            register(
                path,
                license=meta["license"],
                title=meta.get("title"),
                mood_tags=meta.get("mood_tags", []),
            )
        )
    return tracks


def load_all(project_id: str | None = None) -> list[Track]:
    """Library tracks, plus any uploaded for this project."""
    with session_scope() as session:
        query = session.query(MusicTrack)
        rows = [
            r
            for r in query.all()
            if r.project_id is None or r.project_id == project_id
        ]
        return [
            Track(
                music_id=r.id, storage_key=r.storage_key, title=r.title,
                license=r.license, bpm=r.bpm,
                beat_times=r.beat_times_json or [],
                mood_tags=r.mood_tags_json or [], duration=r.duration,
                project_id=r.project_id,
            )
            for r in rows
        ]


def choose(
    style: list[str],
    target_duration: float,
    pace: str,
    project_id: str | None = None,
) -> Track | None:
    """Pick a track by mood overlap, then by tempo matching the pace.

    Deliberately simple and deterministic: a plan that gets a different track
    on every run cannot be compared against itself.
    """
    tracks = load_all(project_id)
    if not tracks:
        return None

    # TODO(review): the tempo a given pace "should" have. Chosen by ear from
    # what typical travel-vlog music sits at; never validated against whether
    # a listener agrees the pairing feels right.
    wanted_bpm = {"slow": 80.0, "medium": 100.0, "medium_fast": 120.0, "fast": 140.0}
    target_bpm = wanted_bpm.get(pace, 110.0)
    styles = {s.lower() for s in style}

    def rank(track: Track) -> tuple[int, int, int, float, str]:
        # A track the user uploaded for this project beats the library: they
        # chose it deliberately, we only guessed.
        own = 0 if track.project_id else 1
        # A track long enough to cover the cut is preferred but not required -
        # a short one loops rather than being excluded.
        long_enough = 0 if (track.duration or 0) >= target_duration else 1
        overlap = len(styles & {m.lower() for m in track.mood_tags})
        distance = abs((track.bpm or target_bpm) - target_bpm)
        return (own, long_enough, -overlap, distance, track.music_id)

    return sorted(tracks, key=rank)[0]
