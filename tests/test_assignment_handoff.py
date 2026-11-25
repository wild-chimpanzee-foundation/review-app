"""M1: frozen handouts, bounded archives, and portable identity persistence."""

import io
import json
import uuid
import zipfile
from contextlib import nullcontext
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from review_app.backend.annotation_package import archive
from review_app.backend.annotation_package.identity import IdentityMap
from review_app.backend.db.migrations import MIGRATIONS, run_migrations
from review_app.backend.db.models import (
    Base,
    Deployment,
    DeploymentFolderLink,
    HandoffAssignment,
    HandoffIdentityMap,
    IndividualObservation,
    ModelAnnotation,
    ProjectSpecies,
    Species,
    Video,
    VideoLabel,
    VideoTag,
)
from review_app.backend.provider.assignment_handoff import AssignmentRequest
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


@pytest.fixture
def prepared(provider_with_project):
    dp, project, root = provider_with_project
    ids = dp.get_video_queue({}, active_project_id=project.id)
    dp.update_manual_review(
        ids[0],
        [
            {
                "species": "deer",
                "count": 3,
                "start_sec": 1.25,
                "end_sec": 5.5,
                "tags": ["grazing"],
                "labeled_by": "alice",
            }
        ],
        is_blank=False,
        labeled_by="alice",
        active_project_id=project.id,
    )
    dp.set_review_later(ids[0], True)
    dp.set_video_tags(ids[0], ["reference_video"])
    with dp.Session.begin() as session:
        session.query(VideoTag).filter_by(video_id=ids[0]).one().tagged_by = "bob"
        deer = session.query(Species).filter_by(scientific_name="deer").one()
        session.add(ProjectSpecies(project_id=project.id, species_id=deer.id))
        session.add(
            ModelAnnotation(
                video_id=ids[0],
                annotation_type="species",
                model_name="model-a",
                value_text="deer",
                probability=0.8,
                t_start_sec=0.25,
                t_end_sec=7.75,
            )
        )
    result = dp.prepare_assignment(project.id, AssignmentRequest(" alice ", tuple(ids)))
    return dp, project, root, ids, result


def test_assignment_preserves_deployment_metadata_and_manual_links(
    provider_with_project, tmp_path
):
    dp, project, root = provider_with_project
    first, second = dp.get_assignment_video_ids(project.id)
    with dp.Session.begin() as session:
        spring = Deployment(
            project_id=project.id,
            deployment_id="spring",
            camera_id="cam_x",
            installation_date=date(2025, 3, 12),
            habitat="forest",
        )
        fall = Deployment(project_id=project.id, deployment_id="fall", camera_id="cam_x")
        session.add_all([spring, fall])
        session.flush()
        session.get(Video, first).deployment_id = spring.id
        session.get(Video, second).deployment_id = fall.id
        session.add(
            DeploymentFolderLink(
                project_dir_id=root.id,
                folder_path="cam_x",
                deployment_id=spring.id,
            )
        )
        origin_ids = {spring.id, fall.id}

    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", (first, second)))
    package = dp.export_assignment(prepared.id)
    manifest, context, work = archive.decode_archive(package)
    assert manifest["version"] == 2
    assert manifest["recipient"] == "alice"
    context["deployments"]["media"][0]["deployment_id"] = "unknown"
    with pytest.raises(ValueError, match="Deployment records must match footage links"):
        archive.decode_archive(repack(manifest, context, work))

    opened = dp.open_assignment(package, "Remote project", {root.id: tmp_path})
    with dp.Session() as session:
        records = {
            row.id: row
            for row in session.query(Deployment).filter_by(project_id=opened.project_id)
        }
        assert not origin_ids.intersection(records)
        assert {row.deployment_id for row in records.values()} == {"spring", "fall"}
        spring = next(row for row in records.values() if row.deployment_id == "spring")
        assert spring.installation_date == date(2025, 3, 12)
        assert spring.habitat == "forest"
        scoped = session.query(Video).filter_by(project_id=opened.project_id).all()
        assert {video.deployment_id for video in scoped} == records.keys()
        assert {video.deployment_assignment for video in scoped} == {"manual"}
        assert (
            session.query(DeploymentFolderLink)
            .filter_by(deployment_id=spring.id)
            .one()
            .folder_path
            == "cam_x"
        )

    returned = dp.export_assignment_return(opened.project_id)
    return_manifest, return_context, _ = archive.decode_archive(returned)
    assert return_manifest["direction"] == "return"
    assert "deployments" not in return_context


