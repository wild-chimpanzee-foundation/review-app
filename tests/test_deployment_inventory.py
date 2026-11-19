"""Backend coverage for the first inventory slice; no UI tests by operator request."""

import csv
import io
from datetime import date

import pytest
from review_app.backend.db.migrations import MIGRATIONS, run_migrations
from review_app.backend.db.models import Base, Deployment, Project, ProjectDir, Video
from review_app.backend.deployments import (
    CSV_COLUMNS,
    CSV_TEMPLATE,
    MAX_CSV_BYTES,
    DeploymentInputError,
    DeploymentPreview,
    infer_deployment_inventory,
    parse_deployment_csv,
    preview_deployment_setup,
)
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_id_only_and_unknown_columns(tmp_path):
    preview = parse_deployment_csv(
        b"deployment_id,custom\nP5-0042,keep outside the app\n", str(tmp_path)
    )
    assert preview.rows[0]["deployment_id"] == "P5-0042"
    assert preview.rows[0]["name"] is None
    assert preview.rows[0]["source"] == "csv"
    assert "custom" not in preview.rows[0]
    assert preview.ignored_columns == ("custom",)


def test_headers_are_trimmed_and_lowercased_without_changing_values(tmp_path):
    preview = parse_deployment_csv(
        b" Deployment_ID ,Installation_date,NAME,Custom\n48_Cam086,2026-02-21,Display Name,Ignored\n",
        str(tmp_path),
    )
    assert preview.rows[0]["deployment_id"] == "48_Cam086"
    assert preview.rows[0]["installation_date"] == date(2026, 2, 21)
    assert preview.rows[0]["name"] == "Display Name"
    assert preview.ignored_columns == ("custom",)


def test_location_is_not_part_of_initial_slice(tmp_path):
    preview = parse_deployment_csv(
        b"deployment_id,x,y,crs\na,bad,also bad,nonsense\n", str(tmp_path)
    )
    assert preview.ignored_columns == ("x", "y", "crs")
    assert not {"x", "y", "latitude", "longitude", "source_crs"} & preview.rows[0].keys()
    assert not {"latitude", "longitude", "source_crs"} & set(Deployment.__table__.columns.keys())


def test_template_is_valid_and_csv_headers_match(tmp_path):
    preview = parse_deployment_csv(CSV_TEMPLATE.encode(), str(tmp_path))
    assert len(preview.rows) == 1
    assert tuple(next(csv.reader(io.StringIO(CSV_TEMPLATE)))) == CSV_COLUMNS


def test_utf8_bom_semicolon_and_quoted_multiline_values(tmp_path):
    preview = parse_deployment_csv(
        '\ufeffdeployment_id;name;notes\r\nP5-0042;Forêt;"line one\nline two"\r\n'.encode(),
        str(tmp_path),
    )
    assert preview.rows[0]["name"] == "Forêt"
    assert preview.rows[0]["notes"] == "line one\nline two"


@pytest.mark.parametrize(
    ("raw", "key"),
    [
        (b"", "headers"),
        (b"camera_id\ncam453\n", "headers"),
        (b"deployment_id,deployment_id\na,b\n", "headers"),
        (b"deployment_id, Deployment_ID \na,b\n", "headers"),
        (b"deployment_id,name,NAME\na,b,c\n", "headers"),
        (b"deployment_id\n", "empty"),
        (b"deployment_id,name\n,missing\n", "id"),
        (b"deployment_id\na\n a \n", "duplicate"),
        (b"deployment_id\na,extra\n", "row"),
        (b'deployment_id,notes\na,"unterminated\n', "csv"),
        (b"deployment_id,name\na,\xff\n", "encoding"),
        (b"deployment_id,installation_date\na,20260217\n", "date"),
        (b"deployment_id,installation_date\na,2026-02-30\n", "date"),
        (
            b"deployment_id,installation_date,retrieval_date\na,2026-05-01,2026-02-01\n",
            "date_order",
        ),
    ],
)
def test_invalid_csv_is_rejected(tmp_path, raw, key):
    with pytest.raises(DeploymentInputError) as error:
        parse_deployment_csv(raw, str(tmp_path))
    assert error.value.key == "deployment_error_" + key


def test_limits(tmp_path, monkeypatch):
    with pytest.raises(DeploymentInputError, match="deployment_error_size"):
        parse_deployment_csv(b"a" * (MAX_CSV_BYTES + 1), str(tmp_path))
    monkeypatch.setattr("review_app.backend.deployments.MAX_DEPLOYMENTS", 1)
    with pytest.raises(DeploymentInputError, match="deployment_error_count"):
        parse_deployment_csv(b"deployment_id\na\nb\n", str(tmp_path))


