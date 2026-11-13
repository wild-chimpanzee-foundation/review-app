"""Interact with the real assignment UI and database, without launching a browser."""

import pytest
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from review_app.app.components.assignment_handoff import (
    assignment_filename,
    render_assignment_handoff,
    render_open_assignment,
)
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

    @ui.page("/handoff-open")
    def opening():
        render_open_assignment(lambda: ui.label("Working project opened"))

    @ui.page("/handoff-working")
    def working():
        render_assignment_handoff(dp, get_active_project_id())

    yield dp, project, root
    set_data_provider(None)


async def test_species_roundtrip_through_prepare_open_return_preview_accept(
    user: User, handoff_ui, monkeypatch
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
    # 'All videos' must not inherit the Review queue's missing-footage filter.
    with dp.Session.begin() as session:
        session.query(Video).filter_by(project_id=project.id).first().is_missing = True
    assert len(dp.get_video_queue({}, active_project_id=project.id)) == 1
    await user.open("/handoff-admin")
    with user:
        recipient = next(iter(user.find(t("handoff_recipient"), kind=ui.select).elements))
        assert "alice" in recipient.options
        recipient.set_value("alice")
        next(iter(user.find(t("handoff_scope"), kind=ui.select).elements)).set_value("all")
    user.find(t("handoff_prepare"), kind=ui.button).click()
    await user.should_see(t("handoff_prepared", count=2))
    assert len(dp.list_assignments(project.id)) == 1
    user.find(t("handoff_download"), kind=ui.button).click()
    setup = (await user.download.next()).content
    info = dp.inspect_assignment(setup)
    assert info["recipient"] == "alice" and info["video_count"] == 2
    assert filenames[-1] == f"assignment-alice-{info['assignment_id']}.zip"

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
    assert not button(user, "handoff_accept").enabled
    await upload(user, returned)
    await user.should_see(t("handoff_return_loaded"))
    user.find(t("handoff_preview"), kind=ui.button).click()
    await user.should_see(t("handoff_keep_warning"))
    assert not button(user, "handoff_accept").enabled
    expansion = next(iter(user.find(t("handoff_details"), kind=ui.expansion).elements))
    assert not expansion.value
    # Simulate Quasar's model update; User.click doesn't emulate expansions.
    with user:
        expansion.set_value(True)
    assert expansion.value
    # A policy change replans automatically, with no second Preview click.
    with user:
        next(iter(user.find(t("handoff_policy"), kind=ui.select).elements)).set_value(
            "use_imported"
        )
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


async def test_open_reports_missing_footage_and_keeps_fresh_project(
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
    await user.should_see(t("handoff_missing", count=2))
    assert not button(user, "handoff_open").enabled
    with user:
        working = get_active_project_id()
    assert working != project.id and dp.get_project_video_count(working) == 2
    user.find(t("handoff_continue"), kind=ui.button).click()
    await user.should_see("Working project opened")
