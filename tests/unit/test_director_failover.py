from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from aive.agent.provider import DirectorFailoverProvider
from aive.contracts.conversation import AgentStepDecision
from aive.providers import get_provider
from aive.providers.openai_compatible import QuotaExhausted
from aive.roles import Role


class StubDirector:
    version = "1"

    def __init__(self, name: str, outcomes: list[object]) -> None:
        self.name = name
        self.outcomes = outcomes
        self.calls = 0
        self.max_retries = 5
        self.client = SimpleNamespace(retry_rate_limit_once=True)

    async def next_edit_step(self, context, steps):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _finished(response: str) -> AgentStepDecision:
    return AgentStepDecision(kind="finish", response=response)


async def test_failover_uses_next_model_after_quota_and_observes_cooldown() -> None:
    primary = StubDirector("gemini-primary", [QuotaExhausted("quota")])
    fallback = StubDirector(
        "gemini-fallback",
        [_finished("first"), _finished("second")],
    )
    provider = DirectorFailoverProvider(
        (primary, fallback),
        cooldown_seconds=300,
        clock=lambda: 100,
    )

    first = await provider.next_edit_step(object(), ())
    second = await provider.next_edit_step(object(), ())

    assert first.response == "first"
    assert second.response == "second"
    assert primary.calls == 1
    assert fallback.calls == 2
    assert primary.max_retries == 2
    assert primary.client.retry_rate_limit_once is False


async def test_failover_does_not_hide_invalid_model_output() -> None:
    primary = StubDirector("gemini-primary", [ValueError("invalid Patch")])
    fallback = StubDirector("gemini-fallback", [_finished("must not run")])
    provider = DirectorFailoverProvider((primary, fallback))

    with pytest.raises(ValueError, match="invalid Patch"):
        await provider.next_edit_step(object(), ())

    assert fallback.calls == 0


async def test_failover_revisits_transient_5xx_after_short_cooldown() -> None:
    clock = [100.0]
    request = httpx.Request("POST", "https://example.invalid/chat/completions")
    response = httpx.Response(503, request=request)
    capacity = httpx.HTTPStatusError(
        "temporarily unavailable",
        request=request,
        response=response,
    )
    primary = StubDirector(
        "gemini-primary",
        [capacity, _finished("primary recovered")],
    )
    fallback = StubDirector(
        "gemini-fallback",
        [_finished("fallback"), _finished("fallback still active")],
    )
    provider = DirectorFailoverProvider(
        (primary, fallback),
        cooldown_seconds=300,
        transient_cooldown_seconds=15,
        clock=lambda: clock[0],
    )

    assert (await provider.next_edit_step(object(), ())).response == "fallback"
    clock[0] = 110
    assert (
        await provider.next_edit_step(object(), ())
    ).response == "fallback still active"
    clock[0] = 116
    assert (
        await provider.next_edit_step(object(), ())
    ).response == "primary recovered"


def test_registry_builds_an_explicit_pinned_director_chain(monkeypatch) -> None:
    monkeypatch.setenv("AIVE_ROLE_DIRECTOR_MODEL", "gemini-3.6-flash")
    monkeypatch.setenv(
        "AIVE_ROLE_DIRECTOR_FALLBACK_MODELS",
        "gemini-3.5-flash,gemini-3.1-flash-lite",
    )

    provider = get_provider(Role.DIRECTOR)

    assert isinstance(provider, DirectorFailoverProvider)
    assert [candidate.name for candidate in provider.providers] == [
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ]


def test_explicit_bakeoff_model_ignores_runtime_fallback_chain(monkeypatch) -> None:
    monkeypatch.setenv(
        "AIVE_ROLE_DIRECTOR_FALLBACK_MODELS",
        "gemini-3.5-flash,gemini-3.1-flash-lite",
    )

    provider = get_provider(Role.DIRECTOR, "gemini-3.6-flash")

    assert provider.name == "gemini-3.6-flash"
    assert not isinstance(provider, DirectorFailoverProvider)
