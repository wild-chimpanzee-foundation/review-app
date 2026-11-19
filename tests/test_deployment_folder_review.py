"""Folder summaries, advisory matching, and confirmed correction flows; no UI tests."""

import pytest
from review_app.backend.db.models import Video
from review_app.backend.deployment_review import rank_folder_deployments, summarize_folder_preview
from review_app.backend.deployments import DeploymentInputError
from review_app.backend.provider.local_data_provider import LocalDataProvider


def inventory(*identifiers):
    return [
        {
            "id": identifier,
            "deployment_id": identifier,
            "name": None,
            "camera_id": None,
            "transect_id": None,
        }
        for identifier in identifiers
    ]


@pytest.mark.parametrize(
    ("path", "ids", "expected", "reason"),
    [
        (
            "E6/T7/07_Cam433_0809319_1140669_20260508",
            ("7_cam433", "7_cam536", "8_cam433"),
            "7_cam433",
            "deployment_reason_zero_padding",
        ),
        (
            "E3/T43/VR-Cam_453",
            ("44_cam453", "43_cam453", "43_cam455"),
            "43_cam453",
            "deployment_reason_short_reference",
        ),
        (
            "E3/T44/VR-Cam_559",
            ("44_cam489", "44_cam559"),
            "44_cam559",
            "deployment_reason_same_camera",
        ),
        (
            "E2/T85/85_cam388_0824768_1112855_20260212",
            ("76_cam388", "85_cam288"),
            "85_cam288",
            "deployment_reason_other_camera",
        ),
        (
            "E2/T76/76_cam384_0171260_1121700_20260210 VR",
            ("76_cam441", "76_cam388"),
            "76_cam388",
            "deployment_reason_edit_distance",
        ),
        (
            "Trip-042_extra/videos",
            ("Trip-041", "Trip-042"),
            "Trip-042",
            "deployment_reason_edit_distance",
        ),
    ],
)
def test_fuzzy_suggestions_rank_actual_folder_issues(path, ids, expected, reason):
    suggestions = rank_folder_deployments({"folder": path}, inventory(*ids))
    assert suggestions[0]["deployment_id"] == expected
    assert reason in suggestions[0]["reasons"]
    assert suggestions[0]["distance"] >= 0
    assert 0 <= suggestions[0]["similarity"] <= 100


def test_edit_distance_is_reported_and_ids_are_not_rewritten():
    candidates = inventory("7_cam433", "7_cam536")
    before = [dict(row) for row in candidates]
    result = rank_folder_deployments({"folder": "T7/07_Cam433_0809319_20260508"}, candidates)
    assert result[0]["distance"] == 1
    assert result[0]["query"] == "07_cam433"
    assert candidates == before


def test_shared_reference_folder_does_not_invent_a_match():
    assert (
        rank_folder_deployments(
            {"folder": "DS_M1Phase5_E5/VR"}, inventory("7_cam433", "43_cam453")
        )
        == []
    )


def test_same_camera_elsewhere_is_marked_as_a_different_transect():
    suggestions = rank_folder_deployments(
        {"folder": "T85/85_cam388_suffix"}, inventory("76_cam388", "85_cam288")
    )
    other = next(row for row in suggestions if row["id"] == "76_cam388")
    assert "deployment_reason_other_transect" in other["reasons"]
    assert "deployment_reason_same_camera" in other["reasons"]


def test_suggestion_order_is_stable_and_limit_is_respected():
    rows = inventory("192_cam001", "192_cam355", "188_cam508")
    folder = {"folder": "T192/192_cam519_coords"}
    assert rank_folder_deployments(folder, rows, limit=2) == rank_folder_deployments(
        folder, list(reversed(rows)), limit=2
    )
    assert len(rank_folder_deployments(folder, rows, limit=2)) == 2


@pytest.fixture
def folders(tmp_db, mock_probe):
    root = tmp_db["video_dir"]
    for name in (
        "T7/07_Cam433_suffix/a.mp4",
        "T7/07_Cam433_suffix/b.mp4",
        "T7/07_Cam433_suffix/child/c.mp4",
        "T7/07_Cam433_suffix_VR/ref.mp4",
    ):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    dp = LocalDataProvider()
    project = dp.create_project_with_deployments(
        "Folders", str(root), csv_data=b"deployment_id\n7_cam433\n7_cam536\n"
    )
    dp.sync_videos(active_project_id=project.id)
    return dp, project, root


