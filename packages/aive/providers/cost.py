"""Persist CostRecords into the api_calls table."""

from __future__ import annotations

from ..db.models import ApiCall
from ..db.session import session_scope
from .base import CostRecord, set_cost_sink


def db_cost_sink(record: CostRecord) -> None:
    with session_scope() as session:
        session.add(ApiCall(**record.as_dict()))


def install_db_cost_sink() -> None:
    set_cost_sink(db_cost_sink)