def test_optional_dates_are_date_only_and_independently_optional(tmp_path):
    preview = parse_deployment_csv(
        b"deployment_id,installation_date,retrieval_date\na,2026-02-17,\nb,,2026-05-01\n",
        str(tmp_path),
    )
    assert preview.rows[0]["installation_date"] == date(2026, 2, 17)
    assert preview.rows[0]["retrieval_date"] is None
    assert preview.rows[1]["installation_date"] is None


def test_paths_are_canonical_relative_and_missing_paths_warn(tmp_path):
    existing = tmp_path / "camera"
    existing.mkdir()
    preview = parse_deployment_csv(
        f"deployment_id,folder_path\na,{existing}\nb,later/reference\n".encode(), str(tmp_path)
    )
    assert preview.rows[0]["folder_path"] == "camera"
    assert preview.rows[1]["folder_path"] == "later/reference"
    assert preview.missing_folders == ("later/reference",)
    root = parse_deployment_csv(b"deployment_id,folder_path\na,.\n", str(tmp_path))
    assert root.rows[0]["folder_path"] == "."


def test_escaping_paths_files_and_duplicate_directories_rejected(tmp_path):
    for path in ("../elsewhere", str(tmp_path.parent / "outside")):
        with pytest.raises(DeploymentInputError, match="deployment_error_path"):
            parse_deployment_csv(f"deployment_id,folder_path\na,{path}\n".encode(), str(tmp_path))
    (tmp_path / "clip.mp4").touch()
    with pytest.raises(DeploymentInputError, match="deployment_error_path"):
        parse_deployment_csv(b"deployment_id,folder_path\na,clip.mp4\n", str(tmp_path))
    with pytest.raises(DeploymentInputError, match="deployment_error_folder_duplicate"):
        parse_deployment_csv(b"deployment_id,folder_path\na,camera\nb,./camera\n", str(tmp_path))


def test_symlink_outside_root_is_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(DeploymentInputError, match="deployment_error_path"):
        parse_deployment_csv(b"deployment_id,folder_path\na,link\n", str(root))


def test_inference_preserves_paired_layout_and_skips_hidden_nonvideo_files(tmp_path):
    for relative in (
        "cam-a/videos/animal.mp4",
        "cam-a/references/ref.mp4",
        "cam-b/clip.MP4",
        ".hidden/clip.mp4",
        "cam-c/readme.txt",
    ):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    preview = infer_deployment_inventory(str(tmp_path))
    assert [row["deployment_id"] for row in preview.rows] == ["cam-a", "cam-b"]
    assert (
        preview.rows[0]["folder_path"] is None
    )  # More than one media directory; don't invent a link.
    assert preview.rows[1]["folder_path"] == "cam-b"
    assert all(row["source"] == "inferred" for row in preview.rows)


def test_csv_creation_persists_inventory_and_isolates_projects(tmp_db):
    dp = LocalDataProvider()
    root = str(tmp_db["video_dir"])
    project = dp.create_project_with_deployments(
        "CSV",
        root,
        csv_data=(
            b"deployment_id,name,camera_id,installation_date,folder_path\nP5-42,A,cam453,2026-02-17,future\n"
        ),
    )
    rows = dp.get_project_deployments(project.id)
    assert len(rows) == 1
    assert rows[0]["deployment_id"] == "P5-42"
    assert rows[0]["installation_date"] == "2026-02-17"
    assert rows[0]["folder_path"] == "future"
    assert rows[0]["source"] == "csv"
    other = dp.create_project_with_deployments("Other", root, csv_data=b"deployment_id\nP5-42\n")
    assert len(dp.get_project_deployments(other.id)) == 1
    legacy = dp.create_project("Legacy", root)
    assert dp.get_project_deployments(legacy.id) == []
    assert len(dp.get_project_deployments(project.id)) == 1
    dp.delete_project(project.id)
    assert dp.get_project_deployments(project.id) == []
    assert len(dp.get_project_deployments(other.id)) == 1


def test_invalid_input_never_creates_partial_project_or_inventory(tmp_db):
    dp = LocalDataProvider()
    for kwargs in (
        {"csv_data": b"deployment_id\na\na\n"},
        {},
        {"csv_data": b"deployment_id\na\n", "infer_from_folders": True},
    ):
        with pytest.raises(DeploymentInputError):
            dp.create_project_with_deployments("Invalid", str(tmp_db["video_dir"]), **kwargs)
    with dp.Session() as session:
        assert session.query(Project).count() == 0
        assert session.query(ProjectDir).count() == 0
        assert session.query(Deployment).count() == 0


