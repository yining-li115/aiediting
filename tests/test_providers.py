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
