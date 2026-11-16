"""Interact with the real assignment UI and database, without launching a browser."""

import asyncio

import pytest
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from review_app.app.components.assignment_handoff import (
    assignment_filename,
    render_assignment_handoff,
    render_open_assignment,
)
from review_app.app.pages.data_assignments.assignment_plan import apply_species_request
from review_app.app.pages.data_assignments.assignments import render_assignments
from review_app.app.pages.data_assignments.legacy_import import render_legacy_annotations_import
from review_app.app.state import get_active_project_id, set_data_provider
from review_app.app.translations import t
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db.models import Video

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.mark.parametrize("direction", ["assignment", "return"])
@pytest.mark.parametrize(
    ("recipient", "safe_name"),
    [("Alice Smith", "Alice_Smith"), ("../Alice/Bob", "Alice_Bob"), ("Élodie", "Élodie")],
)
def test_download_filename_keeps_recipient_without_path_characters(
    direction, recipient, safe_name
):
    assert (
        assignment_filename(direction, recipient, "assignment-id")
        == f"{direction}-{safe_name}-assignment-id.zip"
    )


def upload_element(user):
    return next(e for e in user.client.elements.values() if isinstance(e, ui.upload))


def button(user, key):
    return next(iter(user.find(t(key), kind=ui.button).elements))


async def upload(user, raw):
    if not any(isinstance(e, ui.upload) for e in user.client.elements.values()):
        user.find(t("assign_accept"), kind=ui.button).click()
        await user.should_see(t("handoff_return_upload"))
    with user:
        await upload_element(user).handle_uploads(
            [SmallFileUpload("assignment.zip", "application/zip", raw)]
        )


@pytest.fixture
def handoff_ui(provider_with_project):
    dp, project, root = provider_with_project
    set_data_provider(dp)

    @ui.page("/handoff-admin")
    def admin():
        app.storage.user["active_project_id"] = project.id
        render_assignment_handoff(dp, project.id)

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
        render_assignment_handoff(dp, get_active_project_id())

    yield dp, project, root
    set_data_provider(None)


async def test_distribution_uses_saved_delivery_and_keeps_independent_csv_import(
    user: User, handoff_ui
):
    dp, project, _ = handoff_ui
    video_id = dp.get_assignment_video_ids(project.id)[0]
    dp.update_manual_review(
        video_id, [{"species": "deer", "count": 3}], active_project_id=project.id
    )
    csv = dp.export_annotations_csv(project.id).to_csv(index=False).encode()
    dp.update_manual_review(video_id, [], active_project_id=project.id)
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", (video_id,)))
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
    await user.open("/handoff-legacy-import")
    await user.should_see(t("data_legacy_upload_warning"))
    csv_upload = next(
        e
        for e in user.client.elements.values()
        if isinstance(e, ui.upload) and e.props.get("label") == t("batch_import_btn")
    )
    with user:
        await csv_upload.handle_uploads([SmallFileUpload("annotations.csv", "text/csv", csv)])
    await user.should_see(t("batch_import_summary", files=1, imported=2, skipped=0))
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
        apply_species_request(dp, project.id, AssignmentRequest(name, (video_id,)))
    original = dp.prepare_assignment
    calls = []

    def prepare(project_id, request):
        calls.append(request.recipient)
        if request.recipient == "bob" and calls.count("bob") == 1:
            raise ValueError("Temporary preparation failure")
        return original(project_id, request)

    monkeypatch.setattr(dp, "prepare_assignment", prepare)
    await user.open("/handoff-admin")
    user.find(t("assign_title"), kind=ui.button).click()
    await user.should_see(t("assign_recipient_count", name="alice", count=1))
    await user.should_not_see(t("distribution_apply_btn"))
    await user.should_not_see(t("handoff_prepare"), kind=ui.button)
    assert not dp.list_assignments(project.id)
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("assign_freeze_help"))
    user.find(t("assign_prepare"), kind=ui.button).click().click()
    await user.should_see("Temporary preparation failure")
    await user.should_see(t("assign_view"))
    assert calls == ["alice", "bob"]
    assert len(dp.list_assignments(project.id)) == 1
    user.find(t("assign_prepare"), kind=ui.button).click()
    await user.should_not_see("Temporary preparation failure")
    assert calls == ["alice", "bob", "bob"]
    assert len(dp.list_assignments(project.id)) == 2
    assert not button(user, "assign_prepare").enabled
    # Draft/allocation changes cannot change either saved handoff.
    snapshots = {
        row["id"]: dp.export_assignment(row["id"]) for row in dp.list_assignments(project.id)
    }
    apply_species_request(dp, project.id, AssignmentRequest("alice", tuple(ids)))
    assert all(dp.export_assignment(key) == raw for key, raw in snapshots.items())


