"""THE model file.

Every model decision in this project lives here and nowhere else. Business
code never names a vendor, never names a model, and never reads an API key.
It asks for a *role*; this file decides which model serves that role and which
adapter talks to it.

Swapping a model is therefore a one-line edit to ``ACTIVE_STACK`` (or one
environment variable), with no change to any pipeline code. If a model swap
ever requires touching code outside this file, the abstraction has a hole and
that hole must be fixed before the swap.

Three rules that this file exists to enforce:

* **Pin snapshots.** ``model_id`` must be a version or snapshot identifier,
  never a rolling alias. Otherwise a silent upstream upgrade changes output
  quality and nothing in the logs can explain it.
* **No single model for everything.** Roles are selected independently. A
  model that is excellent at reading video may be a poor, slow and expensive
  choice for resolving "make the subtitles smaller".
* **Capabilities decide pipeline shape.** ``reads_video_audio``,
  ``sampling_fps`` and ``reliable_timestamps`` are not documentation - the
  material and reference pipelines branch on them.

Status values are deliberate: nothing is ``frozen`` until the bake-off has
actually been run.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

from .roles import Role

Status = Literal["frozen", "provisional", "challenger", "auxiliary", "excluded"]

# Adapter = the client implementation that knows how to talk to this model.
# Several models share one adapter; that is the point of the split.
Adapter = Literal[
    "openai_compatible",
    "local_openclip",
    "volcengine_embedding",
    "volcengine_seed_asr",
]


@dataclass(frozen=True)
class ModelCard:
    """One selectable model, with everything needed to choose or reject it."""

    key: str  # stable identifier used in ACTIVE_STACK and env vars
    vendor: str
    model_id: str  # snapshot-pinned; never a rolling alias
    adapter: Adapter
    roles: tuple[Role, ...]
    status: Status = "challenger"

    # --- capabilities that change how the pipeline is built ---
    accepts_video: bool = False
    accepts_images: bool = False
    reads_video_audio: bool = False
    reliable_timestamps: bool = False
    sampling_fps: float | None = None
    max_video_seconds: float | None = None
    structured_output: bool = False
    tunable: bool = False
    context_tokens: int | None = None
    embedding_dim: int | None = None

    # --- cost and access ---
    price_in_per_mtok: float | None = None
    price_out_per_mtok: float | None = None
    price_per_video_minute: float | None = None
    # Free tiers are defined by their rate limit far more than by their price.
    # Analysing one project means hundreds of calls, so this number decides
    # whether a model is usable at all, not just how fast it is.
    requests_per_minute: int | None = None
    base_url_env: str | None = None
    api_key_env: str | None = None
    # Some vendors are addressed by an endpoint id created in their console
    # rather than by a public model name. Reading it from the environment
    # keeps ids that only exist in one account out of the source tree.
    model_id_env: str | None = None

    # True when content sent through this endpoint may be used by the vendor
    # for training or analysis - for example an endpoint enrolled in a
    # data-for-credits programme. This is not a note: real user footage must
    # never reach one of these, and that has to be enforced rather than
    # remembered.
    data_shared_with_vendor: bool = False

    notes: str = ""
    unknowns: tuple[str, ...] = field(default_factory=tuple)

    def is_usable_offline(self) -> bool:
        return self.adapter == "local_openclip"

    def resolved_model_id(self) -> str:
        """The id to send, taking an environment override into account."""
        if self.model_id_env:
            from_env = os.environ.get(self.model_id_env, "").strip()
            if from_env:
                return from_env
        return self.model_id


# ---------------------------------------------------------------------------
# Catalog
#
# Sourced from docs/MODEL_SELECTION.md. Entries marked with `unknowns` have
# facts that must be verified against the vendor console before the bake-off -
# they are explicitly not guesses to be filled in by code.
# ---------------------------------------------------------------------------

CATALOG: dict[str, ModelCard] = {}


def _add(card: ModelCard) -> ModelCard:
    CATALOG[card.key] = card
    return card


_add(
    ModelCard(
        key="local-openclip-vitb32",
        vendor="open_clip",
        model_id="ViT-B-32/laion2b_s34b_b79k",
        adapter="local_openclip",
        roles=(Role.EMBEDDING,),
        status="provisional",
        accepts_images=True,
        embedding_dim=512,
        notes=(
            "Runs locally on CPU: zero marginal cost, no quota, works in CI. "
            "The production recall model is a cloud embedding + rerank pair; "
            "both sit behind the same role so the swap is config-only."
        ),
    )
)

# --- video understanding (roles 1 and 2) ----------------------------------

_add(
    ModelCard(
        key="qwen3.7-plus",
        vendor="alibaba-bailian",
        model_id="qwen3.7-plus-2026-05-26",
        adapter="openai_compatible",
        roles=(Role.MATERIAL, Role.STYLE),
        status="provisional",
        accepts_video=True,
        accepts_images=True,
        reads_video_audio=False,  # audio must be routed to ASR separately
        reliable_timestamps=False,  # unverified for fast cuts; assume shot-first
        max_video_seconds=7200,
        structured_output=True,
        tunable=False,
        context_tokens=1_000_000,
        base_url_env="AIVE_BAILIAN_BASE_URL",
        api_key_env="AIVE_BAILIAN_API_KEY",
        notes=(
            "Provisional default for material/reference/planner. Does NOT read "
            "the audio track inside a video file, so ASR is a separate call. "
            "Cannot be fine-tuned, so it cannot be the trainable critic."
        ),
        unknowns=("sampling behaviour on sub-second shots", "China-region price snapshot"),
    )
)

_add(
    ModelCard(
        key="gemini-3.7-flash",
        vendor="google",
        model_id="gemini-3.7-flash",
        adapter="openai_compatible",
        roles=(
            Role.MATERIAL,
            Role.STYLE,
            Role.DIRECTOR,
            Role.RERANK,
            Role.REVIEWER,
            Role.ASR,
            Role.AUDIO,
        ),
        status="provisional",
        accepts_video=True,
        accepts_images=True,
        reads_video_audio=True,
        sampling_fps=1.0,  # the reason the reference pipeline is shot-first
        structured_output=True,
        context_tokens=1_000_000,
        # Conservative default for the free tier. Raise it once the real quota
        # is confirmed in the console; too low only costs time, too high costs
        # a run that dies half way through with 429s.
        requests_per_minute=10,
        base_url_env="AIVE_GOOGLE_BASE_URL",
        api_key_env="AIVE_GOOGLE_API_KEY",
        data_shared_with_vendor=True,
        notes=(
            "Current provisional choice for material understanding: a free "
            "tier makes it cheap to iterate on. Default 1 FPS video sampling "
            "is documented to lose fast motion and fast cuts, which is why the "
            "reference pipeline stays shot-first regardless. Expected to be "
            "replaced by a China-region model later - that is a one-line "
            "change to ACTIVE_STACK."
        ),
        unknowns=(
            "exact model id in the console",
            "free-tier requests per minute and per day",
            "cross-border data handling for China users",
        ),
    )
)

_add(
    ModelCard(
        key="gemini-3.8-flash",
        vendor="google",
        model_id="gemini-3.8-flash",
        adapter="openai_compatible",
        roles=(Role.DIRECTOR,),
        status="challenger",
        accepts_images=True,
        structured_output=False,
        context_tokens=1_000_000,
        requests_per_minute=10,
        base_url_env="AIVE_GOOGLE_BASE_URL",
        api_key_env="AIVE_GOOGLE_API_KEY",
        data_shared_with_vendor=True,
        notes=(
            "Pinned Gemini Flash Director candidate for internal demo footage. "
            "The OpenAI-compatible endpoint rejects the Director's composite "
            "JSON Schema, so the adapter uses schema-in-prompt JSON mode. "
            "Free-tier traffic may be used by the vendor to improve products."
        ),
        unknowns=(
            "free-tier requests per minute and per day for this account",
            "cross-border data handling for China users",
        ),
    )
)

_add(
    ModelCard(
        key="gemini-3.6-flash",
        vendor="google",
        model_id="gemini-3.6-flash",
        adapter="openai_compatible",
        roles=(Role.DIRECTOR,),
        status="challenger",
        accepts_images=True,
        structured_output=False,
        context_tokens=1_000_000,
        requests_per_minute=10,
        base_url_env="AIVE_GOOGLE_BASE_URL",
        api_key_env="AIVE_GOOGLE_API_KEY",
        data_shared_with_vendor=True,
        notes=(
            "Pinned Gemini Flash Director candidate for internal demo footage. "
            "Uses schema-in-prompt JSON mode because the endpoint rejects the "
            "Director's composite JSON Schema."
        ),
        unknowns=("free-tier requests per minute and per day for this account",),
    )
)

_add(
    ModelCard(
        key="gemini-3.5-flash",
        vendor="google",
        model_id="gemini-3.5-flash",
        adapter="openai_compatible",
        roles=(Role.DIRECTOR,),
        status="challenger",
        accepts_images=True,
        structured_output=False,
        context_tokens=1_000_000,
        requests_per_minute=10,
        base_url_env="AIVE_GOOGLE_BASE_URL",
        api_key_env="AIVE_GOOGLE_API_KEY",
        data_shared_with_vendor=True,
        notes=(
            "Pinned Gemini Flash Director fallback for internal demo footage. "
            "Uses schema-in-prompt JSON mode because the endpoint rejects the "
            "Director's composite JSON Schema."
        ),
        unknowns=("free-tier requests per minute and per day for this account",),
    )
)

_add(
    ModelCard(
        key="gemini-3.1-flash-lite",
        vendor="google",
        model_id="gemini-3.1-flash-lite",
        adapter="openai_compatible",
        roles=(Role.DIRECTOR,),
        status="challenger",
        accepts_images=True,
        structured_output=False,
        context_tokens=1_000_000,
        requests_per_minute=10,
        base_url_env="AIVE_GOOGLE_BASE_URL",
        api_key_env="AIVE_GOOGLE_API_KEY",
        data_shared_with_vendor=True,
        notes=(
            "Pinned lower-cost Gemini Director fallback candidate for internal "
            "demo footage; switching remains explicit and auditable. Uses "
            "schema-in-prompt JSON mode for the composite Director response."
        ),
        unknowns=(
            "free-tier requests per minute and per day for this account",
            "continuous-edit quality relative to Flash",
        ),
    )
)

_add(
    ModelCard(
        key="yt-vita",
        vendor="tencent",
        model_id="TBD-console-confirm",
        adapter="openai_compatible",
        roles=(Role.MATERIAL, Role.STYLE),
        status="challenger",
        accepts_video=True,
        reads_video_audio=True,
        notes="Mandatory wildcard; limited public specification.",
        unknowns=("structured output", "sampling strategy"),
    )
)

_add(
    ModelCard(
        key="qwen3.5-omni-plus",
        vendor="alibaba-bailian",
        model_id="qwen3.5-omni-plus-2026-03-15",
        adapter="openai_compatible",
        roles=(Role.MATERIAL,),
        status="auxiliary",
        accepts_video=True,
        reads_video_audio=True,
        sampling_fps=1.0,
        max_video_seconds=400,
        structured_output=False,
        context_tokens=262_144,
        base_url_env="AIVE_BAILIAN_BASE_URL",
        api_key_env="AIVE_BAILIAN_API_KEY",
        notes=(
            "Audio-visual mood/环境 helper only. No structured output, so it "
            "must never be the primary parser."
        ),
    )
)

# Volcengine Ark. Pro and Lite are separate cards even though they share one
# project API key: every persisted model call must say which pinned model and
# role actually made the decision.

_add(
    ModelCard(
        key="doubao-seed-2.1-pro",
        vendor="bytedance-volcengine",
        model_id="doubao-seed-2-1-pro-260915",
        adapter="openai_compatible",
        roles=(Role.DIRECTOR, Role.REVIEWER),
        status="provisional",
        accepts_images=True,
        accepts_video=True,
        reads_video_audio=True,
        structured_output=True,
        context_tokens=1_024_000,
        base_url_env="AIVE_ARK_BASE_URL",
        api_key_env="AIVE_ARK_API_KEY",
        notes=(
            "High-reasoning model for first cut, complex continuous edits, "
            "AutoFix decisions, and creative render review. The card is not "
            "frozen until a real investor-demo Golden Run passes."
        ),
    )
)

_add(
    ModelCard(
        key="doubao-seed-2.1-lite",
        vendor="bytedance-volcengine",
        model_id="doubao-seed-2-1-lite-260915",
        adapter="openai_compatible",
        roles=(
            Role.MATERIAL,
            Role.STYLE,
            Role.RERANK,
            Role.AUDIO,
        ),
        status="provisional",
        accepts_images=True,
        accepts_video=True,
        reads_video_audio=True,
        structured_output=True,
        context_tokens=1_024_000,
        requests_per_minute=30_000,
        base_url_env="AIVE_ARK_BASE_URL",
        api_key_env="AIVE_ARK_API_KEY",
        notes=(
            "Lower-cost multimodal model for source understanding, Style "
            "Blueprint extraction, raw-audio meaning, Moment grouping, and "
            "semantic reranking."
        ),
    )
)


# --- retrieval (role 4) ----------------------------------------------------

_add(
    ModelCard(
        key="doubao-embedding-vision-251215",
        vendor="bytedance-volcengine",
        model_id="doubao-embedding-vision-251215",
        adapter="volcengine_embedding",
        roles=(Role.EMBEDDING,),
        status="provisional",
        accepts_images=True,
        accepts_video=True,
        context_tokens=128_000,
        embedding_dim=2_048,
        requests_per_minute=15_000,
        base_url_env="AIVE_ARK_BASE_URL",
        api_key_env="AIVE_ARK_API_KEY",
        notes=(
            "Pinned cross-modal recall model. Image corpus and text-query "
            "instructions define one shared retrieval vector space."
        ),
    )
)

_add(
    ModelCard(
        key="qwen3-vl-embedding",
        vendor="alibaba-bailian",
        model_id="qwen3-vl-embedding",
        adapter="openai_compatible",
        roles=(Role.EMBEDDING,),
        status="provisional",
        accepts_images=True,
        accepts_video=True,
        embedding_dim=1024,
        base_url_env="AIVE_BAILIAN_BASE_URL",
        api_key_env="AIVE_BAILIAN_API_KEY",
        notes="Provisional production recall model; pairs with qwen3-vl-rerank.",
    )
)

_add(
    ModelCard(
        key="qwen3-vl-rerank",
        vendor="alibaba-bailian",
        model_id="qwen3-vl-rerank",
        adapter="openai_compatible",
        roles=(Role.RERANK,),
        status="provisional",
        base_url_env="AIVE_BAILIAN_BASE_URL",
        api_key_env="AIVE_BAILIAN_API_KEY",
        notes="Precision pass over the recalled top-k.",
    )
)

# --- speech ----------------------------------------------------------------

_add(
    ModelCard(
        key="seed-asr-2.0",
        vendor="bytedance-volcengine",
        model_id="volc.seedasr.auc",
        adapter="volcengine_seed_asr",
        roles=(Role.ASR,),
        status="provisional",
        reliable_timestamps=True,
        base_url_env="AIVE_SEED_ASR_BASE_URL",
        api_key_env="AIVE_SEED_ASR_API_KEY",
        notes=(
            "Recording-file recognition with utterance timestamps, speaker, "
            "language, and emotion evidence. Uses a separate Doubao Voice key "
            "and requires a short-lived remotely fetchable audio URL."
        ),
    )
)

_add(
    ModelCard(
        key="qwen-asr",
        vendor="alibaba-bailian",
        model_id="qwen3-asr-flash",
        adapter="openai_compatible",
        roles=(Role.ASR,),
        status="provisional",
        base_url_env="AIVE_BAILIAN_BASE_URL",
        api_key_env="AIVE_BAILIAN_API_KEY",
        notes=(
            "OpenAI-compatible Base64 audio transcription. The endpoint does "
            "not return timestamps, so the media service calls it with bounded "
            "physical-shot audio windows and owns the source timing."
        ),
        unknowns=("accuracy on noisy handheld footage",),
    )
)


# ---------------------------------------------------------------------------
# Active stack - the actual selection
#
# THIS IS THE LINE YOU EDIT TO SWAP A MODEL. The Director deliberately points
# at a real model card: model-authored editing must fail visibly when its
# credentials or output are invalid, never fall back to a local rule policy.
# Every creative role points at a real provider. Test fixtures are injected
# from the test suite and are never registered as runtime fallbacks.
# ---------------------------------------------------------------------------

ACTIVE_STACK: dict[Role, str] = {
    Role.MATERIAL: "doubao-seed-2.1-lite",
    Role.STYLE: "doubao-seed-2.1-lite",
    Role.EMBEDDING: "doubao-embedding-vision-251215",
    Role.RERANK: "doubao-seed-2.1-lite",
    Role.DIRECTOR: "doubao-seed-2.1-pro",
    Role.REVIEWER: "doubao-seed-2.1-pro",
    Role.ASR: "seed-asr-2.0",
    Role.AUDIO: "doubao-seed-2.1-lite",
}


def _env_override(role: Role) -> str | None:
    """AIVE_ROLE_<ROLE>_MODEL overrides ACTIVE_STACK for one role."""
    return os.environ.get(f"AIVE_ROLE_{role.name}_MODEL")


def active_key(role: Role) -> str:
    key = _env_override(role) or ACTIVE_STACK.get(role)
    if key is None:
        raise KeyError(f"no model selected for role {role.value!r}")
    return key


def card_for(role: Role, key: str | None = None) -> ModelCard:
    """The ModelCard currently serving ``role``."""
    resolved = key or active_key(role)
    card = CATALOG.get(resolved)
    if card is None:
        raise KeyError(
            f"unknown model key {resolved!r}; known: {sorted(CATALOG)}"
        )
    if role not in card.roles:
        raise ValueError(
            f"model {resolved!r} is not declared for role {role.value!r} "
            f"(declared: {[r.value for r in card.roles]})"
        )
    return card


def candidates_for(role: Role) -> list[ModelCard]:
    return [c for c in CATALOG.values() if role in c.roles]


def open_unknowns() -> dict[str, tuple[str, ...]]:
    """Facts that must be confirmed against a vendor console before freezing."""
    return {c.key: c.unknowns for c in CATALOG.values() if c.unknowns}


class DataPolicyViolation(RuntimeError):
    """Real user content was about to go through a data-sharing endpoint."""


def check_data_policy(role: Role, contains_user_data: bool) -> None:
    """Refuse to send user content through an endpoint that shares it.

    Configuration discipline is not enough here. Someone debugging a user's
    report will run their footage through whatever is currently selected, and
    if that happens to be the reward endpoint the material has been authorised
    away with nothing to notice it. So the combination raises instead.
    """
    if not contains_user_data:
        return
    card = card_for(role)
    if card.data_shared_with_vendor:
        raise DataPolicyViolation(
            f"{card.key} shares its traffic with {card.vendor} (reward plan or "
            f"similar), so user content must not go through it. Switch "
            f"AIVE_ROLE_{role.name}_MODEL to a non-sharing endpoint, or mark "
            f"this project as internal test material."
        )


def stack_summary() -> list[dict[str, str]]:
    rows = []
    for role in Role:
        try:
            card = card_for(role)
        except KeyError:
            rows.append({"role": role.value, "model": "-", "status": "unselected"})
            continue
        rows.append(
            {
                "role": role.value,
                "key": card.key,
                "model": card.resolved_model_id(),
                "vendor": card.vendor,
                "adapter": card.adapter,
                "status": card.status,
                "shared": card.data_shared_with_vendor,
            }
        )
    return rows
