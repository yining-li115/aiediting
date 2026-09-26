from __future__ import annotations

from collections.abc import Callable

from ..contracts.patch_base import PatchOperationBase
from ..contracts.patch_timeline import (
    AddCaption,
    AddGraphic,
    DeleteAudioClip,
    DeleteCaption,
    DeleteGraphic,
    DeleteVisualClip,
    ImageSourceSelection,
    InsertAudioClip,
    InsertVisualClip,
    RemoveVisualEffect,
    ReplaceAudioSource,
    ReplaceVisualSource,
    SetAudioDucking,
    SetAudioFades,
    SetAudioGain,
    SetAudioOutput,
    SetAudioPlayback,
    SetCaptionOutput,
    SetCaptionStyle,
    SetGraphicOutput,
    SetVideoPlayback,
    SetVisualCrop,
    SetVisualMotion,
    SetVisualOutput,
    SetVisualReframe,
    SetVisualTransition,
    UpdateCaptionText,
    UpdateGraphicProps,
    UpsertVisualEffect,
)
from ..contracts.project_doc import ProjectDoc
from ..contracts.time import ConstantPlayback
from ..contracts.timeline import (
    AudioClip,
    AudioTrack,
    CaptionCue,
    CaptionTrack,
    GraphicClip,
    GraphicTrack,
    ImageClip,
    VideoClip,
    VideoTrack,
)
from .document import (
    element_id,
    insert_before,
    remove_by_id,
    replace_element,
    replace_track,
    require_element,
    require_track,
    track_elements,
    upsert_by_id,
)


OperationHandler = Callable[[ProjectDoc, PatchOperationBase], ProjectDoc]


def _insert_visual_clip(
    document: ProjectDoc, operation: InsertVisualClip
) -> ProjectDoc:
    track = require_track(document, operation.track_id, VideoTrack)
    clips = insert_before(
        track.clips,
        operation.clip,
        operation.before_clip_id,
        lambda clip: clip.clip_id,
        label="visual clip",
    )
    return replace_track(document, track.model_copy(update={"clips": clips}))


def _delete_visual_clip(
    document: ProjectDoc, operation: DeleteVisualClip
) -> ProjectDoc:
    track, _ = require_element(document, operation.clip_id, (VideoClip, ImageClip))
    clips = remove_by_id(
        track_elements(track),
        operation.clip_id,
        element_id,
        label="visual clip",
    )
    return replace_track(document, track.model_copy(update={"clips": clips}))


def _replace_visual_source(
    document: ProjectDoc, operation: ReplaceVisualSource
) -> ProjectDoc:
    track, clip = require_element(
        document, operation.clip_id, (VideoClip, ImageClip)
    )
    common = {
        "clip_id": clip.clip_id,
        "asset_id": operation.source.asset_id,
        "output": clip.output,
        "crop": clip.crop,
        "reframe": clip.reframe,
        "motion": clip.motion,
        "transition_out": clip.transition_out,
        "effects": clip.effects,
    }
    if isinstance(operation.source, ImageSourceSelection):
        replacement = ImageClip(**common)
    else:
        replacement = VideoClip(
            **common,
            source=operation.source.source,
            playback=clip.playback if isinstance(clip, VideoClip) else ConstantPlayback(),
        )
    return replace_element(document, track, replacement)


def _set_visual_output(document: ProjectDoc, operation: SetVisualOutput) -> ProjectDoc:
    track, clip = require_element(
        document, operation.clip_id, (VideoClip, ImageClip)
    )
    return replace_element(
        document, track, clip.model_copy(update={"output": operation.output})
    )


def _set_video_playback(
    document: ProjectDoc, operation: SetVideoPlayback
) -> ProjectDoc:
    track, clip = require_element(document, operation.clip_id, VideoClip)
    return replace_element(
        document, track, clip.model_copy(update={"playback": operation.playback})
    )


def _set_visual_crop(document: ProjectDoc, operation: SetVisualCrop) -> ProjectDoc:
    track, clip = require_element(
        document, operation.clip_id, (VideoClip, ImageClip)
    )
    return replace_element(document, track, clip.model_copy(update={"crop": operation.crop}))


