"""Deterministic offline implementations of non-Director test roles.

These exist so that the entire pipeline skeleton can be built and tested
before the model bake-off freezes the real stack, and so that CI never needs
network access, API quota or money.

Everything is seeded from a hash of the input, so the same input always
produces the same output - which is also what makes ablations meaningful.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np

from ..specs.base import new_id
from ..specs.edit_plan import EditPlanSpec, Intent, Section, Slot, Story
from ..specs.enums import CameraMotion, SemanticRole, ShotType
from ..specs.operations import EditCommandPlan, EditOperation
from ..specs.segment import MaterialSegment, RawSegment
from ..contracts.moments import (
    MomentGroupDecision,
    ShotObservation,
    TranscriptDraft,
)
from .base import BaseProvider, Role

_ROLES: tuple[SemanticRole, ...] = (
    "opening_environment",
    "environment",
    "person_activity",
    "detail",
    "food",
    "action",
    "ending_environment",
)
_SHOTS: tuple[ShotType, ...] = ("wide", "medium", "close_up", "extreme_close_up")
_MOTIONS: tuple[CameraMotion, ...] = ("static", "handheld", "slow_pan", "tracking")


def _seed_of(*parts: str) -> int:
    digest = hashlib.sha256("|".join(parts).encode()).digest()
    return int.from_bytes(digest[:8], "big")


def _rng(*parts: str) -> np.random.Generator:
    return np.random.default_rng(_seed_of(*parts) % (2**32))


class MockMaterialProvider(BaseProvider):
    role = Role.MATERIAL
    name = "mock"

    async def analyze_segment(
        self,
        segment: RawSegment,
        keyframes: list[Path],
        clip: Path | None = None,
    ) -> MaterialSegment:
        async def _run() -> MaterialSegment:
            rng = _rng(segment.segment_id)
            role = _ROLES[int(rng.integers(len(_ROLES)))]
            shot = _SHOTS[int(rng.integers(len(_SHOTS)))]
            # Recorded as loudly as a real one: a row whose provenance says
            # "mock" is the difference between a label and a placeholder.
            self.report_provenance(
                raw_response=f"mock: seeded from {segment.segment_id}",
            )
            return MaterialSegment(
                segment_id=segment.segment_id,
                project_id=segment.project_id,
                source_id=segment.asset_id,
                start=segment.start,
                end=segment.end,
                semantic=f"mock description of a {shot} {role} shot",
                semantic_role=role,
                shot_type=shot,
                people=int(rng.integers(0, 3)),
                interestingness=float(rng.uniform(0.3, 0.95)),
                emotion=["warm"] if rng.random() > 0.5 else ["calm"],
                keyframe_keys=[],
            )

        return await self._guarded(_run, seconds_processed=segment.duration)

    async def group_moments(
        self, shots: tuple[ShotObservation, ...]
    ) -> tuple[MomentGroupDecision, ...]:
        """Group consecutive speech shots; silent shots stay independently usable."""
        groups: list[MomentGroupDecision] = []
        index = 0
        while index < len(shots):
            current = shots[index]
            run = [current]
            if current.transcript:
                while index + 1 < len(shots) and shots[index + 1].transcript:
                    index += 1
                    run.append(shots[index])
            descriptions = [
                item.visual_semantic for item in run if item.visual_semantic
            ]
            transcript = " ".join(
                item.transcript or "" for item in run
            ).strip()
            groups.append(
                MomentGroupDecision(
                    shot_ids=tuple(item.shot_id for item in run),
                    semantic=(
                        transcript
                        or "; ".join(descriptions)
                        or "unlabelled visual moment"
                    )[:500],
                    semantic_role=run[0].semantic_role,
                )
            )
            index += 1
        self.report_provenance(raw_response="mock: grouped consecutive speech shots")
        return tuple(groups)


class MockReferenceProvider(BaseProvider):
    """Reference understanding.

    ``analyze_segment`` is the one the shot-first pipeline uses: with the cuts
    already known, the question per shot is the same as for user footage.
    ``analyze`` is the whole-video path, kept for the case where a model turns
    out to have reliable timestamps (section 0.5, path A).
    """

    role = Role.REFERENCE
    name = "mock"

    async def analyze_segment(
        self,
        segment: RawSegment,
        keyframes: list[Path],
        clip: Path | None = None,
    ) -> MaterialSegment:
        return await MockMaterialProvider(
            project_id=self.project_id
        ).analyze_segment(segment, keyframes, clip)

    async def analyze(
        self, video: Path, shots: list[tuple[float, float]] | None = None
    ) -> dict[str, Any]:
        async def _run() -> dict[str, Any]:
            rng = _rng(str(video))
            spans = shots or [(i * 1.2, (i + 1) * 1.2) for i in range(12)]
            return {
                "reference_id": new_id("ref"),
                "duration": spans[-1][1],
                "shot_count": len(spans),
                "shots": [
                    {
                        "index": i,
                        "start": s,
                        "end": e,
                        "shot_type": _SHOTS[int(rng.integers(len(_SHOTS)))],
                        "semantic_role": _ROLES[int(rng.integers(len(_ROLES)))],
                        "motion": _MOTIONS[int(rng.integers(len(_MOTIONS)))],
                        "energy": float(rng.uniform(0.2, 0.9)),
                        "transition_out": "hard_cut",
                        "has_text": False,
                    }
                    for i, (s, e) in enumerate(spans)
                ],
                "style_tags": ["travel", "clean"],
            }

        return await self._guarded(_run)


class MockIntentProvider(BaseProvider):
    """Role 3a without a model.

    Delegates to the rule parser rather than inventing an answer: a mock that
    returns something a rule pass would not is a mock that hides bugs, and the
    rule parser is the real fallback anyway.
    """

    role = Role.INTENT
    name = "mock"

    async def parse_intent(self, prompt: str) -> Intent:
        async def _run() -> Intent:
            from ..planner.intent import parse_intent_rules  # noqa: PLC0415

            self._finish()
            return parse_intent_rules(prompt)

        return await self._guarded(_run)


class MockPlannerProvider(BaseProvider):
    """Produces structurally valid plans with the same explicit perturbation
    strategy the real planner uses, so downstream code can be exercised without
    a model."""

    role = Role.PLANNER
    name = "mock"

    async def generate_plans(
        self, context: dict[str, Any], n: int, seed: int
    ) -> list[EditPlanSpec]:
        async def _run() -> list[EditPlanSpec]:
            project_id = context.get("project_id", "proj_mock")
            conditioning = context.get("conditioning")
            target = float(
                context.get("target_duration")
                or (conditioning.target_duration if conditioning else 45.0)
            )
            plans: list[EditPlanSpec] = []
            for index in range(n):
                # Candidates differ by construction, not by sampling
                # temperature: variant 0 is the baseline, then faster, slower,
                # restructured, and so on.
                pace_factor = [1.0, 0.75, 1.35, 1.0, 0.9, 1.2][index % 6]
                base_shot = 1.4 * pace_factor
                count = max(3, int(round(target / base_shot)))
                slots = [
                    Slot(
                        slot_id=f"slot_{i + 1:02d}",
                        order=i,
                        section="opening" if i == 0 else "body",
                        semantic_role=_ROLES[i % len(_ROLES)],
                        shot_type=_SHOTS[i % len(_SHOTS)],
                        target_duration=base_shot,
                        energy=0.4 + 0.05 * (i % 6),
                    )
                    for i in range(count)
                ]
                plan = EditPlanSpec(
                    project_id=project_id,
                    intent=context.get("intent") or Intent(target_duration=target),
                    story=Story(
                        sections=[
                            Section(name="opening", target_duration=target * 0.2),
                            Section(name="body", target_duration=target * 0.8),
                        ]
                    ),
                    slots=slots,
                )
                plan.provenance.candidate_index = index
                plan.provenance.seed = seed + index
                plan.provenance.model = "mock"
                # The code owns the arithmetic, never the model.
                plan.normalise_durations()
                plans.append(plan)
            return plans

        return await self._guarded(_run)


class MockEmbeddingProvider(BaseProvider):
    role = Role.EMBEDDING
    name = "mock"
    dim = 512

    async def embed_images(self, images: list[Path]) -> np.ndarray:
        async def _run() -> np.ndarray:
            return self._vectors([str(p) for p in images])

        return await self._guarded(_run)

    async def embed_texts(self, texts: list[str]) -> np.ndarray:
        async def _run() -> np.ndarray:
            return self._vectors(texts)

        return await self._guarded(_run)

    def _vectors(self, keys: list[str]) -> np.ndarray:
        out = np.zeros((len(keys), self.dim), dtype=np.float32)
        for i, key in enumerate(keys):
            vec = _rng(key).normal(size=self.dim).astype(np.float32)
            out[i] = vec / (np.linalg.norm(vec) + 1e-9)
        return out


class MockRerankProvider(BaseProvider):
    role = Role.RERANK
    name = "mock"

    async def rerank(self, query: str, candidates: list[str]) -> list[float]:
        async def _run() -> list[float]:
            return [float(_rng(query, c).uniform(0, 1)) for c in candidates]

        return await self._guarded(_run)


class PassthroughCritic(BaseProvider):
    """Placeholder that keeps the call site stable until a real critic exists.

    Always reports a tie, so best-of-N degrades to "take the first candidate"
    without any caller having to special-case its absence.
    """

    role = Role.CRITIC
    name = "passthrough"

    async def compare(
        self, a: dict[str, Any], b: dict[str, Any], context: dict[str, Any]
    ) -> dict[str, Any]:
        async def _run() -> dict[str, Any]:
            return {"choice": "tie", "reasons": [], "confidence": 0.0}

        return await self._guarded(_run)


class MockAgentProvider(BaseProvider):
    role = Role.AGENT_FAST
    name = "mock"

    async def resolve(
        self, instruction: str, state: dict[str, Any], conversation: list[dict]
    ) -> EditCommandPlan:
        async def _run() -> EditCommandPlan:
            return EditCommandPlan(
                instruction=instruction,
                operations=[EditOperation(operation="inspect_project", origin="chat")],
                explanation="mock agent: no-op",
            )

        return await self._guarded(_run)


class MockASRProvider(BaseProvider):
    role = Role.ASR
    name = "mock"

    async def transcribe(self, audio: Path) -> tuple[TranscriptDraft, ...]:
        async def _run() -> tuple[TranscriptDraft, ...]:
            return ()

        return await self._guarded(_run)