def test_management_groups_direct_files_and_reference_folders(folders):
    dp, project, root = folders
    rows = {row["folder"]: row for row in dp.get_deployment_management(project.id)["folders"]}
    assert len(rows) == 3
    ordinary = rows["T7/07_Cam433_suffix"]
    assert ordinary["total"] == ordinary["ordinary"] == ordinary["unresolved"] == 2
    assert ordinary["needs_attention"]
    assert rows["T7/07_Cam433_suffix/child"]["total"] == 1
    assert rows["T7/07_Cam433_suffix_VR"]["references"] == 1


def test_suggesting_does_not_assign_and_confirmed_folder_correction_survives_scan(folders):
    dp, project, root = folders
    management = dp.get_deployment_management(project.id)
    folder = next(row for row in management["folders"] if row["folder"] == "T7/07_Cam433_suffix")
    candidates = dp.get_project_deployments(project.id)
    target = rank_folder_deployments(folder, candidates)[0]["id"]
    with dp.Session() as session:
        assert (
            session.query(Video).filter_by(project_id=project.id, deployment_id=None).count() == 4
        )
    preview = dp.preview_deployment_assignment(
        project.id,
        target_id=target,
        action="folder",
        folder={"root_id": folder["root_id"], "path": folder["folder"]},
    )
    summary = summarize_folder_preview(preview)
    assert len(summary) == 1
    assert summary[0]["before"] is None
    assert summary[0]["after"] == "7_cam433"
    assert summary[0]["count"] == 2
    dp.apply_deployment_assignment(preview)
    (root / folder["folder"] / "future.mp4").touch()
    dp.sync_videos(active_project_id=project.id)
    rows = {row["folder"]: row for row in dp.get_deployment_management(project.id)["folders"]}
    assert rows[folder["folder"]]["total"] == 3
    assert rows[folder["folder"]]["unresolved"] == 0
    assert not rows[folder["folder"]]["needs_attention"]
    assert rows[folder["folder"] + "/child"]["unresolved"] == 1


def test_folder_summary_shows_manual_exceptions_and_confirmation_preserves_them(folders):
    dp, project, root = folders
    with dp.Session() as session:
        video_id = (
            session.query(Video.video_id)
            .filter_by(video_path=str(root / "T7/07_Cam433_suffix/a.mp4"))
            .scalar()
        )
    inventory_rows = {
        row["deployment_id"]: row["id"] for row in dp.get_project_deployments(project.id)
    }
    dp.apply_deployment_assignment(
        dp.preview_deployment_assignment(project.id, [video_id], inventory_rows["7_cam536"])
    )
    folder = next(
        row
        for row in dp.get_deployment_management(project.id)["folders"]
        if row["folder"] == "T7/07_Cam433_suffix"
    )
    assert folder["exceptions"] == 1
    preview = dp.preview_deployment_assignment(
        project.id,
        target_id=inventory_rows["7_cam433"],
        action="folder",
        folder={"root_id": folder["root_id"], "path": folder["folder"]},
    )
    preserved = [row for row in summarize_folder_preview(preview) if row["preserved"]]
    assert sum(row["count"] for row in preserved) == 1
    assert preserved[0]["before"] == preserved[0]["after"] == "7_cam536"
    dp.apply_deployment_assignment(preview)
    with dp.Session() as session:
        assert session.get(Video, video_id).deployment_id == inventory_rows["7_cam536"]


def test_folder_review_uses_existing_stale_preview_guard(folders):
    dp, project, root = folders
    folder = next(
        row
        for row in dp.get_deployment_management(project.id)["folders"]
        if row["folder"] == "T7/07_Cam433_suffix"
    )
    target = dp.get_project_deployments(project.id)[0]["id"]
    preview = dp.preview_deployment_assignment(
        project.id,
        target_id=target,
        action="folder",
        folder={"root_id": folder["root_id"], "path": folder["folder"]},
    )
    (root / folder["folder"] / "late.mp4").touch()
    dp.sync_videos(active_project_id=project.id)
    with pytest.raises(DeploymentInputError, match="stale"):
        dp.apply_deployment_assignment(preview)
