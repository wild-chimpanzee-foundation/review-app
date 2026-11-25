"""Interact with the real assignment UI and database, without launching a browser."""

import asyncio
import io
import re
import threading
import zipfile

import pytest
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from review_app.app.components.assignment_handoff import render_open_assignment
from review_app.app.pages.data_assignments.assignments import render_assignments
from review_app.app.pages.data_assignments.legacy_import import render_legacy_annotations_import
from review_app.app.state import get_active_project_id, set_data_provider
from review_app.app.translations import t
from review_app.backend.annotation_package.delivery import CopyResult
from review_app.backend.annotation_package.detection import inspect_legacy_package
from review_app.backend.annotation_package.naming import package_stem
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import Deployment, Video

pytest_plugins = ["nicegui.testing.user_plugin"]


def upload_element(user):
    return next(e for e in user.client.elements.values() if isinstance(e, ui.upload))


def button(user, key):
    return next(iter(user.find(t(key), kind=ui.button).elements))


async def upload(user, raw):
    if not any(isinstance(e, ui.upload) for e in user.client.elements.values()):
        user.find(t("assign_accept"), kind=ui.button).click()
        await user.should_see(t("handoff_return_upload"))
    with user:
        element = upload_element(user)
        await element.handle_uploads([SmallFileUpload("assignment.zip", "application/zip", raw)])
    if element.props.get("label") == t("handoff_return_upload"):
        await user.should_see(t("handoff_return_loaded"))


@pytest.fixture
def handoff_ui(provider_with_project):
    dp, project, root = provider_with_project
    set_data_provider(dp)

    @ui.page("/handoff-admin")
    def admin():
        app.storage.user["active_project_id"] = project.id
        render_assignments(dp, project.id)

    @ui.page("/handoff-distribution")
    def distribution():
        app.storage.user["active_project_id"] = project.id
        render_assignments(dp, project.id)

    @ui.page("/handoff-legacy-import")
    def legacy_import():
        app.storage.user["active_project_id"] = project.id
        render_legacy_annotations_import(dp, project.id)

    @ui.page("/handoff-open")
    def opening():
        render_open_assignment(lambda: ui.label("Working project opened"))

    @ui.page("/handoff-working")
    def working():
        render_assignments(dp, get_active_project_id())

    yield dp, project, root
    set_data_provider(None)


def test_legacy_package_summary_counts_metadata_rows():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle.json", '{"contents": ["species", "metadata"]}')
        archive.writestr("species.csv", "video_path,species\na.mp4,deer\n")
        archive.writestr("metadata.csv", "video_path,assigned_to\na.mp4,Alice\nb.mp4,Bob\n")
    assert inspect_legacy_package(buffer.getvalue()) == {
        "species_count": 1,
        "tag_count": 0,
        "prediction_count": 0,
        "metadata_count": 2,
    }


async def test_assignment_package_recipient_mapping_ui_suggests_login_and_keeps_package_name(
    user: User, handoff_ui
):
    dp, project, root = handoff_ui
    dp.add_annotator("local reviewer")
    prepared = dp.prepare_assignment(
        project.id,
        AssignmentRequest("remote recipient", tuple(dp.get_assignment_video_ids(project.id))),
    )
    await user.open("/handoff-open")
    with user:
        app.storage.user["annotator_name"] = "local reviewer"
    await upload(user, dp.export_assignment(prepared.id))
    await user.should_see(marker="package-local-recipient")
    await user.should_see(
        t("package_included_summary", **dp.inspect_assignment(dp.export_assignment(prepared.id)))
    )
    await user.should_see(
        t("package_identity_mapping", recipient="remote recipient", current="local reviewer")
    )
    selector = next(iter(user.find(marker="package-local-recipient").elements))
    assert selector.value == "local reviewer"
    assert "__package_recipient__" in selector.options


