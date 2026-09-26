"""Schema smoke tests, plus the two storage rules that are easy to get wrong."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from aive.db.models import (
    Base,
    EditOperationRow,
    PreferencePair,
    PreferenceVote,
    Project,
    ProjectVersionRow,
)


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path/'t.db'}", future=True)
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_all_tables_created(session) -> None:
    assert len(Base.metadata.tables) == 26


def test_votes_are_stored_per_annotator(session) -> None:
    """Disagreement must survive in the data, not be collapsed into a winner."""
    session.add(PreferencePair(id="pair_1", timeline_a="tl_a", timeline_b="tl_b",
                               render_a="r_a", render_b="r_b", source="human"))
    votes = [("u1", "expert", "A"), ("u2", "expert", "A"),
             ("u3", "consumer", "B"), ("u4", "consumer", "B"),
             ("u5", "consumer", "B")]
    for i, (uid, kind, choice) in enumerate(votes):
        session.add(PreferenceVote(id=f"v{i}", pair_id="pair_1", annotator_id=uid,
                                   annotator_type=kind, choice=choice))
    session.commit()

    rows = session.scalars(
        select(PreferenceVote).where(PreferenceVote.pair_id == "pair_1")
    ).all()
    assert len(rows) == 5
    # The 2:3 split is recoverable, and so is the fact that it splits along
    # expert/consumer lines - which is the taste-cluster signal.
    experts = [r.choice for r in rows if r.annotator_type == "expert"]
    consumers = [r.choice for r in rows if r.annotator_type == "consumer"]
    assert experts == ["A", "A"] and consumers == ["B", "B", "B"]


def test_operation_origin_is_recorded(session) -> None:
    """chat vs editor is the Manual-vs-Agent ratio; it has to be honest."""
    session.add(Project(id="p1"))
    session.add(ProjectVersionRow(id="v1", project_id="p1"))
    session.add_all([
        EditOperationRow(id="o1", version_id="v1", operation="trim_clip",
                         origin="chat", applied=True),
        EditOperationRow(id="o2", version_id="v1", operation="trim_clip",
                         origin="editor", applied=True),
    ])
    session.commit()
    origins = session.scalars(select(EditOperationRow.origin)).all()
    assert set(origins) == {"chat", "editor"}