def test_project_and_inventory_rollback_together_on_database_failure(tmp_db, monkeypatch):
    dp = LocalDataProvider()
    # Exercise the write transaction, not just preflight validation.
    monkeypatch.setattr(
        "review_app.backend.deployments.parse_deployment_csv",
        lambda *_: DeploymentPreview(({"deployment_id": "same", "source": "csv"},) * 2),
    )
    with pytest.raises(IntegrityError):
        dp.create_project_with_deployments("Invalid", str(tmp_db["video_dir"]), csv_data=b"unused")
    with dp.Session() as session:
        assert session.query(Project).count() == 0
        assert session.query(ProjectDir).count() == 0
        assert session.query(Deployment).count() == 0


def test_inferred_creation_and_rescan_do_not_change_inventory_or_footage_scope(tmp_db, mock_probe):
    dp = LocalDataProvider()
    root = tmp_db["video_dir"]
    for relative in ("cam-a/videos/animal.mp4", "cam-a/references/ref.mp4"):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    project = dp.create_project_with_deployments("Inferred", str(root), infer_from_folders=True)
    before = dp.get_project_deployments(project.id)
    assert len(before) == 1 and before[0]["deployment_id"] == "cam-a"
    dp.sync_videos(active_project_id=project.id)
    dp.sync_videos(active_project_id=project.id)
    assert dp.get_project_deployments(project.id) == before
    with dp.Session() as session:
        assert {row.camera_id for row in session.query(Video)} == {"cam-a"}
    empty = root / "empty"
    empty.mkdir()
    with pytest.raises(DeploymentInputError, match="deployment_error_empty"):
        dp.create_project_with_deployments("Empty", str(empty), infer_from_folders=True)


def test_setup_previews_actual_folder_matches_and_rejects_stale_footage(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    for relative in (
        "camera_one/videos/animal.mp4",
        "camera_one/references/ref.mp4",
        "camera_two_extra/clip.mp4",
        "explicit/clip.mp4",
        "unknown/clip.mp4",
        "direct.mp4",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    raw = b"deployment_id,folder_path\ncamera_one,\ncamera_two,\ncamera_two_extra,\nmanual,explicit\ndefault,\n"
    preview = preview_deployment_setup(str(root), csv_data=raw)
    folders = {row.folder: row for row in preview.folders}
    assert folders["camera_one/videos"].candidates == ("camera_one",)
    assert folders["camera_one/references"].references == 1
    assert folders["camera_two_extra"].candidates == ("camera_two", "camera_two_extra")
    assert folders["explicit"].candidates == ("manual",)
    assert folders["unknown"].candidates == folders["."].candidates == ()
    assert preview.empty_deployments == ("camera_two", "camera_two_extra", "default")

    (root / "unknown/clip.mp4").rename(root / "unknown/replacement.mp4")
    dp = LocalDataProvider()
    with pytest.raises(DeploymentInputError, match="deployment_error_setup_stale"):
        dp.create_project_with_deployments(
            "Project", str(root), csv_data=raw, expected_preview=preview
        )
    assert dp.list_projects() == []

    fresh = preview_deployment_setup(str(root), csv_data=raw)
    project = dp.create_project_with_deployments(
        "Project", str(root), csv_data=raw, expected_preview=fresh
    )
    dp.sync_videos(active_project_id=project.id)
    for video in dp.get_deployment_management(project.id)["videos"]:
        candidates = folders[video["folder"]].candidates
        assert video["deployment"] == (candidates[0] if len(candidates) == 1 else None)

    inferred = preview_deployment_setup(str(root), infer_from_folders=True)
    assert {row.candidates for row in inferred.folders} == {
        ("camera_one",),
        ("camera_two_extra",),
        ("default",),
        ("explicit",),
        ("unknown",),
    }
    assert not inferred.empty_deployments


def test_v27_upgrade_adds_only_empty_inventory_and_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE deployments"))
        connection.execute(text("CREATE TABLE _schema_version (version INTEGER)"))
        connection.execute(text("INSERT INTO _schema_version VALUES (27)"))
        connection.execute(
            text("INSERT INTO projects (id,name,created_at) VALUES ('p','Existing','2026-01-01')")
        )
        connection.execute(
            text(
                "INSERT INTO videos (video_id,project_id,video_path,camera_id,last_seen_at,is_missing) VALUES ('v','p','/old/clip.mp4','old-camera','2026-01-01',0)"
            )
        )
    run_migrations(engine)
    run_migrations(engine)
    with engine.connect() as connection:
        assert (
            connection.execute(text("SELECT camera_id FROM videos WHERE video_id='v'")).scalar()
            == "old-camera"
        )
        assert connection.execute(text("SELECT COUNT(*) FROM deployments")).scalar() == 0
        assert connection.execute(text("SELECT version FROM _schema_version")).scalar() == len(
            MIGRATIONS
        )
    assert {c["name"] for c in inspect(engine).get_columns("deployments")} == set(
        Deployment.__table__.columns.keys()
    )
    engine.dispose()