async def test_distribution_uses_saved_delivery_and_keeps_independent_csv_import(
    user: User, handoff_ui
):
    dp, project, _ = handoff_ui
    video_id, assigned_id = dp.get_assignment_video_ids(project.id)
    dp.update_manual_review(
        video_id, [{"species": "deer", "count": 3}], active_project_id=project.id
    )
    csv = dp.export_annotations_csv(project.id).to_csv(index=False).encode("utf-8-sig")
    dp.update_manual_review(video_id, [], active_project_id=project.id)
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", (assigned_id,)))
    await user.open("/handoff-distribution")
    await user.should_see(t("assign_title"))
    await user.should_not_see(t("handoff_download"))
    await user.should_not_see(t("distribution_title"))
    table = next(iter(user.find(ui.table).elements))
    user.find(ui.table).trigger("rowClick", [None, table.rows[0], 0])
    await user.should_not_see(t("handoff_download"))
    await user.should_see(t("assign_save_delivery"))
    await user.should_not_see(t("batch_import_btn"))
    texts = {getattr(e, "text", None) for e in user.client.elements.values()}
    assert t("bundle_download_all_btn") not in texts
    assert t("video_zip_btn") not in texts
    user.find(t("assign_accept"), kind=ui.button).click()
    await user.should_see(t("handoff_return_upload"))
    await user.should_not_see(t("batch_import_title"))
    csv_upload = next(
        e
        for e in user.client.elements.values()
        if isinstance(e, ui.upload) and e.props.get("label") == t("handoff_return_upload")
    )
    with user:
        await csv_upload.handle_uploads([SmallFileUpload("annotations.csv", "text/csv", csv)])
    await user.should_see(t("handoff_legacy_csv_loaded"))
    assert dp.get_video_detail(video_id)["manual_selections"] == []
    await user.should_not_see(t("handoff_accept"), kind=ui.button)
    user.find(t("handoff_import_csv"), kind=ui.button).click()
    await user.should_see(t("batch_import_summary", files=1, imported=2, skipped=0), retries=20)
    assert dp.get_video_detail(video_id)["manual_selections"][0]["count"] == 3
    assert dp.list_assignments(project.id)[0]["id"] == saved.id


async def test_remove_assignment_requires_confirmation_and_keeps_return_acceptance(
    user: User, handoff_ui
):
    dp, project, root = handoff_ui
    origin_id = dp.get_assignment_video_ids(project.id)[0]
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", (origin_id,)))
    opened = dp.open_assignment(dp.export_assignment(saved.id), "Working", {root.id: root.path})
    local_id = dp.get_assignment_video_ids(opened.project_id)[0]
    dp.update_manual_review(
        local_id, [{"species": "deer", "count": 3}], active_project_id=opened.project_id
    )
    returned = dp.export_assignment_return(opened.project_id)
    await user.open("/handoff-admin")
    table = next(iter(user.find(ui.table).elements))
    user.find(ui.table).trigger("rowClick", [None, table.rows[0], 0])
    await user.should_see(marker="remove-assignment")
    user.find(marker="remove-assignment").click()
    await user.should_see(t("assign_remove_help"))
    assert dp.list_assignments(project.id)[0]["id"] == saved.id
    user.find(marker="confirm-remove-assignment").click()
    await user.should_see(t("assign_removed"))
    await user.should_see(t("data_no_assignments"))
    assert not dp.list_assignments(project.id)
    await upload(user, returned)
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(
        t("handoff_totals", added=1, unchanged=0, replaced=0, unapplied=0, annotations_removed=0)
    )
    user.find(t("handoff_accept"), kind=ui.button).click()
    await user.should_see(t("handoff_accepted"))
    assert dp.get_video_detail(origin_id)["manual_selections"][0]["count"] == 3


