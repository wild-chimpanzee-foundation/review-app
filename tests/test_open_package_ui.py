"""One setup entry point detects assignment and legacy formats without conflating them."""

import io
import zipfile
from pathlib import Path

import pytest
from nicegui import app, ui
from nicegui.elements.upload_files import SmallFileUpload
from nicegui.testing import User
from review_app.app.project_picker import build_project_picker
from review_app.app.setup_wizard import SetupWizard
from review_app.app.state import get_active_project_id, set_data_provider
from review_app.app.translations import t
from review_app.backend.annotation_package.detection import detect_setup_package
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.video_tools import VideoToolsStatus

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def package_ui(provider_with_project, monkeypatch):
    dp, project, root = provider_with_project
    set_data_provider(dp)
    monkeypatch.setattr(
        "review_app.app.setup_wizard.check_video_tools",
        lambda: VideoToolsStatus({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}),
    )
    monkeypatch.setattr(
        "review_app.app.setup_wizard.get_default_db_path", lambda: Path(dp.engine.url.database)
    )
    wizard = SetupWizard(lambda: ui.label("Package opened"))

    @ui.page("/open-package")
    def opening():
        app.storage.user["active_project_id"] = project.id
        wizard.build(package_only=True)

    @ui.page("/package-setup")
    def setup():
        wizard.build()

    @ui.page("/package-picker")
    def picker():
        dialog, _ = build_project_picker()
        dialog.open()

    yield dp, project, root, wizard
    set_data_provider(None)


async def upload(user, raw):
    uploader = next(
        e
        for e in user.client.elements.values()
        if isinstance(e, ui.upload) and e.props.get("label") == t("package_upload")
    )
    with user:
        await uploader.handle_uploads([SmallFileUpload("package.zip", "application/zip", raw)])


def is_visible(element):
    while element is not None:
        if not element.visible:
            return False
        element = element.parent_slot.parent if element.parent_slot else None
    return True


def elements(user, kind, text):
    return [
        e
        for e in user.client.elements.values()
        if isinstance(e, kind)
        and (getattr(e, "text", None) == text or e.props.get("label") == text)
    ]


async def test_assignment_detects_and_opens_through_common_upload(user: User, package_ui):
    dp, project, root, _ = package_ui
    saved = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", tuple(dp.get_assignment_video_ids(project.id)))
    )
    (Path(root.path) / "unlisted.mp4").touch()
    await user.open("/open-package")
    assert not any(is_visible(e) for e in elements(user, ui.button, t("wizard_bundle_submit_btn")))
    await upload(user, dp.export_assignment(saved.id))
    await user.should_see(t("handoff_root", number=1))
    await user.should_see("package.zip")
    assert not any(is_visible(e) for e in elements(user, ui.button, t("wizard_bundle_submit_btn")))
    user.find(t("handoff_root", number=1)).type(root.path)
    user.find(t("handoff_open"), kind=ui.button).click()
    await user.should_see("Package opened", retries=20)
    working = get_active_project_id()
    assert dp.list_assignments(working)[0]["role"] == "working"
    stats = dp.get_overview_stats(working)["videos"]
    assert stats["unprobed"] == 0
    assert stats["total"] == 2


async def test_failed_opening_sync_retries_without_creating_another_project(
    user: User, package_ui, monkeypatch
):
    dp, project, root, _ = package_ui
    saved = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", tuple(dp.get_assignment_video_ids(project.id)))
    )
    sync = dp.sync_videos
    attempts = []

    def flaky_sync(*args, **kwargs):
        attempts.append(kwargs["active_project_id"])
        if len(attempts) == 1:
            raise ValueError("Footage sync interrupted")
        return sync(*args, **kwargs)

    monkeypatch.setattr(dp, "sync_videos", flaky_sync)
    await user.open("/open-package")
    await upload(user, dp.export_assignment(saved.id))
    await user.should_see(t("handoff_root", number=1))
    user.find(t("handoff_root", number=1)).type(root.path)
    user.find(t("handoff_open"), kind=ui.button).click()
    await user.should_see("Footage sync interrupted", retries=20)
    assert len(dp.list_projects()) == 2
    assert get_active_project_id() == project.id
    user.find(t("handoff_retry_sync"), kind=ui.button).click()
    await user.should_see("Package opened", retries=20)
    assert len(dp.list_projects()) == 2
    assert attempts == [get_active_project_id(), get_active_project_id()]
    assert dp.get_overview_stats(get_active_project_id())["videos"]["unprobed"] == 0


