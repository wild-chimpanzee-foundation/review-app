"""Assignment persistence, portable identities, and fresh/upgrade schema invariants."""

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from review_app.backend.annotation_package.identity import IdentityMap
from review_app.backend.db.migrations import MIGRATIONS, run_migrations
from review_app.backend.db.models import Base, HandoffAssignment, HandoffIdentityMap
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def working(session, project_id, assignment_id="portable-assignment"):
    row = HandoffAssignment(
        project_id=project_id,
        assignment_id=assignment_id,
        role="working",
        source_project_id="external-project",
        recipient="alice",
        stage="species",
        scope={},
        setup_archive=b"fixture",
    )
    session.add(row)
    session.flush()
    return row


def test_identity_maps_allocate_stable_local_ids_for_same_portable_graph_twice(
    provider_with_project,
):
    dp, project, _ = provider_with_project
    other = dp.create_project("Other", "")
    with dp.Session.begin() as session:
        a = working(session, project.id)
        b = working(session, other.id)
        first, second = IdentityMap(session, a.id), IdentityMap(session, b.id)
        ids = [first.local("video", "source-video"), second.local("video", "source-video")]
        assert ids[0] != ids[1] and "source-video" not in ids
        assert first.local("video", "source-video") == ids[0]
        assert first.portable("video", ids[0]) == "source-video"
        new = first.portable("sample", "new-local-work")
        assert first.portable("sample", "new-local-work") == new
        assert first.local("sample", new) == "new-local-work"
        handoff_id = a.id
    with dp.Session() as session:
        mapping = IdentityMap(session, handoff_id)
        assert mapping.local("video", "source-video") == ids[0]
        with pytest.raises(ValueError, match="another entity"):
            mapping.bind("video", "source-video", "other-local")
        with pytest.raises(ValueError, match="another entity"):
            mapping.bind("video", "other-source", ids[0])
    dp.delete_project(project.id)
    with dp.Session() as session:
        assert session.get(HandoffAssignment, handoff_id) is None
        assert session.query(HandoffIdentityMap).filter_by(handoff_id=handoff_id).count() == 0


@pytest.fixture(params=["fresh", "upgraded"])
def handoff_engine(request, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.db'}")

    @event.listens_for(engine, "connect")
    def foreign_keys(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")

    if request.param == "fresh":
        Base.metadata.create_all(engine)
    else:
        snapshot = Path(__file__).parent / "fixtures" / "schema_snapshot.sql"
        with engine.connect() as conn:
            conn.connection.driver_connection.executescript(snapshot.read_text())
    run_migrations(engine)
    with engine.begin() as conn:
        conn.execute(
            text("INSERT INTO projects (id, name, created_at) VALUES ('p', 'Project', :now)"),
            {"now": datetime.now(timezone.utc)},
        )
        assert conn.exec_driver_sql("SELECT version FROM _schema_version").scalar() == len(
            MIGRATIONS
        )
    yield engine
    engine.dispose()


def test_schema_enforces_binding_and_identity_invariants(handoff_engine):
    with Session(handoff_engine) as session, session.begin():
        row = working(session, "p")
        for overrides in (
            {"assignment_id": "another-assignment"},  # second working binding
            {"project_id": "missing-project"},
            {"role": "unknown"},
            {"recipient": " "},
            {"stage": "distance", "target_species": None},
            {"stage": "species", "target_species": "deer"},
            {"role": "origin", "source_project_id": "external-project"},
        ):
            values = {
                "id": str(uuid.uuid4()),
                "project_id": "p",
                "assignment_id": "second",
                "role": "working",
                "source_project_id": "external-project",
                "recipient": "alice",
                "stage": "species",
                "scope": {},
                "setup_archive": b"x",
                **overrides,
            }
            with pytest.raises(IntegrityError), session.begin_nested():
                session.add(HandoffAssignment(**values))
                session.flush()
        session.add(
            HandoffIdentityMap(handoff_id=row.id, kind="video", portable_id="a", local_id="b")
        )
        session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                HandoffIdentityMap(handoff_id=row.id, kind="video", portable_id="c", local_id="b")
            )
            session.flush()
        with pytest.raises(IntegrityError), session.begin_nested():
            session.add(
                HandoffIdentityMap(handoff_id=row.id, kind="video", portable_id="", local_id="c")
            )
            session.flush()
    # Check constraint parity is not covered by the generic structural parity test.
    expected = inspect(handoff_engine).get_check_constraints("handoff_assignments")
    assert {row["name"] for row in expected} == {
        "ck_handoff_role",
        "ck_handoff_recipient",
        "ck_handoff_stage",
        "ck_handoff_origin",
    }


def test_v25_upgrade_preserves_existing_species_work(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    snapshot = Path(__file__).parent / "fixtures" / "schema_snapshot.sql"
    with engine.connect() as conn:
        conn.connection.driver_connection.executescript(snapshot.read_text())
        # Build a genuine v24 schema from frozen migration definitions, not current ORM DDL.
        for version, migration in MIGRATIONS:
            if version in (23, 24):
                migration(conn)
        conn.execute(text("UPDATE _schema_version SET version=24"))
        conn.execute(
            text("INSERT INTO projects (id,name,created_at) VALUES ('p','Keep','2025-01-01')")
        )
        conn.execute(
            text(
                "INSERT INTO videos (video_id,project_id,video_path,last_seen_at) "
                "VALUES ('v','p','animal.mp4','2025-01-01')"
            )
        )
        conn.execute(
            text("INSERT INTO video_labels (video_id,is_blank,labeled_by) VALUES ('v',1,'alice')")
        )
        conn.commit()
    run_migrations(engine)
    run_migrations(engine)
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT is_blank,labeled_by FROM video_labels").one() == (
            1,
            "alice",
        )
        assert conn.exec_driver_sql("SELECT COUNT(*) FROM handoff_assignments").scalar() == 0
    engine.dispose()