async def test_unified_allocation_prepares_recipients_and_retries_only_failures(
    user: User, handoff_ui, monkeypatch
):
    dp, project, _ = handoff_ui
    ids = dp.get_assignment_video_ids(project.id)
    for name, video_id in zip(("alice", "bob"), ids, strict=True):
        dp.add_annotator(name)
        dp.apply_chunk_assignment(
            project.id,
            {name: ["selection"]},
            [{"chunk_id": "selection", "video_ids": (video_id,)}],
        )
    original = dp.prepare_species_assignment
    calls = []
    finish_first_attempt = threading.Event()

    def prepare(project_id, request, clear_video_ids):
        calls.append(request.recipient)
        if request.recipient == "bob" and calls.count("bob") == 1:
            assert finish_first_attempt.wait(timeout=5)
            raise ValueError("Temporary preparation failure")
        return original(project_id, request, clear_video_ids)

    monkeypatch.setattr(dp, "prepare_species_assignment", prepare)
    await user.open("/handoff-admin")
    user.find(t("assign_title"), kind=ui.button).click()
    await user.should_see("alice", kind=ui.label)
    await user.should_see(t("assign_video_count", count=1), kind=ui.label)
    await user.should_not_see(t("distribution_apply_btn"))
    await user.should_not_see(t("handoff_prepare"), kind=ui.button)
    assert not dp.list_assignments(project.id)
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("assign_prepare"), kind=ui.button)
    dialog = next(iter(user.find(marker="assignment-preparation", kind=ui.dialog).elements))
    user.find(t("assign_prepare"), kind=ui.button).click().click()
    try:
        await user.should_see(t("assign_preparing", done=1, total=2), retries=20)
        await user.should_see(kind=ui.spinner)
        assert dialog.value
    finally:
        finish_first_attempt.set()
    await user.should_see("Temporary preparation failure", retries=20)
    assert not dialog.value
    await user.should_see(t("assign_view"), retries=20)
    assert calls == ["alice", "bob"]
    assert len(dp.list_assignments(project.id)) == 1
    user.find(t("assign_prepare"), kind=ui.button).click()
    await user.should_not_see("Temporary preparation failure", retries=20)
    assert calls == ["alice", "bob", "bob"]
    assert len(dp.list_assignments(project.id)) == 2
    assert not button(user, "assign_prepare").enabled
    # Draft/allocation changes cannot change either saved handoff.
    snapshots = {
        row["id"]: dp.export_assignment(row["id"]) for row in dp.list_assignments(project.id)
    }
    dp.apply_chunk_assignment(
        project.id,
        {"alice": ["selection"]},
        [{"chunk_id": "selection", "video_ids": tuple(ids)}],
    )
    assert all(dp.export_assignment(key) == raw for key, raw in snapshots.items())


async def test_new_recipient_can_be_added_without_leaving_creation(user: User, handoff_ui):
    dp, project, _ = handoff_ui
    ids = dp.get_assignment_video_ids(project.id)
    with dp.Session.begin() as session:
        first, second = (session.get(Video, video_id) for video_id in ids)
        camera_a = Deployment(project_id=project.id, deployment_id="camera-a")
        camera_b = Deployment(project_id=project.id, deployment_id="camera-b")
        session.add_all([camera_a, camera_b])
        session.flush()
        first.deployment_id, first.duration_sec = camera_a.id, 3600
        second.deployment_id, second.duration_sec = camera_b.id, None
    await user.open("/handoff-admin")
    user.find(t("assign_title"), kind=ui.button).click()
    await user.should_see(t("distribution_add_annotator"))
    user.find(t("distribution_annotator_placeholder"), kind=ui.input).type("Élodie")
    user.find(t("distribution_add_annotator"), kind=ui.button).click()
    await user.should_see(kind=ui.select, content="Élodie")
    assert "Élodie" in dp.get_all_annotators()
    user.find(t("distribution_auto_btn"), kind=ui.button).click()
    await user.should_see("Élodie", kind=ui.label)
    await user.should_see(t("assign_video_count", count=2), kind=ui.label)
    await user.should_see(t("assign_camera_count", count=2))
    await user.should_not_see("camera-a", kind=ui.label)
    await user.should_not_see("camera-b", kind=ui.label)
    table = next(iter(user.find(marker="species-allocation", kind=ui.table).elements))
    assert {row["camera"] for row in table.rows} == {"camera-a", "camera-b"}
    await user.should_see(t("assign_hours", hours="1.00"))
    await user.should_see(t("assign_unknown_durations", count=1))
    assert not dp.get_assigned_video_ids(project.id, "Élodie")  # Still only a draft.
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("assign_prepare"), kind=ui.button)
    await user.should_see(t("assign_camera_count", count=2))
    await user.should_see(t("assign_hours", hours="1.00"))
    await user.should_see(t("assign_unknown_durations", count=1))
    user.find(t("assign_prepare"), kind=ui.button).click()
    await user.should_see(marker="download-packages", kind=ui.button)
    await user.should_see(t("assign_camera_count", count=2))
    await user.should_see(t("assign_hours", hours="1.00"))
    await user.should_see(t("assign_unknown_durations", count=1))
    assert len(dp.get_assigned_video_ids(project.id, "Élodie")) == 2
    saved = dp.list_assignments(project.id)[0]
    await user.should_not_see(saved["assignment_id"], kind=ui.label)
    user.find(t("assign_view"), kind=ui.button).click()
    await user.should_see(t("assign_reference", assignment_reference="001"))
    await user.should_see(t("assign_saved_scope"), kind=ui.expansion)
    with user:
        next(iter(user.find(t("assign_saved_scope"), kind=ui.expansion).elements)).set_value(True)
    await user.should_see(marker="assignment-media")
    media_table = next(iter(user.find(marker="assignment-media").elements))
    assert len(media_table.rows) == 2
    assert {row["role"] for row in media_table.rows} == {t("assign_work_media")}