@pytest.mark.parametrize("include_manifest", [True, False])
async def test_legacy_detects_warns_and_creates_independent_project(
    user: User, package_ui, include_manifest
):
    dp, project, root, wizard = package_ui
    raw = dp.export_project_bundle(project.id, include=["metadata"])
    if not include_manifest:
        csv_only = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as source, zipfile.ZipFile(csv_only, "w") as target:
            target.writestr("metadata.csv", source.read("metadata.csv"))
        raw = csv_only.getvalue()
    assert detect_setup_package(raw) == "legacy"
    await user.open("/open-package")
    await upload(user, raw)
    await user.should_see(t("package_legacy_warning"))
    assert not any(is_visible(e) for e in elements(user, ui.button, t("handoff_open")))
    with user:
        wizard.inputs["bundle_video_dir"].set_value(root.path)
        wizard.inputs["bundle_project_name"].set_value("Legacy independent")
    user.find(t("wizard_bundle_submit_btn"), kind=ui.button).click()
    await user.should_see(t("go_to_overview_btn"), retries=20)
    assert get_active_project_id() != project.id
    assert dp.list_assignments(get_active_project_id()) == []
    assert dp.get_project_video_count(get_active_project_id()) == 2


async def test_replacing_package_with_invalid_upload_hides_previous_form(user: User, package_ui):
    dp, project, _, _ = package_ui
    await user.open("/open-package")
    await upload(user, dp.export_project_bundle(project.id, include=["metadata"]))
    await user.should_see(t("package_legacy_warning"))
    assert any(is_visible(e) for e in elements(user, ui.button, t("wizard_bundle_submit_btn")))
    await upload(user, b"invalid zip")
    await user.should_see("Choose an assignment setup package or a legacy project bundle")
    assert not any(is_visible(e) for e in elements(user, ui.button, t("wizard_bundle_submit_btn")))
    assert not any(is_visible(e) for e in elements(user, ui.button, t("handoff_open")))


async def test_package_upload_is_a_compact_button_without_intro(user: User, package_ui):
    await user.open("/open-package")
    buttons = elements(user, ui.button, t("package_upload"))
    assert len(buttons) == 1
    assert buttons[0].props.get("outline")
    assert is_visible(buttons[0])
    uploads = [e for e in user.client.elements.values() if isinstance(e, ui.upload)]
    assert len(uploads) == 1
    assert uploads[0].style.get("display") == "none"
    labels = [e.text for e in user.client.elements.values() if isinstance(e, ui.label)]
    assert not any(text.startswith("Upload an assignment") for text in labels)


async def test_setup_has_one_package_tab_and_picker_has_one_entry(user: User, package_ui):
    await user.open("/package-setup")
    tabs = [e.props.get("label") for e in user.client.elements.values() if isinstance(e, ui.tab)]
    assert tabs == [t("wizard_setup_tab_manual"), t("package_open")]
    await user.open("/package-picker")
    assert len(elements(user, ui.button, t("package_open"))) == 1
    assert not elements(user, ui.button, t("handoff_open"))
    user.find(t("package_open"), kind=ui.button).click()
    await user.should_see(t("package_upload"))


def test_detection_rejects_returns_and_does_not_fallback_to_legacy(provider_with_project):
    dp, project, root = provider_with_project
    saved = dp.prepare_assignment(
        project.id, AssignmentRequest("alice", tuple(dp.get_assignment_video_ids(project.id)))
    )
    opened = dp.open_assignment(dp.export_assignment(saved.id), "Working", {root.id: root.path})
    with pytest.raises(ValueError, match="return"):
        detect_setup_package(dp.export_assignment_return(opened.project_id))
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("manifest.json", "invalid")
        archive.writestr("bundle.json", '{"contents": []}')
    with pytest.raises(ValueError):
        detect_setup_package(data.getvalue())