def _set_visual_reframe(
    document: ProjectDoc, operation: SetVisualReframe
) -> ProjectDoc:
    track, clip = require_element(
        document, operation.clip_id, (VideoClip, ImageClip)
    )
    return replace_element(
        document, track, clip.model_copy(update={"reframe": operation.reframe})
    )


def _set_visual_motion(document: ProjectDoc, operation: SetVisualMotion) -> ProjectDoc:
    track, clip = require_element(
        document, operation.clip_id, (VideoClip, ImageClip)
    )
    return replace_element(
        document, track, clip.model_copy(update={"motion": operation.motion})
    )


def _set_visual_transition(
    document: ProjectDoc, operation: SetVisualTransition
) -> ProjectDoc:
    track, clip = require_element(
        document, operation.clip_id, (VideoClip, ImageClip)
    )
    return replace_element(
        document,
        track,
        clip.model_copy(update={"transition_out": operation.transition}),
    )


def _upsert_visual_effect(
    document: ProjectDoc, operation: UpsertVisualEffect
) -> ProjectDoc:
    track, clip = require_element(
        document, operation.clip_id, (VideoClip, ImageClip)
    )
    effects = upsert_by_id(
        clip.effects, operation.effect, lambda effect: effect.effect_id
    )
    return replace_element(document, track, clip.model_copy(update={"effects": effects}))


def _remove_visual_effect(
    document: ProjectDoc, operation: RemoveVisualEffect
) -> ProjectDoc:
    track, clip = require_element(
        document, operation.clip_id, (VideoClip, ImageClip)
    )
    effects = remove_by_id(
        clip.effects,
        operation.effect_id,
        lambda effect: effect.effect_id,
        label="visual effect",
    )
    return replace_element(document, track, clip.model_copy(update={"effects": effects}))


def _insert_audio_clip(document: ProjectDoc, operation: InsertAudioClip) -> ProjectDoc:
    track = require_track(document, operation.track_id, AudioTrack)
    clips = insert_before(
        track.clips,
        operation.clip,
        operation.before_clip_id,
        lambda clip: clip.clip_id,
        label="audio clip",
    )
    return replace_track(document, track.model_copy(update={"clips": clips}))


def _delete_audio_clip(document: ProjectDoc, operation: DeleteAudioClip) -> ProjectDoc:
    track, _ = require_element(document, operation.clip_id, AudioClip)
    clips = remove_by_id(
        track_elements(track),
        operation.clip_id,
        element_id,
        label="audio clip",
    )
    return replace_track(document, track.model_copy(update={"clips": clips}))


def _replace_audio_source(
    document: ProjectDoc, operation: ReplaceAudioSource
) -> ProjectDoc:
    track, clip = require_element(document, operation.clip_id, AudioClip)
    replacement = clip.model_copy(
        update={"asset_id": operation.asset_id, "source": operation.source}
    )
    return replace_element(document, track, replacement)


def _set_audio_output(document: ProjectDoc, operation: SetAudioOutput) -> ProjectDoc:
    track, clip = require_element(document, operation.clip_id, AudioClip)
    return replace_element(
        document, track, clip.model_copy(update={"output": operation.output})
    )


def _set_audio_playback(
    document: ProjectDoc, operation: SetAudioPlayback
) -> ProjectDoc:
    track, clip = require_element(document, operation.clip_id, AudioClip)
    return replace_element(
        document, track, clip.model_copy(update={"playback": operation.playback})
    )


def _set_audio_gain(document: ProjectDoc, operation: SetAudioGain) -> ProjectDoc:
    track, clip = require_element(document, operation.clip_id, AudioClip)
    return replace_element(
        document, track, clip.model_copy(update={"gain_db": operation.gain_db})
    )


def _set_audio_fades(document: ProjectDoc, operation: SetAudioFades) -> ProjectDoc:
    track, clip = require_element(document, operation.clip_id, AudioClip)
    return replace_element(
        document,
        track,
        clip.model_copy(
            update={
                "fade_in_frames": operation.fade_in_frames,
                "fade_out_frames": operation.fade_out_frames,
            }
        ),
    )


def _set_audio_ducking(
    document: ProjectDoc, operation: SetAudioDucking
) -> ProjectDoc:
    track, clip = require_element(document, operation.clip_id, AudioClip)
    return replace_element(
        document,
        track,
        clip.model_copy(update={"ducking_group": operation.ducking_group}),
    )


