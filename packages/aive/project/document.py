from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TypeVar

from ..contracts.project_doc import ProjectDoc
from ..contracts.timeline import (
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionTrack,
    GraphicClip,
    GraphicTrack,
    Track,
    VideoTrack,
    VisualClip,
)
from .errors import PatchApplicationError


Element = VisualClip | AudioClip | CaptionCue | GraphicClip
T = TypeVar("T")


def element_id(element: Element) -> str:
    if isinstance(element, (AudioClip,)):
        return element.clip_id
    if isinstance(element, CaptionCue):
        return element.cue_id
    if isinstance(element, GraphicClip):
        return element.graphic_id
    return element.clip_id


def track_elements(track: Track) -> tuple[Element, ...]:
    if isinstance(track, VideoTrack):
        return track.clips
    if isinstance(track, AudioTrack):
        return track.clips
    if isinstance(track, CaptionTrack):
        return track.cues
    return track.graphics


def replace_track(document: ProjectDoc, replacement: Track) -> ProjectDoc:
    tracks = tuple(
        replacement if track.track_id == replacement.track_id else track
        for track in document.timeline.tracks
    )
    timeline = document.timeline.model_copy(update={"tracks": tracks})
    return document.model_copy(update={"timeline": timeline})


def require_track(
    document: ProjectDoc,
    track_id: str,
    expected_type: type[T] | None = None,
) -> T | Track:
    for track in document.timeline.tracks:
        if track.track_id == track_id:
            if expected_type is not None and not isinstance(track, expected_type):
                raise PatchApplicationError(
                    "wrong_target_type",
                    f"track {track_id!r} is not a {expected_type.__name__}",
                    details={"target_id": track_id},
                )
            return track
    raise PatchApplicationError(
        "target_not_found",
        f"track {track_id!r} does not exist",
        details={"target_id": track_id},
    )


def require_element(
    document: ProjectDoc,
    target_id: str,
    expected_type: type[T] | tuple[type, ...],
) -> tuple[Track, T]:
    for track in document.timeline.tracks:
        for element in track_elements(track):
            if element_id(element) != target_id:
                continue
            if not isinstance(element, expected_type):
                expected_name = (
                    expected_type.__name__
                    if isinstance(expected_type, type)
                    else " or ".join(item.__name__ for item in expected_type)
                )
                raise PatchApplicationError(
                    "wrong_target_type",
                    f"element {target_id!r} is not a {expected_name}",
                    details={"target_id": target_id},
                )
            return track, element
    raise PatchApplicationError(
        "target_not_found",
        f"element {target_id!r} does not exist",
        details={"target_id": target_id},
    )


def replace_element(
    document: ProjectDoc,
    track: Track,
    replacement: Element,
) -> ProjectDoc:
    target_id = element_id(replacement)
    values = tuple(
        replacement if element_id(element) == target_id else element
        for element in track_elements(track)
    )
    return replace_track(document, _track_with_elements(track, values))


def insert_before(
    values: Sequence[T],
    value: T,
    before_id: str | None,
    get_id: Callable[[T], str],
    *,
    label: str,
) -> tuple[T, ...]:
    ids = [get_id(item) for item in values]
    value_id = get_id(value)
    if value_id in ids:
        raise PatchApplicationError(
            "duplicate_target_id",
            f"{label} {value_id!r} already exists",
            details={"target_id": value_id},
        )
    if before_id is None:
        return (*values, value)
    try:
        index = ids.index(before_id)
    except ValueError as exc:
        raise PatchApplicationError(
            "target_not_found",
            f"before-{label} {before_id!r} does not exist",
            details={"target_id": before_id},
        ) from exc
    return (*values[:index], value, *values[index:])


def remove_by_id(
    values: Sequence[T],
    target_id: str,
    get_id: Callable[[T], str],
    *,
    label: str,
) -> tuple[T, ...]:
    result = tuple(item for item in values if get_id(item) != target_id)
    if len(result) == len(values):
        raise PatchApplicationError(
            "target_not_found",
            f"{label} {target_id!r} does not exist",
            details={"target_id": target_id},
        )
    return result


def upsert_by_id(
    values: Sequence[T],
    value: T,
    get_id: Callable[[T], str],
) -> tuple[T, ...]:
    target_id = get_id(value)
    if any(get_id(item) == target_id for item in values):
        return tuple(value if get_id(item) == target_id else item for item in values)
    return (*values, value)


def _track_with_elements(track: Track, values: tuple[Element, ...]) -> Track:
    if isinstance(track, (VideoTrack, AudioTrack)):
        return track.model_copy(update={"clips": values})
    if isinstance(track, CaptionTrack):
        return track.model_copy(update={"cues": values})
    return track.model_copy(update={"graphics": values})