async def test_new_recipient_can_be_added_without_leaving_creation(user: User, handoff_ui):
    dp, project, _ = handoff_ui
    await user.open("/handoff-admin")
    user.find(t("assign_title"), kind=ui.button).click()
    await user.should_see(t("distribution_add_annotator"))
    user.find(t("distribution_annotator_placeholder"), kind=ui.input).type("Élodie")
    user.find(t("distribution_add_annotator"), kind=ui.button).click()
    await user.should_see(kind=ui.select, content="Élodie")
    assert "Élodie" in dp.get_all_annotators()
    user.find(t("distribution_auto_btn"), kind=ui.button).click()
    await user.should_see(t("assign_recipient_count", name="Élodie", count=2))
    assert not dp.get_assigned_video_ids(project.id, "Élodie")  # Still only a draft.
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("assign_freeze_help"))
    user.find(t("assign_prepare"), kind=ui.button).click()
    await user.should_see(t("assign_save_delivery"))
    assert len(dp.get_assigned_video_ids(project.id, "Élodie")) == 2
    saved = dp.list_assignments(project.id)[0]
    technical = next(iter(user.find(t("assign_technical"), kind=ui.expansion).elements))
    assert not technical.value
    table = next(iter(user.find(ui.table).elements))
    assert saved["assignment_id"] not in str(table.rows)
    assert saved["created_at"] not in str(table.rows)
    with user:
        next(iter(user.find(t("assign_saved_scope"), kind=ui.expansion).elements)).set_value(True)
    await user.should_see(marker="assignment-media")
    media_table = next(iter(user.find(marker="assignment-media").elements))
    assert len(media_table.rows) == 2
    assert {row["role"] for row in media_table.rows} == {t("assign_work_media")}


@pytest.mark.parametrize("selection", ["chosen", "cancel", "unavailable"])
async def test_delivery_folder_picker(user: User, handoff_ui, monkeypatch, tmp_path, selection):
    dp, project, _ = handoff_ui
    dp.prepare_assignment(
        project.id, AssignmentRequest("alice", tuple(dp.get_assignment_video_ids(project.id)))
    )
    calls = []

    def pick(initial):
        calls.append(initial)
        if selection == "unavailable":
            raise RuntimeError("No desktop picker")
        return str(tmp_path) if selection == "chosen" else None

    monkeypatch.setattr("review_app.app.components.assignment_handoff.pick_folder", pick)
    await user.open("/handoff-admin")
    table = next(iter(user.find(ui.table).elements))
    user.find(ui.table).trigger("rowClick", [None, table.rows[0], 0])
    await user.should_see(marker="open-delivery")
    user.find(marker="open-delivery").click()
    user.find(t("handoff_copy_output")).type("existing-path")
    user.find(marker="browse-delivery-folder").click()
    await asyncio.sleep(0.1)  # Allow the off-thread picker to return, including on cancel.
    if selection == "unavailable":
        await user.should_see(t("assign_folder_picker_unavailable"))
    else:
        await user.should_see(str(tmp_path) if selection == "chosen" else "existing-path")
    assert calls == ["existing-path"]
    output = next(iter(user.find(t("handoff_copy_output"), kind=ui.input).elements))
    assert output.value == (str(tmp_path) if selection == "chosen" else "existing-path")


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
    include = next(iter(user.find(t("assign_include_footage"), kind=ui.checkbox).elements))
    assert not include.value
    user.find(t("handoff_copy_output")).type(str(tmp_path / "delivery"))
    user.find(marker="save-delivery").click()
    path = tmp_path / "delivery" / f"assignment-{saved.assignment_id}"
    await user.should_see(t("assign_delivery_saved", path=str(path)))
    assert (path / "assignment.zip").read_bytes() == dp.export_assignment(saved.id)
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
    path = tmp_path / "delivery" / f"assignment-{saved.assignment_id}"
    await user.should_see(t("handoff_copy_result", copied=2, missing=0, failed=0, path=str(path)))
    assert len(list(path.rglob("*.mp4"))) == 2
    assert next(iter(user.find(marker="save-delivery").elements)).enabled
    assert (path / "assignment.zip").read_bytes() == dp.export_assignment(saved.id)


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
        recipient = next(iter(user.find(t("handoff_recipient"), kind=ui.select).elements))
        assert "alice" in recipient.options
        recipient.set_value("alice")
    await user.should_see(t("assign_recipient_count", name="alice", count=2))
    user.find(t("assign_review"), kind=ui.button).click()
    await user.should_see(t("assign_freeze_help"))
    user.find(t("assign_prepare"), kind=ui.button).click()
    await user.should_see(t("handoff_prepared", count=2))
    assert len(dp.list_assignments(project.id)) == 1
    user.find(marker="open-delivery").click()
    user.find(t("handoff_copy_output")).type(str(tmp_path))
    user.find(marker="save-delivery").click()
    saved = dp.list_assignments(project.id)[0]
    path = tmp_path / f"assignment-{saved['assignment_id']}"
    await user.should_see(t("assign_delivery_saved", path=str(path)))
    setup = (path / "assignment.zip").read_bytes()
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
    assert filenames[-1] == f"return-alice-{info['assignment_id']}.zip"
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
    assert {detail.text for detail in result_details} == {"alice", "bob"}
    for item in saved[:2]:
        path = tmp_path / f"assignment-{item.assignment_id}"
        await user.should_see(t("assign_delivery_saved", path=str(path)))
        assert (path / "assignment.zip").read_bytes() == dp.export_assignment(item.id)
    assert not (tmp_path / f"assignment-{saved[2].assignment_id}").exists()
    user.find(t("close"), kind=ui.button).click()
    user.find(marker="remove-selected-assignments").click()
    await user.should_see(t("assign_remove_selected_title", count=2))
    assert len(dp.list_assignments(project.id)) == 3
    user.find(marker="confirm-remove-selected-assignments").click()
    await user.should_not_see(marker="assignment-bulk-actions")
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
    await user.should_not_see(marker="assignment-bulk-actions")
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
