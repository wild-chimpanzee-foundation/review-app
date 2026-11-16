"""Species-only append selection and preview safeguards in the return UI."""

import pytest
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from review_app.app.components.assignment_handoff import render_return_acceptance
from review_app.app.translations import t
from review_app.backend.annotation_package.archive import decode_archive, encode_archive
from review_app.backend.annotation_package.scope import AssignmentRequest

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def return_ui(provider_with_project):
    dp, project, root = provider_with_project

    @ui.page("/append-return")
    def page():
        app.storage.user["active_project_id"] = project.id
        render_return_acceptance(dp, project.id, lambda: None)

    return dp, project, root


async def upload(user, data):
    with user:
        element = next(iter(user.find(ui.upload).elements))
        await element.handle_uploads([SmallFileUpload("return.zip", "application/zip", data)])
    await user.should_see(t("handoff_return_loaded"))


async def test_species_append_previews_merged_work_and_is_idempotent(user: User, return_ui):
    dp, project, root = return_ui
    video_id = dp.get_assignment_video_ids(project.id)[0]
    dp.update_manual_review(
        video_id, [{"species": "deer", "count": 1}], active_project_id=project.id
    )
    saved = dp.prepare_assignment(project.id, AssignmentRequest("alice", (video_id,)))
    opened = dp.open_assignment(dp.export_assignment(saved.id), "Working", {root.id: root.path})
    manifest, context, work = decode_archive(dp.export_assignment_return(opened.project_id))
    work[0]["observations"][0]["count"] = 4
    data = encode_archive(manifest, context, work)
    await user.open("/append-return")
    await upload(user, data)
    policy = next(iter(user.find(t("handoff_policy"), kind=ui.select).elements))
    assert "append" in policy.options
    with user:
        policy.set_value("append")
    await user.should_see(t("handoff_append_totals", count=1))
    await user.should_see(t("handoff_append_warning"))
    accept = next(iter(user.find(t("handoff_accept"), kind=ui.button).elements))
    assert accept.enabled
    assert dp.get_video_detail(video_id)["manual_selections"][0]["count"] == 1
    user.find(t("handoff_accept"), kind=ui.button).click()
    await user.should_see(t("handoff_accepted"))
    assert dp.get_video_detail(video_id)["manual_selections"][0]["count"] == 4
    await upload(user, data)
    with user:
        policy.set_value("append")
    await user.should_see(t("handoff_append_totals", count=0))
    assert not accept.enabled


async def test_switching_to_distance_return_removes_append_and_clears_confirmation(
    user: User, return_ui
):
    dp, project, root = return_ui
    video_id = dp.get_assignment_video_ids(project.id)[0]
    dp.update_manual_review(
        video_id, [{"species": "deer", "count": 1}], active_project_id=project.id
    )
    returns = []
    for stage in ("species", "distance"):
        request = AssignmentRequest(
            "alice", (video_id,), stage, "deer" if stage == "distance" else None
        )
        saved = dp.prepare_assignment(project.id, request)
        opened = dp.open_assignment(dp.export_assignment(saved.id), stage, {root.id: root.path})
        returns.append(dp.export_assignment_return(opened.project_id))
    await user.open("/append-return")
    await upload(user, returns[0])
    policy = next(iter(user.find(t("handoff_policy"), kind=ui.select).elements))
    with user:
        policy.set_value("append")
    await user.should_see(t("handoff_append_warning"))
    await upload(user, returns[1])
    assert "append" not in policy.options
    assert policy.value == "use_imported"
    assert not next(iter(user.find(t("handoff_accept"), kind=ui.button).elements)).enabled
    await user.should_not_see(t("handoff_append_warning"))
