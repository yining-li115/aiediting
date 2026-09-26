"""Runtime configuration.

One provider setting per model role. There is deliberately no global
``AIVE_MODEL``: a single global model setting bakes "every role uses the same
model" into the architecture, and that is exactly the mistake the role split
exists to prevent.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env into the real process environment, not only into Settings.
#
# Vendor credentials are looked up by name from each ModelCard
# (base_url_env / api_key_env), so they must reach os.environ. Without this,
# .env configures the application but not the model access it exists to
# configure - which fails in the confusing way of "the file is right and
# nothing works".
load_dotenv(override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AIVE_", env_file=".env", extra="ignore"
    )

    db_url: str = "sqlite:///./data/aive.db"

    # Where uploaded and generated files live. Everything a project owns sits
    # under one prefix, so deleting a project or measuring a user's usage is a
    # single operation.
    storage_backend: str = "local"
    storage_root: Path = Path("./data/storage")

    media_root: Path = Path("./data/media")
    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"
    remotion_runtime_root: Path = Path("./apps/remotion")

    # Model selection lives in aive.model_registry, not here. Per-role
    # overrides go through AIVE_ROLE_<ROLE>_MODEL, which that module reads.

    # What the material model is shown for each segment.
    #   frame   the middle keyframe only
    #   frames  first, middle and last keyframes
    #   clip    the segment itself, cut from the analysis proxy
    # A still cannot show which moment in a shot is the good one, which is
    # exactly what interestingness and worth_using are judging. Models that
    # do not accept video fall back to frames automatically.
    material_input: str = "frame"

    # --- generation defaults ---
    candidate_count: int = 6  # N in best-of-N
    top_k_proxy: int = 3  # K survivors that get a proxy render
    default_target_duration: float = 45.0
    seed: int = 41

    workdir: Path = Field(default=Path("./data/processed"))


settings = Settings()
