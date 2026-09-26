"""Providers must be deterministic, accounted for, and swappable by config."""

from __future__ import annotations

import pytest

from aive.providers import Role, get_provider, set_cost_sink
from aive.providers.base import CostRecord
from aive.specs.segment import RawSegment


@pytest.fixture
def cost_log():
    records: list[CostRecord] = []
    set_cost_sink(records.append)
    yield records
    set_cost_sink(None)


async def test_material_provider_is_deterministic() -> None:
    provider = get_provider(Role.MATERIAL)
    segment = RawSegment(
        segment_id="asset001_seg01", project_id="p", asset_id="asset_001",
        start=0.0, end=2.0,
    )
    first = await provider.analyze_segment(segment, [])
    second = await provider.analyze_segment(segment, [])
    assert first == second, "same input must always give the same output"


async def test_every_call_is_accounted_for(cost_log) -> None:
    provider = get_provider(Role.MATERIAL, project_id="proj_1")
    await provider.analyze_segment(
        RawSegment(segment_id="s1", project_id="p", asset_id="a", start=0, end=1.0), []
    )
    assert len(cost_log) == 1
    record = cost_log[0]
    assert record.role == "material"
    assert record.project_id == "proj_1"
    assert record.latency_ms is not None
    assert record.seconds_processed == pytest.approx(1.0)


async def test_planner_candidates_differ_and_hit_the_target() -> None:
    provider = get_provider(Role.PLANNER)
    plans = await provider.generate_plans(
        {"project_id": "p", "target_duration": 45.0}, n=6, seed=41
    )
    assert len(plans) == 6
    for plan in plans:
        assert plan.planned_duration() == pytest.approx(45.0, abs=0.3)
    # Candidates must differ by construction, not by sampling noise.
    shapes = {(len(p.slots), round(p.slots[0].target_duration, 2)) for p in plans}
    assert len(shapes) >= 3


async def test_embeddings_are_normalised() -> None:
    import numpy as np

    provider = get_provider(Role.EMBEDDING)
    vectors = await provider.embed_texts(["a", "b", "c"])
    assert vectors.shape == (3, 512)
    assert np.allclose(np.linalg.norm(vectors, axis=1), 1.0, atol=1e-5)


async def test_passthrough_critic_reports_tie() -> None:
    critic = get_provider(Role.CRITIC)
    assert (await critic.compare({}, {}, {}))["choice"] == "tie"


def test_unknown_provider_names_are_rejected() -> None:
    with pytest.raises(KeyError):
        get_provider(Role.MATERIAL, "no-such-vendor")


def test_gemini_director_is_selected_through_the_existing_provider_port() -> None:
    from aive.agent.provider import OpenAICompatibleDirectorProvider

    provider = get_provider(Role.DIRECTOR, "gemini-3.8-flash")

    assert isinstance(provider, OpenAICompatibleDirectorProvider)
    assert provider.name == "gemini-3.8-flash"
    assert provider.model == "gemini-3.8-flash"


# --- provenance ------------------------------------------------------------


async def test_provenance_records_which_model_said_what() -> None:
    """"Why is this shot a close_up?" must be answerable from the row."""
    from aive.providers import capture_provenance

    provider = get_provider(Role.MATERIAL)
    segment = RawSegment(
        segment_id="s1", project_id="p", asset_id="a", start=0.0, end=2.0
    )
    with capture_provenance() as evidence:
        await provider.analyze_segment(segment, [])

    assert evidence["provider"] == "mock"
    assert evidence["model"] == "mock-v1"
    assert "s1" in evidence["raw_response"]


async def test_concurrent_calls_do_not_swap_evidence() -> None:
    """One provider instance serves many concurrent calls.

    Keeping the last response on the instance meant whichever call finished
    last overwrote the rest, and every segment ended up carrying some other
    segment's evidence - which looks like working provenance until you read it.
    """
    import asyncio

    from aive.providers import capture_provenance

    provider = get_provider(Role.MATERIAL)

    async def one(index: int) -> tuple[str, str]:
        segment = RawSegment(
            segment_id=f"seg_{index}", project_id="p", asset_id="a",
            start=0.0, end=1.0,
        )
        with capture_provenance() as evidence:
            await provider.analyze_segment(segment, [])
        return segment.segment_id, evidence["raw_response"]

    results = await asyncio.gather(*(one(i) for i in range(12)))
    for segment_id, raw in results:
        assert segment_id in raw, f"{segment_id} carries {raw!r}"


async def test_uncollected_provenance_does_not_break_the_call() -> None:
    """Pipeline code runs from the CLI too, where nobody is collecting."""
    provider = get_provider(Role.MATERIAL)
    result = await provider.analyze_segment(
        RawSegment(segment_id="s", project_id="p", asset_id="a", start=0, end=1),
        [],
    )
    assert result.segment_id == "s"