def test_assignment_preserves_unavailable_ordinary_footage_review(provider_with_project):
    dp, project, root = provider_with_project
    folders = ("T98/98_Cam150_voler_VR", "T136/136_Cam296_VR")
    for folder in folders:
        reference = Path(root.path) / folder / "reference.mp4"
        reference.parent.mkdir(parents=True)
        reference.touch()
    dp.sync_videos(active_project_id=project.id)
    for folder in folders:
        dp.set_folder_ordinary_unavailable(project.id, root.id, folder, "Camera stolen")
    assert not dp.get_deployment_summary(project.id)["has_pending_reference_reviews"]
    reference_ids = [
        video["video_id"]
        for video in dp.get_deployment_management(project.id)["videos"]
        if video["folder"] == folders[0]
    ]
    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(reference_ids)))
    package = dp.export_assignment(prepared.id)
    manifest, context, work = archive.decode_archive(package)
    (review,) = context["deployments"]["folder_reviews"]
    assert review["folder_path"] == folders[0]
    review["folder_path"] = folders[1]
    with pytest.raises(ValueError, match="Invalid deployment folder review"):
        archive.decode_archive(repack(manifest, context, work))

    opened = dp.open_assignment(package, "Remote project", {root.id: root.path})
    summary = dp.get_deployment_summary(opened.project_id)
    assert summary["unmatched_references"] == 1
    assert not summary["has_pending_reference_reviews"]
    local_root = dp.get_project_dirs(opened.project_id)[0]
    assert local_root.id != root.id
    folder, _, _ = dp.get_deployment_folder(opened.project_id, local_root.id, folders[0])
    assert folder["ordinary_unavailable"]
    assert folder["review_note"] == "Camera stolen"
    assert {
        row["folder"] for row in dp.get_deployment_management(opened.project_id)["folders"]
    } == {folders[0]}
    returned = dp.export_assignment_return(opened.project_id)
    return_manifest, return_context, _ = archive.decode_archive(returned)
    assert return_manifest["version"] == 2
    assert "deployments" not in return_context


def unpack(prepared):
    dp, _, _, _, result = prepared
    return archive.decode_archive(dp.export_assignment(result.id))


def repack(manifest, context, work, extra=None):
    manifest = {key: value for key, value in manifest.items() if key != "assignment_number"}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, value in zip(archive.MEMBERS, (manifest, context, work), strict=True):
            zf.writestr(name, json.dumps(value))
        if extra:
            zf.writestr(*extra)
    return output.getvalue()


def test_preparation_preserves_complete_species_setup_including_untouched_work(prepared):
    dp, project, root, ids, result = prepared
    manifest, context, work = unpack(prepared)
    assert result.recipient == manifest["recipient"] == "alice"
    assert manifest["assignment_id"] == result.assignment_id
    assert manifest["source_project_id"] == project.id
    assert set(manifest["work_keys"]) == set(ids)
    assert manifest["roots"] == [root.id]
    assert result.video_count == 2
    assert result.missing_video_ids == ()
    units = {row["video_id"]: row for row in work}
    reviewed = units[ids[0]]
    assert reviewed["label"]["is_blank"] is False
    assert reviewed["label"]["review_later"] is True
    assert reviewed["label"]["labeled_by"] == "alice"
    observation = reviewed["observations"][0]
    assert observation["species"] == "deer"
    assert observation["behaviors"] == ["grazing"]
    assert (observation["count"], observation["start_sec"], observation["end_sec"]) == (
        3,
        1.25,
        5.5,
    )
    assert reviewed["tags"][0]["key"] == "reference_video"
    assert reviewed["tags"][0]["tagged_by"] == "bob"
    assert units[ids[1]] == {"video_id": ids[1], "label": None, "observations": [], "tags": []}
    assert context["project_species"] == ["deer"]
    assert {row["scientific_name"] for row in context["species"]} == {"deer", "fox"}
    assert context["predictions"][0]["probability"] == 0.8
    assert context["predictions"][0]["t_start_sec"] == 0.25
    with dp.Session() as session:
        source = session.query(IndividualObservation).filter_by(video_id=ids[0]).one()
        assert observation["observation_uuid"] == source.observation_uuid
        assert observation["labeled_at"] == source.labeled_at.isoformat()
        assert observation["updated_at"] == source.updated_at.isoformat()
        label = session.get(VideoLabel, ids[0])
        assert reviewed["label"]["labeled_at"] == label.labeled_at.isoformat()
        tag = session.query(VideoTag).filter_by(video_id=ids[0]).one()
        assert reviewed["tags"][0]["tagged_at"] == tag.tagged_at.isoformat()
        stored = session.get(HandoffAssignment, result.id)
        assert stored.role == "origin"
        assert stored.scope["work_keys"] == manifest["work_keys"]
        mapping = (
            session.query(HandoffIdentityMap)
            .filter_by(handoff_id=result.id, kind="observation")
            .one()
        )
        assert json.loads(mapping.local_id) == [ids[0], source.id]
        assert json.loads(mapping.portable_id) == [ids[0], source.observation_uuid]


