"""Provider base: roles, cost accounting, retries.

Business code never imports a vendor SDK and never names a vendor. It asks the
registry for a role and gets back something implementing that role's protocol.

Models decide what the system can do. Models must not decide how it is built.
"""

from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, TypeVar

from ..roles import Role
from ..specs.base import new_id

T = TypeVar("T")

# Per-task, so concurrent calls through one provider instance cannot overwrite
# each other's record of what the model said.
#
# The variable holds a *mutable dict that the caller installs*, and the
# provider fills it in place. Setting a new value would not work: retries run
# the call inside asyncio.wait_for, which copies the context into a new task,
# and a `set()` in there never reaches the caller. Mutating a shared object
# does, because both contexts point at the same dict.
_PROVENANCE: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "aive_provenance", default=None
)


@contextmanager
def capture_provenance() -> Iterator[dict[str, Any]]:
    """Collect the provenance of whatever call happens inside this block.

        with capture_provenance() as evidence:
            result = await provider.analyze_segment(...)
        # evidence now holds the model, prompt version and raw response
    """
    sink: dict[str, Any] = {}
    token = _PROVENANCE.set(sink)
    try:
        yield sink
    finally:
        _PROVENANCE.reset(token)

__all__ = [
    "Role",
    "capture_provenance",
    "CostRecord",
    "BaseProvider",
    "NonRetryable",
    "set_cost_sink",
    "record_cost",
]


class NonRetryable(Exception):
    """An error that repeating the call cannot fix.

    Missing credentials, an unknown model id, a malformed request: retrying
    these three times only delays the report and triples the log noise.
    """


class CostRecord:
    """One accounted call. Written to ``api_calls`` by the sink."""

    __slots__ = (
        "id",
        "project_id",
        "role",
        "provider",
        "model",
        "tokens_in",
        "tokens_out",
        "seconds_processed",
        "cost_estimate",
        "latency_ms",
        "retries",
    )

    def __init__(self, **kw: Any) -> None:
        self.id = kw.pop("id", None) or new_id("call")
        self.project_id = kw.pop("project_id", None)
        self.role = kw.pop("role", "")
        self.provider = kw.pop("provider", "")
        self.model = kw.pop("model", None)
        self.tokens_in = kw.pop("tokens_in", None)
        self.tokens_out = kw.pop("tokens_out", None)
        self.seconds_processed = kw.pop("seconds_processed", None)
        self.cost_estimate = kw.pop("cost_estimate", None)
        self.latency_ms = kw.pop("latency_ms", None)
        self.retries = kw.pop("retries", 0)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__slots__}


# Swapped for a DB-backed sink at startup; a list keeps tests dependency-free.
_cost_sink: Callable[[CostRecord], None] | None = None


def set_cost_sink(sink: Callable[[CostRecord], None] | None) -> None:
    global _cost_sink
    _cost_sink = sink


def record_cost(record: CostRecord) -> None:
    if _cost_sink is not None:
        _cost_sink(record)


class BaseProvider:
    """Shared behaviour for every role implementation.

    Subclasses implement ``_call``; retries, timeouts and cost accounting are
    handled here so that no individual provider can forget them.
    """

    role: Role
    name: str = "base"
    model: str | None = None
    max_retries: int = 3
    timeout_s: float = 120.0

    def __init__(self, project_id: str | None = None, **_: Any) -> None:
        self.project_id = project_id
        # A call can report its own token usage here; _guarded picks it up and
        # clears it. Keeping accounting in the base class means no individual
        # provider can forget to do it.
        self._pending_usage: dict[str, Any] | None = None
        # Retries that eventually succeed are invisible otherwise, and a call
        # that quietly retries every time costs double for no reason. Keeping
        # the swallowed errors is what makes that diagnosable.
        self.attempt_errors: list[str] = []

    def report_provenance(
        self,
        raw_response: str | None = None,
        repairs: int = 0,
        **extra: Any,
    ) -> None:
        sink = _PROVENANCE.get()
        if sink is None:
            # Nobody is collecting; the call still works, the evidence is just
            # not being kept.
            return
        sink.update({
            "provider": self.name,
            "model": self.model,
            "prompt_version": getattr(self, "prompt_version", None),
            "raw_response": raw_response,
            "repairs": repairs,
            **extra,
        })

    def report_usage(
        self,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_estimate: float | None = None,
    ) -> None:
        self._pending_usage = {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_estimate": cost_estimate,
        }

    async def _guarded(
        self,
        fn: Callable[[], Awaitable[T]],
        *,
        seconds_processed: float | None = None,
        project_id: str | None = None,
    ) -> T:
        started = time.perf_counter()
        attempt = 0
        last_error: Exception | None = None
        self.attempt_errors = []
        while attempt < self.max_retries:
            try:
                result = await asyncio.wait_for(fn(), timeout=self.timeout_s)
                usage = self._pending_usage or {}
                self._pending_usage = None
                record_cost(
                    CostRecord(
                        project_id=project_id or self.project_id,
                        role=self.role.value,
                        provider=self.name,
                        model=self.model,
                        seconds_processed=seconds_processed,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        retries=attempt,
                        **usage,
                    )
                )
                return result
            except NonRetryable as exc:
                record_cost(
                    CostRecord(
                        project_id=project_id or self.project_id,
                        role=self.role.value,
                        provider=self.name,
                        model=self.model,
                        latency_ms=int((time.perf_counter() - started) * 1000),
                        retries=attempt,
                    )
                )
                raise
            except Exception as exc:  # noqa: BLE001 - deliberate: retry anything
                last_error = exc
                self.attempt_errors.append(f"{type(exc).__name__}: {exc}"[:400])
                attempt += 1
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(self._backoff_for(exc, attempt))
        record_cost(
            CostRecord(
                project_id=project_id or self.project_id,
                role=self.role.value,
                provider=self.name,
                model=self.model,
                latency_ms=int((time.perf_counter() - started) * 1000),
                retries=attempt,
            )
        )
        raise RuntimeError(
            f"{self.name} ({self.role.value}) failed after {attempt} attempts"
        ) from last_error

    @staticmethod
    def _backoff_for(exc: Exception, attempt: int) -> float:
        """Wait longer for server-side capacity errors than for local ones.

        A 5xx means the provider is briefly out of capacity - retrying half a
        second later mostly just spends another attempt on the same outage.
        """
        status = getattr(getattr(exc, "response", None), "status_code", None)
        base = 2.0 if status and 500 <= status < 600 else 0.5
        # Capped: unbounded exponential growth makes a flaky endpoint look
        # like a hung process, and nothing is gained by waiting a minute
        # between attempts at a free tier that is simply busy.
        return min(15.0, base * (2 ** (attempt - 1)))
