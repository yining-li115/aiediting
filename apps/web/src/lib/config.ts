/**
 * Every hardcoded value in the UI, in one file.
 *
 * The rule: if a number, label or option list was chosen rather than derived,
 * it lives here with a `TODO 硬编码` tag saying what it controls and what it
 * depends on. Scattered magic values are unfindable in a codebase this size,
 * and several of the values below are placeholders for backend features that
 * do not exist yet - which is exactly the kind of thing that quietly ships.
 *
 * Search `TODO 硬编码` to find everything at once.
 */

/* --- brand ---------------------------------------------------------------- */

// TODO 硬编码: product name, from the design mock. No backend source.
export const APP_NAME = "Video AI Studio";

/* --- prompt --------------------------------------------------------------- */

// TODO 硬编码: prompt character limit. The backend does not enforce one yet.
export const PROMPT_MAX_CHARS = 2000;

// TODO 硬编码: placeholder copy.
export const PROMPT_PLACEHOLDER = "Describe the video you want to make...";

/* --- uploads -------------------------------------------------------------- */

// TODO 硬编码: accepted formats and size caps shown to the user. The backend's
// real list is specs/enums.py (VIDEO_SUFFIXES / PHOTO_SUFFIXES) and it accepts
// more than this - heic, webm, tiff. Aligning the two needs an endpoint that
// reports the accepted types rather than a second copy of the list here.
export const UPLOAD_LIMITS = {
  material: {
    accept: "video/mp4,video/quicktime,image/jpeg,image/png",
  },
  style: {
    accept: "video/mp4,video/quicktime",
  },
  remake: {
    accept: "video/mp4,video/quicktime",
  },
  music: {
    accept: "audio/mpeg,audio/mp4,audio/wav,audio/aac,audio/flac,audio/ogg",
  },
} as const;

/* --- video settings ------------------------------------------------------- */

// ProjectDoc deliberately has no maximum target duration. The browser accepts
// any positive minute value; model input windows and preview ranges are
// separate implementation details and must not become a project-length cap.
export const TARGET_DURATION_INPUT = {
  // TODO 硬编码: UI state stores whole seconds before creation converts to frames.
  minMinutes: 1 / 60,
  // TODO 硬编码: arrow-key increment; users may still type any positive value.
  stepMinutes: 0.5,
} as const;

export function formatTargetDuration(totalSeconds: number): string {
  const roundedSeconds = Math.max(1, Math.round(totalSeconds));
  if (roundedSeconds < 60) return `${roundedSeconds}s`;
  const minutes = Math.floor(roundedSeconds / 60);
  const seconds = roundedSeconds % 60;
  return seconds === 0 ? `${minutes}m` : `${minutes}m ${seconds}s`;
}

// TODO 硬编码: style options. The backend's vocabulary is KNOWN_STYLES in
// specs/enums.py (travel, daily, relaxed, energetic, cinematic, minimal, warm,
// food) and it accepts free text besides. This shorter list is the mock's.
export const VIBE_STYLES = [
  { value: "cinematic", label: "Cinematic" },
  { value: "energetic", label: "Energetic" },
  { value: "relaxed", label: "Relaxed" },
  { value: "minimal", label: "Minimal" },
  { value: "warm", label: "Warm" },
] as const;

// TODO 硬编码: aspect ratios. **The backend only renders 9:16 today**
// (TECHNICAL_PLAN 1.5); 16:9 and 1:1 are in the spec but not implemented, so
// choosing them here would not change the output.
export const ASPECT_RATIOS = [
  { value: "16:9", label: "16:9 (Widescreen)" },
  { value: "9:16", label: "9:16 (Vertical)" },
  { value: "1:1", label: "1:1 (Square)" },
] as const;

// TODO 硬编码: quality options. The backend's render_constraints carry a
// resolution, but the API does not accept one yet.
export const QUALITIES = [
  { value: "720p", label: "720p" },
  { value: "1080p", label: "1080p" },
  { value: "4k", label: "4K" },
] as const;

// TODO 硬编码: initial canvas dimensions selected by the quality/aspect
// controls. The renderer capability endpoint will replace this lookup once
// revision-bound rendering is connected.
export const OUTPUT_DIMENSIONS = {
  "720p": {
    "16:9": { width: 1280, height: 720 },
    "9:16": { width: 720, height: 1280 },
    "1:1": { width: 720, height: 720 },
  },
  "1080p": {
    "16:9": { width: 1920, height: 1080 },
    "9:16": { width: 1080, height: 1920 },
    "1:1": { width: 1080, height: 1080 },
  },
  "4k": {
    "16:9": { width: 3840, height: 2160 },
    "9:16": { width: 2160, height: 3840 },
    "1:1": { width: 2160, height: 2160 },
  },
} as const;

export const DEFAULT_SETTINGS = {
  // TODO 硬编码: defaults from the mock.
  length: 60,
  vibe: "cinematic",
  aspectRatio: "16:9",
  quality: "1080p",
  autoCaptions: true,
  backgroundMusic: true,
  soundEffects: false,
} as const;

/* --- layout --------------------------------------------------------------- */

/**
 * Layout sizes are ranges, not fixed numbers.
 *
 * The design mock was drawn at one width. Pinning the pixel values from it
 * means every other monitor gets a layout that is proportionally wrong -
 * cramped on a laptop, marooned in whitespace on a wide display. Each value
 * below is `clamp(floor, viewport-relative, ceiling)`: it scales with the
 * screen and stops at sizes that stay usable.
 *
 * Things that should NOT scale - border widths, corner radii, icon sizes -
 * stay fixed. A 1px border is 1px on every screen.
 */
export const LAYOUT = {
  // TODO 硬编码: header height.
  topBarHeight: "2.75rem",
} as const;
