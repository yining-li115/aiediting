"""Model and signal analysis for already-authorized uploaded audio Assets."""

from __future__ import annotations

import asyncio
import tempfile
from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from ..contracts.audio import AudioAssetAnalysis, AudioSignalAnalysis
from ..contracts.moments import AudioEvidence, AudioUnderstanding, MediaSourceSpan
from ..contracts.project_doc import ProjectDoc
from ..providers.roles import AudioUnderstandingProvider
from ..storage import StorageProvider
from .analysis import audio_windows
from .audio import extract_audio_window
from .audio_signal import AUDIO_SIGNAL_DETECTOR, analyze_audio_signal
from .repository import (
    AssetProcessingRecord,
    MediaAssetRepository,
    ProjectAssetNotFound,
)


AudioExtractor = Callable[[Path, float, float, Path], Path]
SignalAnalyzer = Callable[[Path], AudioSignalAnalysis]


class AudioAssetAnalysisUnavailable(RuntimeError):
    pass


class AudioAssetAnalysisApplication:
    """Resolve cached, evidence-bound analysis for uploaded audio Assets."""

    def __init__(
        self,
        *,
        storage: StorageProvider,
        assets: MediaAssetRepository,
        provider: AudioUnderstandingProvider,
        audio_extractor: AudioExtractor = extract_audio_window,
        signal_analyzer: SignalAnalyzer = analyze_audio_signal,
    ) -> None:
        self._storage = storage
        self._assets = assets
        self._provider = provider
        self._audio_extractor = audio_extractor
        self._signal_analyzer = signal_analyzer

    async def resolve(
        self,
        document: ProjectDoc,
        statuses: tuple[AssetProcessingRecord, ...],
    ) -> tuple[AudioAssetAnalysis, ...]:
        by_id = {status.asset_id: status for status in statuses}
        resolved: list[AudioAssetAnalysis] = []
        for asset in document.assets:
            if asset.kind != "audio" or not ({"music", "sfx"} & set(asset.roles)):
                continue
            status = by_id.get(asset.asset_id)
            if status is None or status.project_id != document.project_id:
                raise ProjectAssetNotFound(
                    f"audio Asset {asset.asset_id!r} has no project media record"
                )
            if status.readiness != "ready" or not status.has_audio:
                raise AudioAssetAnalysisUnavailable(
                    f"audio Asset {asset.asset_id!r} is not ready for analysis"
                )
            identity = self._assets.get_asset(asset.asset_id)
            if identity is None:
                raise ProjectAssetNotFound(
                    f"audio Asset {asset.asset_id!r} is not registered"
                )
            basis_hash = _basis_hash(identity.sha256, self._provider)
            cached = status.audio_analysis
            if cached is not None and cached.basis_hash == basis_hash:
                resolved.append(cached)
                continue
            analysis = await self._analyze(
                status,
                content_hash=identity.sha256,
                basis_hash=basis_hash,
            )
            self._assets.save_audio_analysis(analysis)
            resolved.append(analysis)
        return tuple(resolved)

    async def _analyze(
        self,
        status: AssetProcessingRecord,
        *,
        content_hash: str,
        basis_hash: str,
    ) -> AudioAssetAnalysis:
        source = await asyncio.to_thread(
            self._storage.open_local,
            status.storage_key,
        )
        with tempfile.TemporaryDirectory(
            prefix=f"aive_audio_{status.asset_id}_"
        ) as raw:
            root = Path(raw)
            complete = await asyncio.to_thread(
                self._audio_extractor,
                source,
                0.0,
                status.duration_seconds,
                root / "complete.wav",
            )
            signal = await asyncio.to_thread(self._signal_analyzer, complete)
            understandings: list[AudioUnderstanding] = []
            for ordinal, (start, end) in enumerate(
                audio_windows(0.0, signal.duration_ms / 1_000)
            ):
                window = await asyncio.to_thread(
                    self._audio_extractor,
                    complete,
                    start,
                    end,
                    root / f"window_{ordinal:03d}.wav",
                )
                source_span = MediaSourceSpan(
                    start=round(start * 1_000),
                    end=min(signal.duration_ms, max(1, round(end * 1_000))),
                    timescale=1_000,
                )
                evidence = AudioEvidence(
                    evidence_id=_evidence_id(status.asset_id, ordinal, source_span),
                    project_id=status.project_id,
                    asset_id=status.asset_id,
                    source=source_span,
                )
                understandings.append(
                    await self._provider.understand_audio(evidence, window)
                )
        return AudioAssetAnalysis(
            project_id=status.project_id,
            asset_id=status.asset_id,
            content_hash=content_hash,
            basis_hash=basis_hash,
            signal=signal,
            understandings=tuple(understandings),
            analyzed_at=datetime.now(timezone.utc),
        )


def _basis_hash(content_hash: str, provider: AudioUnderstandingProvider) -> str:
    identity = (
        content_hash,
        AUDIO_SIGNAL_DETECTOR,
        str(getattr(provider, "name", provider.__class__.__name__)),
        str(getattr(provider, "model", "unknown")),
        str(getattr(provider, "prompt_version", "unknown")),
    )
    return sha256("\x1f".join(identity).encode("utf-8")).hexdigest()


def _evidence_id(
    asset_id: str,
    ordinal: int,
    source: MediaSourceSpan,
) -> str:
    digest = sha256(
        f"{asset_id}\x1f{ordinal}\x1f{source.start}\x1f{source.end}".encode("utf-8")
    ).hexdigest()[:24]
    return f"audio_evidence_{digest}"