def _add_caption(document: ProjectDoc, operation: AddCaption) -> ProjectDoc:
    track = require_track(document, operation.track_id, CaptionTrack)
    cues = insert_before(
        track.cues,
        operation.cue,
        None,
        lambda cue: cue.cue_id,
        label="caption",
    )
    return replace_track(document, track.model_copy(update={"cues": cues}))


def _update_caption_text(
    document: ProjectDoc, operation: UpdateCaptionText
) -> ProjectDoc:
    track, cue = require_element(document, operation.cue_id, CaptionCue)
    return replace_element(document, track, cue.model_copy(update={"text": operation.text}))


def _set_caption_output(
    document: ProjectDoc, operation: SetCaptionOutput
) -> ProjectDoc:
    track, cue = require_element(document, operation.cue_id, CaptionCue)
    return replace_element(
        document, track, cue.model_copy(update={"output": operation.output})
    )


def _delete_caption(document: ProjectDoc, operation: DeleteCaption) -> ProjectDoc:
    track, _ = require_element(document, operation.cue_id, CaptionCue)
    cues = remove_by_id(
        track_elements(track),
        operation.cue_id,
        element_id,
        label="caption",
    )
    return replace_track(document, track.model_copy(update={"cues": cues}))


def _set_caption_style(document: ProjectDoc, operation: SetCaptionStyle) -> ProjectDoc:
    track = require_track(document, operation.track_id, CaptionTrack)
    return replace_track(document, track.model_copy(update={"style": operation.style}))


def _add_graphic(document: ProjectDoc, operation: AddGraphic) -> ProjectDoc:
    track = require_track(document, operation.track_id, GraphicTrack)
    graphics = insert_before(
        track.graphics,
        operation.graphic,
        None,
        lambda graphic: graphic.graphic_id,
        label="graphic",
    )
    return replace_track(document, track.model_copy(update={"graphics": graphics}))


def _set_graphic_output(
    document: ProjectDoc, operation: SetGraphicOutput
) -> ProjectDoc:
    track, graphic = require_element(document, operation.graphic_id, GraphicClip)
    return replace_element(
        document, track, graphic.model_copy(update={"output": operation.output})
    )


def _update_graphic_props(
    document: ProjectDoc, operation: UpdateGraphicProps
) -> ProjectDoc:
    track, graphic = require_element(document, operation.graphic_id, GraphicClip)
    return replace_element(
        document, track, graphic.model_copy(update={"props": operation.props})
    )


def _delete_graphic(document: ProjectDoc, operation: DeleteGraphic) -> ProjectDoc:
    track, _ = require_element(document, operation.graphic_id, GraphicClip)
    graphics = remove_by_id(
        track_elements(track),
        operation.graphic_id,
        element_id,
        label="graphic",
    )
    return replace_track(document, track.model_copy(update={"graphics": graphics}))


TIMELINE_OPERATION_HANDLERS: dict[str, OperationHandler] = {
    "insert_visual_clip": _insert_visual_clip,
    "delete_visual_clip": _delete_visual_clip,
    "replace_visual_source": _replace_visual_source,
    "set_visual_output": _set_visual_output,
    "set_video_playback": _set_video_playback,
    "set_visual_crop": _set_visual_crop,
    "set_visual_reframe": _set_visual_reframe,
    "set_visual_motion": _set_visual_motion,
    "set_visual_transition": _set_visual_transition,
    "upsert_visual_effect": _upsert_visual_effect,
    "remove_visual_effect": _remove_visual_effect,
    "insert_audio_clip": _insert_audio_clip,
    "delete_audio_clip": _delete_audio_clip,
    "replace_audio_source": _replace_audio_source,
    "set_audio_output": _set_audio_output,
    "set_audio_playback": _set_audio_playback,
    "set_audio_gain": _set_audio_gain,
    "set_audio_fades": _set_audio_fades,
    "set_audio_ducking": _set_audio_ducking,
    "add_caption": _add_caption,
    "update_caption_text": _update_caption_text,
    "set_caption_output": _set_caption_output,
    "delete_caption": _delete_caption,
    "set_caption_style": _set_caption_style,
    "add_graphic": _add_graphic,
    "set_graphic_output": _set_graphic_output,
    "update_graphic_props": _update_graphic_props,
    "delete_graphic": _delete_graphic,
}