def test_download_is_immutable_after_edits_distribution_and_restart(prepared):
    dp, project, root, ids, result = prepared
    original = dp.export_assignment(result.id)
    dp.update_manual_review(ids[0], [], is_blank=True)
    dp.add_annotator("bob")
    dp.apply_distribution(project.id, {"bob": ["cam_x"]})
    dp.update_project_name(project.id, "Changed name")
    with dp.Session.begin() as session:
        session.query(ModelAnnotation).filter_by(video_id=ids[0]).delete()
        session.query(Species).filter_by(scientific_name="deer").one().name_en = "Changed"
    assert dp.export_assignment(result.id) == original
    restarted = LocalDataProvider()
    try:
        assert restarted.export_assignment(result.id) == original
        assert str(root.path).encode() not in b"".join(
            json.dumps(value).encode() for value in archive.decode_archive(original)
        )
    finally:
        restarted.engine.dispose()


def test_preparation_uses_explicit_scope_not_distribution_or_missing_filters(
    provider_with_project,
):
    dp, project, _ = provider_with_project
    ids = dp.get_video_queue({}, active_project_id=project.id)
    dp.update_manual_review(ids[0], [], is_blank=True)
    with dp.Session.begin() as session:
        session.get(Video, ids[0]).is_missing = True
    result = dp.prepare_assignment(project.id, AssignmentRequest("alice", (ids[0],)))
    manifest, context, work = archive.decode_archive(dp.export_assignment(result.id))
    assert manifest["work_keys"] == [ids[0]]
    assert context["media"][0]["is_missing"] is True
    assert work[0]["label"]["is_blank"] is True
    assert result.missing_video_ids == (ids[0],)


def test_multi_root_paths_keep_separate_namespaces(provider_with_project, tmp_path):
    dp, project, _ = provider_with_project
    second = tmp_path / "second-root"
    (second / "cam_x").mkdir(parents=True)
    (second / "cam_x" / "a.mp4").touch()
    new_root = dp.add_project_dir(project.id, str(second))
    dp.sync_videos(video_dir=second, active_project_id=project.id)
    with dp.Session() as session:
        ids = [row.video_id for row in session.query(Video).filter_by(project_id=project.id)]
    result = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    _, context, _ = archive.decode_archive(dp.export_assignment(result.id))
    same_names = [row for row in context["media"] if row["video_path"] == "cam_x/a.mp4"]
    assert len(same_names) == 2
    assert len({row["root_id"] for row in same_names}) == 2
    assert new_root.id in {row["root_id"] for row in same_names}


@pytest.mark.parametrize(
    "selection,recipient,stage,target",
    [
        ((), "alice", "species", None),
        (("unknown",), "alice", "species", None),
        (("first", "first"), "alice", "species", None),
        (("first",), " ", "species", None),
        (("first",), "alice", "distance", "deer"),
        (("first",), "alice", "species", "deer"),
    ],
)
def test_invalid_preparation_leaves_no_handoff(
    provider_with_project, selection, recipient, stage, target
):
    dp, project, _ = provider_with_project
    first = dp.get_video_queue({}, active_project_id=project.id)[0]
    selection = tuple(first if value == "first" else value for value in selection)
    with pytest.raises(ValueError):
        dp.prepare_assignment(project.id, AssignmentRequest(recipient, selection, stage, target))
    with dp.Session() as session:
        assert session.query(HandoffAssignment).count() == 0
        assert session.query(HandoffIdentityMap).count() == 0


def test_cross_project_or_unregistered_media_is_not_packaged(provider_with_project, tmp_path):
    dp, project, _ = provider_with_project
    video_id = dp.get_video_queue({}, active_project_id=project.id)[0]
    other = dp.create_project("Other", "")
    with pytest.raises(ValueError, match="belong"):
        dp.prepare_assignment(other.id, AssignmentRequest("alice", (video_id,)))
    with dp.Session.begin() as session:
        session.get(Video, video_id).video_path = str(tmp_path / "elsewhere.mp4")
    with pytest.raises(ValueError, match="registered"):
        dp.prepare_assignment(project.id, AssignmentRequest("alice", (video_id,)))
    with dp.Session() as session:
        assert session.query(HandoffAssignment).count() == 0


