"""TimelineSpec -> ffmpeg command lines. Pure mechanical translation.

Nothing in this module is allowed to make an editorial decision. "Which clip
is better" and "how long should this be" belong to the matcher. Keeping the
line sharp is what makes swapping the render backend a local change instead of
a rewrite of the matching logic.

Strategy is two-pass on purpose:

1. cut each clip out to a normalised intermediate (identical codec, fps,
   resolution, pixel format, sample rate);
2. concatenate the intermediates and mix the music in.

A single filter_complex covering 30+ clips is both hard to debug and prone to
failing as a whole when one parameter changes. The two-pass version fails per
clip, which is far easier to diagnose during single-person development.
"""

from __future__ import annotations

from pathlib import Path

from ..specs.timeline import AudioMix, TimelineSpec, VideoClip
from .profiles import RenderProfile

# Silent stereo track used when a source has no audio, or when the plan asks
# for the original sound to be muted. Every intermediate must have exactly one
# audio stream or the concat demuxer refuses to join them.
_SILENT = "anullsrc=channel_layout=stereo:sample_rate=48000"

# Detected from the file rather than carried in the spec: whether a source is
# a still is a mechanical property of the bytes, and the compiler's job is
# mechanical translation.
_PHOTO_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".bmp", ".tiff"}
)


def is_photo(path: Path) -> bool:
    return path.suffix.lower() in _PHOTO_SUFFIXES


# TODO(review): Ken Burns for stills is not implemented.
#
# A photo that holds perfectly still between two moving shots reads as the
# video having stalled, not as a photograph. A slow push or pull fixes that,
# and ffmpeg's zoompan filter does the work:
#
#   zoompan=z='min(zoom+0.0008,1.15)':d=<frames>
#           :x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'
#
# Deliberately NOT a renderer behaviour. If this function quietly added motion
# to every still, four things would break: a plan could not ask for a shot to
# stay static (sometimes the right choice for a quiet ending), the critic
# could not judge whether the motion suited the moment, a reference remake
# could not reproduce "this reference holds its stills still", and a user
# could not say "stop moving my photos". The decision belongs in
# EditPlanSpec.slot.motion, which already exists; this function should only
# execute it.
#
# When it is built, start with centred zoom only (zoom_in / zoom_out) and
# leave panning until composition analysis exists. A pan needs to know where
# the subject is - `composition_score` is still null - and a blind pan
# regularly walks the subject out of frame, which is worse than no motion.
# Two further judgement calls will need their own review: how much zoom
# (5-15% is typical, more looks cheap) and when the planner should ask for it
# at all (adjacent shots moving -> yes; a still surrounded by stills -> no).


def clip_filter(clip: VideoClip, profile: RenderProfile) -> str:
    """Video filter chain for one clip: crop -> speed -> scale -> pad -> fps."""
    steps: list[str] = []

    if clip.crop is not None:
        c = clip.crop
        steps.append(
            f"crop=w=iw*{c.w:.6f}:h=ih*{c.h:.6f}:x=iw*{c.x:.6f}:y=ih*{c.y:.6f}"
        )

    if abs(clip.speed - 1.0) > 1e-6:
        # setpts scales presentation timestamps: >1 speed means smaller PTS.
        steps.append(f"setpts=PTS/{clip.speed:.6f}")

    steps.append(
        f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=decrease"
    )
    steps.append(
        f"pad={profile.width}:{profile.height}:(ow-iw)/2:(oh-ih)/2:color=black"
    )
    steps.append("setsar=1")
    steps.append(f"fps={profile.fps}")
    return ",".join(steps)


def clip_audio_filter(clip: VideoClip) -> str:
    """Audio chain for one clip: tempo compensation -> gain -> resample."""
    steps: list[str] = []

    if abs(clip.speed - 1.0) > 1e-6:
        # atempo only accepts 0.5-2.0, so larger changes are chained.
        remaining = clip.speed
        while remaining > 2.0 + 1e-9:
            steps.append("atempo=2.0")
            remaining /= 2.0
        while remaining < 0.5 - 1e-9:
            steps.append("atempo=0.5")
            remaining /= 0.5
        steps.append(f"atempo={remaining:.6f}")

    steps.append(f"volume={clip.source_audio_gain:.4f}")
    steps.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
    return ",".join(steps)