async def test_save_delivery_defaults_to_zip_only(user: User, handoff_ui, tmp_path):
    dp, project, _ = handoff_ui
    saved = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", tuple(dp.get_assignment_video_ids(project.id)))
    )
    await user.open("/handoff-admin")
    table = next(iter(user.find(ui.table).elements))
    user.find(ui.table).trigger("rowClick", [None, table.rows[0], 0])
    await user.should_see(marker="open-delivery")
    user.find(marker="open-delivery").click()
    await user.should_not_see(marker="browse-delivery-folder")
    include = next(iter(user.find(t("assign_include_footage"), kind=ui.checkbox).elements))
    assert not include.value
    user.find(t("handoff_copy_output")).type(str(tmp_path / "delivery"))
    user.find(marker="save-delivery").click()
    stem = package_stem(dp.list_assignments(project.id)[0])
    path = tmp_path / "delivery" / stem
    await user.should_see(t("assign_delivery_saved", path=str(path)))
    assert (path / f"{stem}.zip").read_bytes() == dp.export_assignment(saved.id)
    assert not list(path.rglob("*.mp4"))


async def test_saved_assignment_delivery_control(user: User, handoff_ui, tmp_path):
    dp, project, _ = handoff_ui
    saved = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", tuple(dp.get_assignment_video_ids(project.id)))
    )
    await user.open("/handoff-admin")
    table = next(iter(user.find(ui.table).elements))
    user.find(ui.table).trigger("rowClick", [None, table.rows[0], 0])
    await user.should_see(marker="open-delivery")
    user.find(marker="open-delivery").click()
    with user:
        next(iter(user.find(t("assign_include_footage"), kind=ui.checkbox).elements)).set_value(
            True
        )
    user.find(t("handoff_copy_output")).type(str(tmp_path / "delivery"))
    user.find(marker="save-delivery").click()
    await user.should_see(marker="delivery-copy-progress")
    stem = package_stem(dp.list_assignments(project.id)[0])
    path = tmp_path / "delivery" / stem
    await user.should_see(t("handoff_copy_result", copied=2, missing=0, failed=0, path=str(path)))
    assert len(list(path.rglob("*.mp4"))) == 2
    assert next(iter(user.find(marker="save-delivery").elements)).enabled
    assert (path / f"{stem}.zip").read_bytes() == dp.export_assignment(saved.id)


