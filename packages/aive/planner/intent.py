"""Prompt -> Intent.

An LLM does the extraction when one is available; a rule pass is the fallback.
The fallback is not a placeholder - it runs whenever the model is unavailable,
returns unparseable output, or omits a duration, and it is what keeps an empty
prompt from producing an empty plan.
"""

from __future__ import annotations

import re

from ..specs.edit_plan import Intent

DEFAULT_DURATION = 45.0
MIN_DURATION = 20.0
MAX_DURATION = 90.0

# "45秒" / "45 s" / "1分30秒" / "a minute"
_SECONDS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:秒|s\b|sec|seconds?)", re.I)
_MINUTES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:分钟|分|min\b|minutes?)", re.I)

# --- rule fallback ----------------------------------------------------------
#
# The LLM is the primary parser (see parse_intent below); these tables are the
# fallback for when it is unavailable, times out, or returns something invalid.
#
# They are deliberately kept small. A word list cannot understand "像王家卫那样"
# or "多给我朋友的镜头" and should not pretend to - extending it towards
# open-vocabulary understanding is what the model is for. What it must do is
# emit the SAME canonical values as the model, so the planner behaves
# identically whichever parser ran.
_STYLE_WORDS: dict[str, tuple[str, ...]] = {
    "travel": ("旅行", "旅游", "travel", "trip", "vlog"),
    "daily": ("日常", "生活", "daily", "everyday"),
    "relaxed": ("轻松", "放松", "chill", "relaxed", "慢"),
    "energetic": ("活力", "动感", "快节奏", "energetic", "upbeat", "快"),
    "cinematic": ("电影感", "氛围感", "cinematic", "moody"),
    "minimal": ("简洁", "干净", "minimal", "clean", "简单"),
    "warm": ("温暖", "温馨", "warm", "cozy"),
    "food": ("美食", "吃", "food", "餐"),
}

# Constraints the user states negatively. Each maps to a concrete plan change,
# applied in planner.plan; recording the raw phrase alongside keeps the
# original wording available for the agent layer later.
# Values must come from specs.enums.UserConstraint - anything else is dropped
# by the planner and the user's instruction disappears.
_CONSTRAINTS: dict[str, tuple[str, ...]] = {
    "few_transitions": ("不要太多转场", "少一点转场", "少用转场", "fewer transitions"),
    "no_transitions": ("不要转场", "别加转场", "no transitions"),
    "no_subtitles": ("不要字幕", "别加字幕", "no subtitles", "不要大字幕"),
    "few_subtitles": ("少一点字幕", "字幕少", "fewer subtitles"),
    "slow_pace": ("慢一点", "别太快", "slower", "别切太碎"),
    "fast_pace": ("快一点", "紧凑一点", "faster"),
    "keep_original_sound": ("保留原声", "留着原声", "现场的声音", "keep original sound"),
    "no_music": ("不要音乐", "别加音乐", "no music"),
}


def parse_duration(prompt: str) -> float | None:
    minutes = _MINUTES.search(prompt)
    seconds = _SECONDS.search(prompt)
    total = 0.0
    if minutes:
        total += float(minutes.group(1)) * 60.0
    if seconds:
        total += float(seconds.group(1))
    if total <= 0:
        return None
    return max(MIN_DURATION, min(MAX_DURATION, total))


def parse_styles(prompt: str) -> list[str]:
    lowered = prompt.lower()
    return [
        tag
        for tag, words in _STYLE_WORDS.items()
        if any(word.lower() in lowered for word in words)
    ]


def parse_constraints(prompt: str) -> list[str]:
    lowered = prompt.lower()
    return [
        name
        for name, phrases in _CONSTRAINTS.items()
        if any(phrase.lower() in lowered for phrase in phrases)
    ]


def parse_intent_rules(
    prompt: str | None,
    target_duration: float | None = None,
    aspect_ratio: str = "9:16",
) -> Intent:
    """Deterministic extraction. Always succeeds, never calls a model."""
    text = prompt or ""
    duration = target_duration or parse_duration(text) or DEFAULT_DURATION
    styles = parse_styles(text)
    if not styles:
        # Travel/daily vlog is the only supported category, so an unhinted
        # prompt gets the category default rather than an empty style list.
        styles = ["travel"]
    return Intent(
        target_duration=max(MIN_DURATION, min(MAX_DURATION, duration)),
        aspect_ratio=aspect_ratio,
        style=styles,
        user_constraints=parse_constraints(text),
    )


async def parse_intent(
    prompt: str | None,
    provider=None,
    target_duration: float | None = None,
    aspect_ratio: str = "9:16",
) -> Intent:
    """LLM first, rules as the fallback.

    An explicit ``target_duration`` always wins: if the user set a length in
    the UI, no model gets to overrule it.
    """
    rule_based = parse_intent_rules(prompt, target_duration, aspect_ratio)
    if provider is None or not prompt or not hasattr(provider, "parse_intent"):
        return rule_based

    try:
        parsed = await provider.parse_intent(prompt)
    except Exception:
        # A model failure must degrade to rules, never abort the project.
        return rule_based

    if not isinstance(parsed, Intent):
        return rule_based
    if target_duration is not None:
        parsed.target_duration = target_duration
    parsed.target_duration = max(
        MIN_DURATION, min(MAX_DURATION, parsed.target_duration)
    )
    if not parsed.style:
        parsed.style = rule_based.style
    # Rule-detected constraints are unioned in: the model dropping "不要太多
    # 转场" should not silently lose the instruction.
    for constraint in rule_based.user_constraints:
        if constraint not in parsed.user_constraints:
            parsed.user_constraints.append(constraint)
    return parsed