def clip_command(
    clip: VideoClip,
    source_path: Path,
    out_path: Path,
    profile: RenderProfile,
    ffmpeg_bin: str = "ffmpeg",
    source_has_audio: bool = True,
) -> list[str]:
    """Pass 1: extract and normalise a single clip."""
    use_source_audio = (
        source_has_audio
        and clip.source_audio_gain > 0.0
        and not is_photo(source_path)
    )

    cmd = [ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error"]

    if is_photo(source_path):
        # A still has no timeline to seek into. -loop 1 turns it into a video
        # stream and -t gives it the length the slot asked for, which is why a
        # photo can fill a slot of any duration without adaptation.
        cmd += [
            "-loop", "1",
            "-t", f"{clip.timeline_duration:.6f}",
            "-i", str(source_path),
        ]
    else:
        # -ss before -i seeks fast; -t is the source-side span, which is the
        # timeline span multiplied by speed.
        cmd += ["-ss", f"{clip.source_in:.6f}", "-t", f"{clip.source_duration:.6f}"]
        cmd += ["-i", str(source_path)]

    if not use_source_audio:
        cmd += ["-f", "lavfi", "-t", f"{clip.timeline_duration:.6f}", "-i", _SILENT]

    filter_v = clip_filter(clip, profile)
    if use_source_audio:
        filter_a = clip_audio_filter(clip)
        # "0:a:0" rather than "0:a": phone footage often carries several audio
        # and data tracks, and a bare stream specifier then matches more than
        # one and ffmpeg refuses the whole graph.
        cmd += [
            "-filter_complex",
            f"[0:v:0]{filter_v}[v];[0:a:0]{filter_a}[a]",
            "-map", "[v]", "-map", "[a]",
        ]
    else:
        cmd += [
            "-filter_complex",
            f"[0:v:0]{filter_v}[v]",
            "-map", "[v]", "-map", "1:a",
        ]

    cmd += [
        "-c:v", profile.video_codec,
        "-crf", str(profile.crf),
        "-preset", profile.preset,
        "-pix_fmt", "yuv420p",
        "-c:a", profile.audio_codec,
        "-b:a", profile.audio_bitrate,
        "-ar", "48000",
        "-ac", "2",
        # Force the exact timeline duration so rounding inside filters cannot
        # accumulate drift across dozens of clips.
        "-t", f"{clip.timeline_duration:.6f}",
        str(out_path),
    ]
    return cmd


def concat_file(intermediates: list[Path]) -> str:
    """Body of the concat demuxer list file."""
    lines = []
    for path in intermediates:
        escaped = str(path.resolve()).replace("'", r"'\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def music_filter(audio: AudioMix, total_duration: float) -> str:
    steps = [f"volume={audio.volume:.4f}"]
    if audio.fade_in > 0:
        steps.append(f"afade=t=in:st=0:d={audio.fade_in:.3f}")
    if audio.fade_out > 0:
        start = max(0.0, total_duration - audio.fade_out)
        steps.append(f"afade=t=out:st={start:.3f}:d={audio.fade_out:.3f}")
    steps.append("aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo")
    return ",".join(steps)


def concat_command(
    list_path: Path,
    out_path: Path,
    timeline: TimelineSpec,
    profile: RenderProfile,
    music_path: Path | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Pass 2: join the intermediates and mix in the music bed."""
    cmd = [
        ffmpeg_bin, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_path),
    ]

    if music_path is None:
        cmd += [
            "-c:v", "copy",
            "-c:a", profile.audio_codec,
            "-b:a", profile.audio_bitrate,
            "-t", f"{timeline.duration:.6f}",
            "-movflags", "+faststart",
            str(out_path),
        ]
        return cmd

    audio = timeline.audio

    # --- duration policy: the VIDEO is authoritative -----------------------
    #
    # The user asked for a video of a certain length, and the plan was built
    # to that length, so the music adapts to it - never the other way round.
    # Three mechanisms enforce it, and all three are needed:
    #
    #   1. -stream_loop -1   a track shorter than the cut repeats, instead of
    #                        leaving the tail in silence (which reads as a bug)
    #   2. amix duration=first   the mix ends when the video's own audio does
    #   3. -t <duration>     a hard bound on the output, so neither input can
    #                        extend the result
    #
    # The cost is that a longer track is cut off mid-phrase. AudioMix.fade_out
    # exists for exactly that: it masks a truncation that would otherwise sound
    # like the file simply stopped.
    #
    # TODO(review): this policy is right for "make me a 45-second video" and
    # wrong for "cut this to my song". A user who chose a specific track often
    # wants the edit to end where the music does, or at least on a phrase
    # boundary rather than 1.4 seconds into one. Two better behaviours, in
    # increasing order of work:
    #
    #   a) let the plan's target_duration snap to a musical boundary (the end
    #      of a bar or a section) within its tolerance, so truncation lands
    #      somewhere the ear accepts;
    #   b) a music-led mode where the track's length sets the target duration
    #      outright - which needs a way for the user to ask for it, so it is
    #      really a product decision (block E) rather than a renderer one.
    #
    # Whichever is chosen, it must stay a property of the TimelineSpec rather
    # than of this function: the renderer executes a decision, it does not make
    # one.
    cmd += [
        "-stream_loop", "-1",
        "-ss", f"{audio.music_in:.6f}",
        "-i", str(music_path),
    ]
    mfilter = music_filter(audio, timeline.duration)
    # duration=first keeps the video length authoritative (see the policy note
    # above); a longer music file must never extend the cut.
    filtergraph = (
        f"[1:a]{mfilter}[music];"
        f"[0:a][music]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )
    cmd += [
        "-filter_complex", filtergraph,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", profile.audio_codec,
        "-b:a", profile.audio_bitrate,
        "-t", f"{timeline.duration:.6f}",
        "-movflags", "+faststart",
        str(out_path),
    ]
    return cmd