async def test_delivery_copy_progress_updates_while_copy_paused_and_resets_on_retry(
    user: User, handoff_ui, monkeypatch, tmp_path
):
    dp, project, _ = handoff_ui
    saved = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", tuple(dp.get_assignment_video_ids(project.id)))
    )
    gates = [threading.Event(), threading.Event()]
    entered = [asyncio.Event(), asyncio.Event()]
    loop = asyncio.get_running_loop()
    calls = [0]

    def paused_copy(
        project_id, assignment_id, output_dir, *, include_footage=False, progress_callback=None
    ):
        index = calls[0]
        calls[0] += 1
        if index == 0:
            progress_callback(1, 2)
        loop.call_soon_threadsafe(entered[index].set)
        assert gates[index].wait(timeout=5)
        progress_callback(1, 2) if index == 1 else progress_callback(2, 2)
        if index == 1:
            progress_callback(2, 2)
        return CopyResult(str(tmp_path / f"delivery-{index}"), {}, 2, (), {})

    monkeypatch.setattr(dp, "save_assignment_delivery", paused_copy)
    await user.open("/handoff-admin")
    table = next(iter(user.find(ui.table).elements))
    user.find(ui.table).trigger("rowClick", [None, table.rows[0], 0])
    await user.should_see(marker="open-delivery")
    user.find(marker="open-delivery").click()
    with user:
        next(iter(user.find(t("assign_include_footage"), kind=ui.checkbox).elements)).set_value(
            True
        )
    user.find(t("handoff_copy_output")).type(str(tmp_path))
    user.find(marker="save-delivery").click()

    await asyncio.wait_for(entered[0].wait(), timeout=3)
    await user.should_see(t("assign_delivery_progress", done=1, total=2))
    progress = next(iter(user.find(marker="delivery-copy-progress").elements))
    assert progress.value == 0.5
    assert next(iter(user.find(marker="delivery-copy-status").elements)).text == t(
        "assign_delivery_progress", done=1, total=2
    )
    gates[0].set()
    await user.should_see(t("assign_delivery_saved", path=str(tmp_path / "delivery-0")))

    user.find(marker="save-delivery").click()
    await asyncio.wait_for(entered[1].wait(), timeout=3)
    await user.should_see(t("assign_delivery_progress", done=0, total=2))
    assert progress.value == 0
    assert next(iter(user.find(marker="delivery-copy-status").elements)).text == t(
        "assign_delivery_progress", done=0, total=2
    )
    gates[1].set()
    await user.should_see(t("assign_delivery_saved", path=str(tmp_path / "delivery-1")))
    assert calls[0] == 2
    assert saved.id


