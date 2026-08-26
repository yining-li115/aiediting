"""Local CLIP embedding adapter.

Registered as an EMBEDDING-role adapter, so choosing it is a catalog decision
like any other model. Running locally means zero marginal cost, no quota and
no network - which is what lets the whole analysis pipeline run in CI and
during offline iteration.

Torch is imported lazily: the deterministic half of material understanding
must not require a multi-gigabyte install to be importable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from ..providers.base import BaseProvider
from ..roles import Role

_MODEL_CACHE: dict[str, tuple[Any, Any, Any]] = {}


def _load(model_id: str):
    """Load and cache (model, preprocess, tokenizer) for one model id."""
    if model_id in _MODEL_CACHE:
        return _MODEL_CACHE[model_id]

    import open_clip  # noqa: PLC0415 - deliberate lazy import
    import torch  # noqa: PLC0415

    name, _, pretrained = model_id.partition("/")
    model, _, preprocess = open_clip.create_model_and_transforms(
        name, pretrained=pretrained or None
    )
    model.eval()
    torch.set_num_threads(max(1, (torch.get_num_threads() or 4) // 2))
    tokenizer = open_clip.get_tokenizer(name)
    _MODEL_CACHE[model_id] = (model, preprocess, tokenizer)
    return _MODEL_CACHE[model_id]


class LocalOpenClipProvider(BaseProvider):
    """CPU CLIP embeddings, L2-normalised, float32."""

    role = Role.EMBEDDING
    name = "local-openclip-vitb32"
    dim = 512

    def __init__(self, model_id: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.model_id = model_id or "ViT-B-32/laion2b_s34b_b79k"

    async def embed_images(self, images: list[Path]) -> np.ndarray:
        async def _run() -> np.ndarray:
            import torch  # noqa: PLC0415
            from PIL import Image  # noqa: PLC0415

            if not images:
                return np.zeros((0, self.dim), dtype=np.float32)
            model, preprocess, _ = _load(self.model_id)
            batch = torch.stack(
                [preprocess(Image.open(p).convert("RGB")) for p in images]
            )
            with torch.no_grad():
                features = model.encode_image(batch)
            return self._normalise(features.cpu().numpy())

        return await self._guarded(_run)

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        async def _run() -> np.ndarray:
            import torch  # noqa: PLC0415

            if not texts:
                return np.zeros((0, self.dim), dtype=np.float32)
            model, _, tokenizer = _load(self.model_id)
            with torch.no_grad():
                features = model.encode_text(tokenizer(texts))
            return self._normalise(features.cpu().numpy())

        return await self._guarded(_run)

    @staticmethod
    def _normalise(matrix: np.ndarray) -> np.ndarray:
        matrix = matrix.astype(np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        return matrix / np.clip(norms, 1e-9, None)


def segment_vector(frame_vectors: np.ndarray) -> np.ndarray:
    """Average the keyframe vectors, then re-normalise.

    A segment is represented by its whole span rather than one instant, so
    that a shot with camera movement retrieves sensibly.
    """
    if frame_vectors.size == 0:
        return np.zeros(0, dtype=np.float32)
    mean = frame_vectors.mean(axis=0)
    return (mean / max(float(np.linalg.norm(mean)), 1e-9)).astype(np.float32)


def to_blob(vector: np.ndarray) -> bytes:
    return np.asarray(vector, dtype=np.float32).tobytes()


def from_blob(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)