def test_late_preparation_failure_rolls_back_handout_and_identity_maps(
    provider_with_project, monkeypatch
):
    dp, project, _ = provider_with_project
    ids = dp.get_video_queue({}, active_project_id=project.id)
    original = IdentityMap.bind
    calls = 0

    def fail_second(self, *args):
        nonlocal calls
        calls += 1
        value = original(self, *args)
        if calls == 2:
            raise ValueError("simulated mapping failure")
        return value

    monkeypatch.setattr(IdentityMap, "bind", fail_second)
    with pytest.raises(ValueError, match="simulated"):
        dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    with dp.Session() as session:
        assert session.query(HandoffAssignment).count() == 0
        assert session.query(HandoffIdentityMap).count() == 0
        assert session.query(Video).filter_by(project_id=project.id).count() == 2


def test_unknown_assignment_download(provider_with_project):
    with pytest.raises(ValueError, match="not found"):
        provider_with_project[0].export_assignment("missing")


@pytest.mark.parametrize(
    "change,match",
    [
        (lambda m, c, w: m.update(version=True), "version"),
        (lambda m, c, w: m.update(version=99), "version"),
        (lambda m, c, w: m.update(work_keys=[]), "scope"),
        (lambda m, c, w: c["media"][0].update(video_path="../escape.mp4"), "relative"),
        (lambda m, c, w: c["media"][0].update(video_path="C:/absolute.mp4"), "relative"),
        (lambda m, c, w: c["media"][0].update(root_id="unknown"), "roots"),
        (lambda m, c, w: c["media"][0].update(duration_sec=float("nan")), "finite"),
        (lambda m, c, w: c["predictions"][0].update(video_id="unassigned"), "scope"),
        (
            lambda m, c, w: w[0].update(
                tags=[{"key": "missing", "tagged_by": None, "tagged_at": "2025-01-01"}]
            ),
            "tag",
        ),
        (lambda m, c, w: w.append(w[0]), "Duplicate"),
        (lambda m, c, w: w.pop(), "scope"),
        (lambda m, c, w: c.update(unversioned_field="extra"), "fields"),
    ],
)
def test_archive_rejects_invalid_records_and_graphs(prepared, change, match):
    manifest, context, work = unpack(prepared)
    change(manifest, context, work)
    with pytest.raises(ValueError, match=match):
        archive.decode_archive(repack(manifest, context, work))


def test_archive_rejects_bad_zip_extra_duplicate_members_and_duplicate_json(prepared):
    with pytest.raises(ValueError, match="Invalid assignment archive"):
        archive.decode_archive(b"not a zip")
    manifest, context, work = unpack(prepared)
    for extra in (("../media.mp4", b"video"), ("work.json", b"[]")):
        with pytest.warns(UserWarning) if extra[0] == "work.json" else nullcontext():
            data = repack(manifest, context, work, extra=extra)
        with pytest.raises(ValueError, match="exactly"):
            archive.decode_archive(data)
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zf:
        zf.writestr("manifest.json", '{"version":1,"version":1}')
        zf.writestr("context.json", "{}")
        zf.writestr("work.json", "[]")
    with pytest.raises(ValueError, match="Duplicate JSON"):
        archive.decode_archive(output.getvalue())


def test_archive_limits_apply_on_read_and_write(prepared, monkeypatch):
    dp, _, _, _, result = prepared
    data = dp.export_assignment(result.id)
    manifest, context, work = archive.decode_archive(data)
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", len(data) - 1)
    with pytest.raises(ValueError, match="archive size"):
        archive.decode_archive(data)
    with pytest.raises(ValueError, match="archive size"):
        archive.encode_archive(manifest, context, work)
    monkeypatch.setattr(archive, "MAX_ARCHIVE_BYTES", len(data) * 2)
    monkeypatch.setattr(archive, "MAX_UNCOMPRESSED_BYTES", 10)
    with pytest.raises(ValueError, match="uncompressed"):
        archive.decode_archive(data)
    with pytest.raises(ValueError, match="uncompressed"):
        archive.encode_archive(manifest, context, work)


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
    with pytest.raises(ValueError, match="working assignment"):
        dp.prepare_assignment(project.id, AssignmentRequest("bob", (ids[0],)))
    with pytest.raises(ValueError, match="originating"):
        dp.export_assignment(handoff_id)
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


def test_v22_upgrade_preserves_existing_species_work(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    snapshot = Path(__file__).parent / "fixtures" / "schema_snapshot.sql"
    with engine.connect() as conn:
        conn.connection.driver_connection.executescript(snapshot.read_text())
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