async def test_species_roundtrip_through_prepare_open_return_preview_accept(
    user: User, handoff_ui, monkeypatch, tmp_path
):
    dp, project, root = handoff_ui
    dp.add_annotator("alice")
    filenames = []
    download_type = type(user.download)
    original_download = download_type.__call__

    def capture_download(self, src, filename=None, media_type=""):
        filenames.append(filename)
        return original_download(self, src, filename, media_type)

    monkeypatch.setattr(download_type, "__call__", capture_download)
    # Manual allocation must not inherit the Review queue's missing-footage filter.
    with dp.Session.begin() as session:
        session.query(Video).filter_by(project_id=project.id).first().is_missing = True
    assert len(dp.get_video_queue({}, active_project_id=project.id)) == 1
    await user.open("/handoff-admin")
    user.find(t("assign_title"), kind=ui.button).click()
    await user.should_see(t("assign_adjust"))
    await user.should_not_see(t("handoff_scope_all"))
    with user:
        next(iter(user.find(t("assign_adjust"), kind=ui.expansion).elements)).set_value(True)
        table = next(iter(user.find(marker="species-allocation", kind=ui.table).elements))
        (row,) = table.rows
        listener = next(
            event for event in table._event_listeners.values() if event.type == "assignRecipient"
        )
        table._handle_event({"listener_id": listener.id, "args": [row["id"], "alice"]})
        assert table.rows[0]["recipient"] == "alice"
    await user.should_see("alice", kind=ui.label)
    await user.should_see(t("assign_video_count", count=2), kind=ui.label)
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("assign_prepare"), kind=ui.button)
    user.find(t("assign_prepare"), kind=ui.button).click()
    await user.should_see(t("handoff_prepared", count=2))
    assert len(dp.list_assignments(project.id)) == 1
    saved = dp.list_assignments(project.id)[0]
    await user.should_see(marker="download-packages")
    user.find(marker="download-packages").click()
    user.find(t("handoff_copy_output"), kind=ui.input).type(str(tmp_path))
    user.find(marker="save-packages").click()
    await user.should_see(t("assign_download_saved", count=1, path=str(tmp_path)))
    (package,) = tmp_path.glob("*.zip")
    assert package.name == f"{package_stem(saved)}.zip"
    assert re.fullmatch(r"Test_Project-species-alice-001-setup-\d{8}-\d{6}Z\.zip", package.name)
    setup = package.read_bytes()
    info = dp.inspect_assignment(setup)
    assert info["recipient"] == "alice" and info["video_count"] == 2

    await user.open("/handoff-open")
    assert not button(user, "handoff_open").enabled
    await upload(user, setup)
    await user.should_see(t("handoff_root", number=1))
    user.find(t("handoff_root", number=1)).type(root.path)
    user.find(t("handoff_open"), kind=ui.button).click()
    await user.should_see("Working project opened")
    with user:
        working_id = get_active_project_id()
    assert working_id != project.id
    local_ids = dp.get_video_queue({}, active_project_id=working_id)
    dp.update_manual_review(
        local_ids[0],
        [{"species": "deer", "count": 3}],
        is_blank=False,
        labeled_by="alice",
        active_project_id=working_id,
    )
    await user.open("/handoff-working")
    user.find(t("handoff_return"), kind=ui.button).click()
    returned = (await user.download.next()).content
    assert re.fullmatch(r"Test_Project-species-alice-001-return-\d{8}-\d{6}Z\.zip", filenames[-1])
    origin_id = next(
        vid
        for vid in dp.get_assignment_video_ids(project.id)
        if dp.get_video_detail(vid)["video_path"]
        == dp.get_video_detail(local_ids[0])["video_path"]
    )
    dp.update_manual_review(
        origin_id, [{"species": "fox", "count": 9}], active_project_id=project.id
    )
    await user.open("/handoff-admin")
    user.find(t("assign_accept"), kind=ui.button).click()
    await user.should_see(t("handoff_return_upload"))
    assert not button(user, "handoff_accept").enabled
    await upload(user, returned)
    await user.should_see(t("handoff_return_loaded"))
    policy_select = next(iter(user.find(t("handoff_policy"), kind=ui.select).elements))
    assert policy_select.value == "use_imported"
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(t("handoff_replace_warning"))
    assert button(user, "handoff_accept").enabled
    expansion = next(iter(user.find(t("handoff_details"), kind=ui.expansion).elements))
    assert not expansion.value
    # Simulate Quasar's model update; User.click doesn't emulate expansions.
    with user:
        expansion.set_value(True)
    assert expansion.value
    # Policy changes replan automatically, with no second Preview click.
    with user:
        policy_select.set_value("keep_existing")
    await user.should_see(t("handoff_keep_warning"))
    assert not button(user, "handoff_accept").enabled
    with user:
        policy_select.set_value("use_imported")
    await user.should_see(t("handoff_replace_warning"))
    assert button(user, "handoff_accept").enabled
    assert not next(iter(user.find(t("handoff_details"), kind=ui.expansion).elements)).value
    table = next(e for e in user.client.elements.values() if isinstance(e, ui.table))
    assert any(row["action"] == t("handoff_action_replaced") for row in table.rows)
    user.find(t("handoff_accept"), kind=ui.button).click()
    await user.should_see(t("handoff_accepted"))
    assert dp.preview_assignment_return(project.id, returned).totals["unchanged"] == 2
    assert not button(user, "handoff_accept").enabled


async def test_invalid_upload_and_stale_preview_cannot_apply_work(user: User, handoff_ui):
    dp, project, root = handoff_ui
    ids = dp.get_video_queue({}, active_project_id=project.id)
    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    setup = dp.export_assignment(prepared.id)
    opened = dp.open_assignment(setup, "Working", {root.id: root.path})
    local = dp.get_video_queue({}, active_project_id=opened.project_id)[0]
    dp.update_manual_review(
        local, [{"species": "deer", "count": 2}], active_project_id=opened.project_id
    )
    returned = dp.export_assignment_return(opened.project_id)
    await user.open("/handoff-admin")
    await upload(user, returned)
    await user.should_see(t("handoff_return_loaded"))
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(ui.table)
    await upload(user, b"not a ZIP")
    await user.should_see(t("handoff_return_loaded"))
    assert not button(user, "handoff_accept").enabled
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see("Invalid assignment archive")
    assert not button(user, "handoff_accept").enabled
    await upload(user, returned)
    await user.should_see(t("handoff_return_loaded"))
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(ui.table)
    dp.update_manual_review(ids[0], [{"species": "fox", "count": 9}], active_project_id=project.id)
    user.find(t("handoff_accept"), kind=ui.button).click()
    await user.should_see("Return preview is stale")
    assert dp.get_video_detail(ids[0])["manual_selections"][0]["count"] == 9
    assert not button(user, "handoff_accept").enabled


async def test_missing_footage_can_be_corrected_before_creating_project(
    user: User, handoff_ui, tmp_path
):
    dp, project, root = handoff_ui
    ids = dp.get_video_queue({}, active_project_id=project.id)
    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    await user.open("/handoff-open")
    await upload(user, dp.export_assignment(prepared.id))
    await user.should_see(t("handoff_root", number=1))
    user.find(t("handoff_root", number=1)).type(str(tmp_path / "not-delivered"))
    user.find(t("handoff_open"), kind=ui.button).click()
    await user.should_see(t("handoff_missing_folders", count=2))
    assert button(user, "handoff_open").enabled
    with user:
        assert get_active_project_id() is None  # No project is activated by a failed path check.
    assert len(dp.list_projects()) == 1
    with user:
        next(iter(user.find(t("handoff_root", number=1), kind=ui.input).elements)).set_value(
            root.path
        )
    await user.should_not_see(t("handoff_open_missing"))
    user.find(t("handoff_open"), kind=ui.button).click()
    await user.should_see("Working project opened", retries=10)
    with user:
        working = get_active_project_id()
    assert working != project.id and dp.get_project_video_count(working) == 2
    assert len(dp.list_projects()) == 2


async def test_bulk_assignment_delivery_and_removal(user: User, handoff_ui, tmp_path):
    dp, project, _ = handoff_ui
    ids = dp.get_assignment_video_ids(project.id)
    saved = [
        dp.prepare_assignment(project.id, AssignmentRequest(name, (ids[0],)))
        for name in ("alice", "bob", "unselected")
    ]
    await user.open("/handoff-admin")
    table = next(iter(user.find(ui.table).elements))
    assert table.selection == "multiple"
    selected = [row for row in table.rows if row["id"] in {item.id for item in saved[:2]}]
    user.find(ui.table).trigger(
        "selection", {"added": True, "rows": selected, "keys": [row["id"] for row in selected]}
    )
    await user.should_see(t("assign_selected_count", count=2))
    assert {row["id"] for row in table.selected} == {item.id for item in saved[:2]}
    await user.should_not_see(marker="download-selected-assignments")

    user.find(marker="open-delivery").click()
    user.find(t("handoff_copy_output")).type(str(tmp_path))
    user.find(marker="save-delivery").click()
    await user.should_see(t("assign_deliveries_saved", saved=2, total=2))
    result_details = user.find(marker="delivery-result").elements
    assert len(result_details) == 2
    assert all(isinstance(detail, ui.expansion) and not detail.value for detail in result_details)
    bindings = {row["id"]: row for row in dp.list_assignments(project.id)}
    assert {detail.text for detail in result_details} == {
        f"{t('assign_reference', **bindings[item.id])} · {item.recipient}" for item in saved[:2]
    }
    stems = {key: package_stem(row) for key, row in bindings.items()}
    for item in saved[:2]:
        stem = stems[item.id]
        path = tmp_path / stem
        await user.should_see(t("assign_delivery_saved", path=str(path)))
        assert (path / f"{stem}.zip").read_bytes() == dp.export_assignment(item.id)
    assert not (tmp_path / stems[saved[2].id]).exists()
    user.find(t("close"), kind=ui.button).click()
    user.find(marker="remove-selected-assignments").click()
    await user.should_see(t("assign_remove_selected_title", count=2))
    assert len(dp.list_assignments(project.id)) == 3
    user.find(marker="confirm-remove-selected-assignments").click()
    await user.should_not_see(marker="assignment-bulk-actions", retries=20)
    assert [row["id"] for row in dp.list_assignments(project.id)] == [saved[2].id]


async def test_bulk_selection_can_be_cleared(user: User, handoff_ui):
    dp, project, _ = handoff_ui
    ids = dp.get_assignment_video_ids(project.id)
    for name in ("alice", "bob"):
        dp.prepare_assignment(project.id, AssignmentRequest(name, (ids[0],)))
    await user.open("/handoff-admin")
    table = next(iter(user.find(ui.table).elements))
    keys = [row["id"] for row in table.rows]
    user.find(ui.table).trigger("selection", {"added": True, "rows": table.rows, "keys": keys})
    await user.should_see(marker="assignment-bulk-actions")
    user.find(ui.table).trigger("selection", {"added": False, "rows": table.rows, "keys": keys})
    await user.should_not_see(marker="assignment-bulk-actions", retries=20)
    assert not table.selected


async def test_open_with_missing_footage_requires_explicit_confirmation(
    user: User, handoff_ui, tmp_path
):
    dp, project, root = handoff_ui
    ids = dp.get_assignment_video_ids(project.id)
    prepared = dp.prepare_assignment(project.id, AssignmentRequest("alice", tuple(ids)))
    await user.open("/handoff-open")
    await upload(user, dp.export_assignment(prepared.id))
    await user.should_see(t("handoff_root", number=1))
    user.find(t("handoff_root", number=1)).type(str(tmp_path / "not-delivered"))
    user.find(t("handoff_open"), kind=ui.button).click()
    await user.should_see(t("handoff_missing_folders", count=2))
    assert len(dp.list_projects()) == 1
    user.find(t("handoff_open_missing"), kind=ui.button).click()
    await user.should_see("Working project opened")
    with user:
        working = get_active_project_id()
    assert working != project.id
    assert len(dp.list_projects()) == 2
    assert dp.get_project_video_count(working) == 2
    with dp.Session() as session:
        assert all(row.is_missing for row in session.query(Video).filter_by(project_id=working))


async def test_closing_partial_bulk_removal_refreshes_assignments(
    user: User, handoff_ui, monkeypatch
):
    dp, project, _ = handoff_ui
    video_id = dp.get_assignment_video_ids(project.id)[0]
    removed, retained = [
        dp.prepare_assignment(project.id, AssignmentRequest(name, (video_id,)))
        for name in ("alice", "bob")
    ]
    original = dp.remove_assignment

    def remove(project_id, assignment_id):
        if assignment_id == retained.id:
            raise ValueError("Temporary removal failure")
        original(project_id, assignment_id)

    monkeypatch.setattr(dp, "remove_assignment", remove)
    await user.open("/handoff-admin")
    table = next(iter(user.find(ui.table).elements))
    user.find(ui.table).trigger(
        "selection", {"added": True, "rows": table.rows, "keys": [removed.id, retained.id]}
    )
    await user.should_see(marker="remove-selected-assignments")
    user.find(marker="remove-selected-assignments").click()
    user.find(marker="confirm-remove-selected-assignments").click()
    await user.should_see("bob: Temporary removal failure")
    user.find(t("cancel"), kind=ui.button).click()
    await user.should_not_see(marker="assignment-bulk-actions", retries=20)
    table = next(iter(user.find(ui.table).elements))
    assert [row["id"] for row in table.rows] == [retained.id]


async def test_return_preview_identifies_same_relative_paths_in_different_roots(
    user: User, handoff_ui, tmp_path
):
    dp, project, root = handoff_ui
    second = tmp_path / "second-root"
    other_clip = second / "cam_x" / "a.mp4"
    other_clip.parent.mkdir(parents=True)
    other_clip.touch()
    other_root = dp.add_project_dir(project.id, str(second))
    dp.sync_videos(video_dir=second, active_project_id=project.id)
    paths = {f"{root.path}/cam_x/a.mp4", str(other_clip)}
    with dp.Session() as session:
        ids = tuple(
            row.video_id
            for row in session.query(Video).filter(
                Video.project_id == project.id, Video.video_path.in_(paths)
            )
        )
    assert len(ids) == 2
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", ids))
    opened = dp.open_assignment(
        dp.export_assignment(saved.id), "Working", {root.id: root.path, other_root.id: str(second)}
    )
    await user.open("/handoff-admin")
    await upload(user, dp.export_assignment_return(opened.project_id))
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(marker="assignment-return-videos", retries=20)
    table = next(iter(user.find(marker="assignment-return-videos").elements))
    assert {row["id"] for row in table.rows} == set(ids)
    assert {row["video"] for row in table.rows} == paths
