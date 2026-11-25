"""Deployment review compares footage and previews changes on selection."""

import asyncio
from pathlib import Path

import pytest
from nicegui import app, ui
from nicegui.testing.user_simulation import user_simulation
from review_app.app import state
from review_app.app.components import playback
from review_app.app.entry_point import GUI
from review_app.app.pages.settings.species import render_species_section
from review_app.app.translations import t
from review_app.backend.db.models import Video
from review_app.backend.deployments import DeploymentInputError
from review_app.backend.transcoding import TranscodeResult
from review_app.backend.video_tools import VideoToolsStatus


@pytest.fixture
def streamed_clips(monkeypatch):
    paths = {}

    def serve(local_file, *, url_path):
        paths[url_path] = Path(local_file)
        return url_path

    monkeypatch.setattr(app, "add_media_file", serve)
    return paths


async def player_for(user, side, paths, expected):
    marker = f"deployment-review-{side}-player"
    await user.should_see(marker=marker, retries=20)
    panel = next(iter(user.find(marker=marker).elements))
    for _ in range(40):
        for element in panel.descendants():
            if isinstance(element, ui.video) and paths[element.source] == expected:
                return element
        await asyncio.sleep(0.05)
    raise AssertionError(f"{side} did not load {expected.name}")


async def test_reference_review_compares_first_clips_and_disables_self_link(
    provider_with_project,
    streamed_clips,
):
    dp, project, directory = provider_with_project
    prefix = "DS_M1Phase5_E3/T44"
    ref448 = f"{prefix}/VR-Cam_448"
    ref559 = f"{prefix}/VR-Cam_559"
    cam489 = f"{prefix}/44_cam489_802176_1104161_20260210"
    cam559 = f"{prefix}/44_cam559_802831_1104239_20260210"
    root = Path(directory.path)
    left_first = root / ref448 / "a448.mp4"
    left_second = root / ref448 / "b448.mp4"
    second_folder = root / ref559 / "a559.mp4"
    target_first = root / cam489 / "a489.mp4"
    target_second = root / cam489 / "videos/z489.mp4"
    target_alternate = root / cam559 / "a559-ordinary.mp4"
    for clip in (
        left_first,
        left_second,
        second_folder,
        target_first,
        target_alternate,
        root / cam489 / "ref/00-reference.mp4",
        target_second,
    ):
        clip.parent.mkdir(parents=True, exist_ok=True)
        clip.touch()
    dp.sync_videos(active_project_id=project.id)
    inventory = {row["deployment_id"]: row["id"] for row in dp.get_project_deployments(project.id)}

    state.set_data_provider(dp)
    state.set_annotator_name("Alice")
    state.set_active_project(project.id)
    gui = GUI()
    try:
        async with user_simulation() as user:

            @ui.page("/deployments", reconnect_timeout=3)
            async def deployments(review: str = ""):
                await gui.deployments_page(review=review)

            await user.open("/deployments")
            await user.should_see(marker="deployment-group-matched", retries=20)
            for group, deployment in (("matched", cam489), ("no_reference", cam559)):
                user.find(kind=ui.tab, marker=f"deployment-group-{group}").click()
                await user.should_see(marker=f"deployment-card-{inventory[deployment]}")
            await user.should_see(kind=ui.button, content=t("deployment_review_open"), retries=20)
            user.find(kind=ui.button, content=t("deployment_review_open")).click()
            await user.should_see(marker="deployment-review-source", retries=20)
            assert not any(
                isinstance(element, ui.dialog) and element.value
                for element in user.client.elements.values()
            )
            picker = next(
                iter(user.find(kind=ui.select, marker="deployment-review-target").elements)
            )
            assert list(picker.options)[:2] == [inventory[cam489], inventory[cam559]]
            assert picker.value == inventory[cam489]
            left = await player_for(user, "source", streamed_clips, left_first)
            await player_for(user, "target", streamed_clips, target_first)
            path_comparison = user.find(marker="deployment-review-path-comparison")
            rendered = next(iter(path_comparison.elements))
            parts = [item.content for item in rendered.descendants() if isinstance(item, ui.html)]
            assert parts[:4] == [
                '<span class="text-positive">DS_M1Phase5_E3</span>',
                '<span class="text-positive">DS_M1Phase5_E3</span>',
                '<span class="text-positive">T44</span>',
                '<span class="text-positive">T44</span>',
            ]
            assert "underline" in parts[4] and "448" in parts[4]
            assert "underline" in parts[5] and "489" in parts[5]
            comparison = next(
                iter(user.find(kind=ui.select, marker="deployment-review-target-clip").elements)
            )
            assert list(comparison.options.values()) == [target_first.name, target_second.name]
            comparison.set_value(next(reversed(comparison.options)))
            await player_for(user, "target", streamed_clips, target_second)
            assert rendered.is_deleted
            rendered = next(iter(user.find(marker="deployment-review-path-comparison").elements))
            parts = [item.content for item in rendered.descendants() if isinstance(item, ui.html)]
            assert parts[-2:] == [
                "—",
                '<span class="text-warning font-semibold underline">videos</span>',
            ]
            assert not left.is_deleted
            assert picker.value == inventory[cam489]
            assert dp.get_deployment_summary(project.id)["unmatched_references"] == 3
            picker.set_value(inventory[cam559])
            right = await player_for(user, "target", streamed_clips, target_alternate)
            assert comparison.is_deleted
            assert not left.is_deleted
            source = next(
                iter(user.find(kind=ui.select, marker="deployment-review-source").elements)
            )
            source.set_value(
                next(
                    identifier
                    for identifier, label in source.options.items()
                    if label == left_second.name
                )
            )
            await player_for(user, "source", streamed_clips, left_second)
            assert not right.is_deleted
            picker.set_value(inventory[ref448])
            await user.should_see(t("deployment_review_no_change"), retries=20)
            await user.should_see(t("deployment_review_no_comparison_clip"), retries=20)
            await user.should_not_see(marker="deployment-review-path-comparison")
            apply = user.find(kind=ui.button, content=t("deployment_review_apply_next"))
            assert not next(iter(apply.elements)).enabled

            user.find(kind=ui.button, content=t("deployment_review_skip")).click()
            await user.should_see(t("deployment_review_position", current=2, total=2), retries=20)
            await player_for(user, "source", streamed_clips, second_folder)
            await player_for(user, "target", streamed_clips, target_alternate)
            await user.should_see(
                t("deployment_preview_counts", changed=1, unchanged=0, blocked=0), retries=20
            )
            apply.click()
            await user.should_see(
                t("deployment_review_complete", applied=1, skipped=1), retries=20
            )
            assert dp.get_deployment_summary(project.id)["unmatched_references"] == 2
    finally:
        state.reset_app_state(keep_prefs=False)


async def test_reference_review_warns_when_matching_footage_is_likely_missing(
    provider_with_project, streamed_clips
):
    dp, project, directory = provider_with_project
    folder = "DS_M1Phase5_E2/T136/136_cam296_0185142_1107244_20260208_VR"
    clip = Path(directory.path) / folder / "DSCF0002.mp4"
    clip.parent.mkdir(parents=True)
    clip.touch()
    dp.sync_videos(active_project_id=project.id)
    reference_deployment = next(
        row["id"]
        for row in dp.get_project_deployments(project.id)
        if row["deployment_id"] == folder
    )
    state.set_data_provider(dp)
    state.set_annotator_name("Alice")
    state.set_active_project(project.id)
    gui = GUI()
    try:
        async with user_simulation() as user:

            @ui.page("/deployments", reconnect_timeout=3)
            async def deployments(review: str = "", deployment: str = ""):
                await gui.deployments_page(review=review, deployment=deployment)

            @ui.page("/review", reconnect_timeout=3)
            async def review_queue():
                assert state.get_filters()["selected_deployment"] == reference_deployment
                ui.label("Deployment review queue opened")

            await user.open("/deployments?review=references")
            await user.should_see(t("deployment_review_likely_missing_ordinary"), retries=20)
            details = user.find(kind=ui.expansion, content=t("deployment_review_folder_details"))
            next(iter(details.elements)).open()
            user.find(kind=ui.button, content=t("deployment_add")).click()
            id_label = t("deployment_field_required", field=t("deployment_field_deployment_id"))
            await user.should_see(kind=ui.input, content=id_label)
            deployment_id = next(iter(user.find(kind=ui.input, content=id_label).elements))
            assert deployment_id.value == folder
            await user.should_see(
                kind=ui.input, content=t("deployment_field_optional", field=t("deployment_name"))
            )

            new_id = folder.removesuffix("_VR")
            deployment_id.set_value(new_id)
            user.find(kind=ui.button, content=t("deployment_save")).click()
            await user.should_not_see(kind=ui.button, content=t("deployment_save"), retries=20)
            assert any(
                row["deployment_id"] == new_id and row["name"] is None
                for row in dp.get_project_deployments(project.id)
            )
            await user.should_see(t("deployment_review_no_comparison_clip"), retries=20)
            user.find(kind=ui.button, content=t("deployment_review_mark_unavailable")).click()
            note_label = t("deployment_field_optional", field=t("deployment_field_notes"))
            await user.should_see(kind=ui.textarea, content=note_label)
            note = next(iter(user.find(kind=ui.textarea, content=note_label).elements))
            note.set_value("Camera may have been stolen")
            user.find(kind=ui.button, content=t("save")).click()
            await user.should_see(
                t("deployment_review_complete", applied=1, skipped=0), retries=20
            )
            summary = dp.get_deployment_summary(project.id)
            assert not summary["has_pending_reference_reviews"]
            assert not summary["has_pending_deployment_issues"]

            await user.open("/deployments")
            card_marker = f"deployment-card-{reference_deployment}"
            await user.should_see(marker="deployment-group-no_ordinary", retries=20)
            target_tab = user.find(kind=ui.tab, marker="deployment-group-no_ordinary")
            tab = next(iter(target_tab.elements))
            assert tab.props["label"] == f"{t('deployment_group_no_ordinary')} (1)"
            target_tab.click()
            await user.should_see(marker=card_marker, retries=20)
            await user.should_see(t("deployment_reference_count", count=1))
            await user.should_not_see(t("deployment_advanced_video_tools"))
            user.find(kind=ui.link, marker=card_marker).click()
            await user.should_see(t("deployment_catalog_folder_clips", count=1), retries=20)
            await user.should_see(folder)
            user.find(kind=ui.button, content=t("deployment_catalog_review_folder")).click()
            await user.should_see(t("deployment_review_ordinary_unavailable"), retries=20)
            await user.should_see("Camera may have been stolen")
            user.find(kind=ui.button, content=t("deployment_review_reopen")).click()
            await user.should_see(t("deployment_review_likely_missing_ordinary"), retries=20)
            assert dp.get_deployment_summary(project.id)["has_pending_reference_reviews"]
            user.find(kind=ui.button, content=t("deployment_manage")).click()
            await user.should_see(t("deployment_catalog_folder_clips", count=1), retries=20)
            user.find(kind=ui.button, content=t("deployment_edit")).click()
            name_label = t("deployment_field_optional", field=t("deployment_name"))
            await user.should_see(kind=ui.input, content=name_label)
            name = next(iter(user.find(kind=ui.input, content=name_label).elements))
            name.set_value("Cam 296")
            user.find(kind=ui.button, content=t("deployment_save")).click()
            await user.should_see(kind=ui.label, content="Cam 296", retries=20)
            user.find(kind=ui.button, content=t("deployment_open_review")).click()
            await user.should_see("Deployment review queue opened", retries=20)
    finally:
        state.reset_app_state(keep_prefs=False)


@pytest.mark.parametrize("conversion_mode", ["browser_error", "automatic", "manual"])
async def test_deployment_review_converts_clip_and_previews_before_apply(
    provider_with_project, tmp_path, monkeypatch, conversion_mode, streamed_clips
):
    dp, project, directory = provider_with_project
    root = Path(directory.path)
    source = root / "ref/cam_y/first.mp4"
    second = root / "ref/cam_z/second.mp4"
    for path in (source, second):
        path.parent.mkdir(parents=True)
        path.touch()
    dp.sync_videos(active_project_id=project.id)
    with dp.Session.begin() as session:
        video = session.query(Video).filter_by(video_path=str(source)).one()
        video.is_web_safe = conversion_mode == "browser_error"
        video_id = video.video_id
    target_id = next(
        row["id"]
        for row in dp.get_project_deployments(project.id)
        if row["deployment_id"] == "cam_x"
    )

    converted = tmp_path / "converted.mp4"
    converted.touch()
    attempts = []

    def convert(clip_id):
        attempts.append(clip_id)
        if conversion_mode == "browser_error" and len(attempts) == 1:
            return TranscodeResult(False, error="Test conversion error")
        return TranscodeResult(True, new_path=str(converted))

    monkeypatch.setattr(dp, "transcode_video", convert)
    monkeypatch.setattr(
        playback,
        "check_video_tools",
        lambda: VideoToolsStatus({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}),
    )
    apply = dp.apply_deployment_assignment
    confirmed = []

    def apply_after_stale_preview(preview):
        confirmed.append(preview)
        if len(confirmed) == 1:
            raise DeploymentInputError("deployment_error_stale")
        return apply(preview)

    monkeypatch.setattr(dp, "apply_deployment_assignment", apply_after_stale_preview)
    state.set_data_provider(dp)
    state.set_annotator_name("Alice")
    state.set_active_project(project.id)
    state.set_auto_transcode(conversion_mode != "manual")
    gui = GUI()
    try:
        async with user_simulation() as user:

            @ui.page("/deployments")
            async def deployments(review: str = ""):
                await gui.deployments_page(review=review)

            await user.open("/deployments?review=references")
            await user.should_see(marker="deployment-review-source", retries=20)
            await user.should_not_see(kind=ui.button, content=t("deployment_preview"))
            next(
                iter(
                    user.find(
                        kind=ui.expansion, content=t("deployment_review_select_clips")
                    ).elements
                )
            ).open()
            clips = user.find(kind=ui.table, marker="deployment-review-clips")
            row = next(iter(clips.elements)).rows[0]
            assert row["video_id"] == video_id
            clips.trigger("selection", {"added": True, "rows": [row]})

            if conversion_mode == "browser_error":
                player = await player_for(user, "source", streamed_clips, source)
                player.mark("source-playback-error")
                user.find(marker="source-playback-error").trigger("error", 4)
                await user.should_see(
                    t("transcode_failed", error="Test conversion error"),
                    retries=20,
                )
            elif conversion_mode == "manual":
                assert attempts == []
                await user.should_see(t("playback_conversion_needed"), retries=20)
            if conversion_mode != "automatic":
                user.find(kind=ui.button, content=t("transcode_for_playback")).click()
            await player_for(user, "source", streamed_clips, converted)
            assert attempts == [video_id] * (2 if conversion_mode == "browser_error" else 1)

            target = next(
                iter(user.find(kind=ui.select, marker="deployment-review-target").elements)
            )
            target.set_value(target_id)
            await asyncio.sleep(0)
            counts = t("deployment_preview_counts", changed=1, unchanged=0, blocked=0)
            await user.should_see(counts, retries=20)
            assert next(
                iter(
                    user.find(kind=ui.button, content=t("deployment_review_apply_clips")).elements
                )
            ).enabled
            assert confirmed == []

            clips.trigger("selection", {"added": False, "keys": [video_id]})
            await asyncio.sleep(0)
            apply_button = user.find(kind=ui.button, content=t("deployment_review_apply_next"))
            await user.should_see(counts, retries=20)
            apply_button.click()
            await user.should_see(t("deployment_review_preview_changed"), retries=20)
            assert dp.get_deployment_summary(project.id)["unmatched_references"] == 2
            await user.should_see(counts, retries=20)
            apply_button.click()
            await user.should_see(t("deployment_review_position", current=2, total=2), retries=20)
            assert dp.get_deployment_summary(project.id)["unmatched_references"] == 1
    finally:
        state.reset_app_state(keep_prefs=False)


async def test_overview_shows_reference_thumbnails_and_omits_empty_deployments(
    provider_with_project, tmp_path, monkeypatch
):
    dp, project, directory = provider_with_project
    reference = Path(directory.path) / "test_camera_VR" / "reference.mp4"
    reference.parent.mkdir()
    reference.touch()
    dp.sync_videos(active_project_id=project.id)
    empty_id = dp.save_deployment(project.id, {"deployment_id": "Former deployment"})
    samples = []

    def thumbnail(source, _destination):
        samples.append(source)
        return False

    monkeypatch.setattr("review_app.app.config.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "review_app.app.components.deployment_catalog.get_user_data_dir", lambda: tmp_path
    )
    monkeypatch.setattr(
        "review_app.app.components.deployment_thumbnail.generate_thumbnail", thumbnail
    )
    state.set_data_provider(dp)
    state.set_annotator_name("Alice")
    state.set_active_project(project.id)
    gui = GUI()
    try:
        async with user_simulation() as user:

            @ui.page("/overview", reconnect_timeout=3)
            async def overview():
                await gui.overview_page()

            @ui.page("/deployments", reconnect_timeout=3)
            async def deployments():
                await gui.deployments_page()

            await user.open("/overview")
            await user.should_see(t("deployment_reference_count", count=1), retries=20)
            await user.should_see(kind=ui.button, content=t("deployment_view"))
            await user.should_not_see("Former deployment")
            for _ in range(20):
                if reference in samples:
                    break
                await asyncio.sleep(0.05)
            assert reference in samples

            samples.clear()
            user.find(kind=ui.link, content=t("deployment_view_all")).click()
            await user.should_see(marker=f"deployment-card-{empty_id}", retries=20)
            await user.should_see(t("deployment_no_assigned_videos"))
            user.find(kind=ui.tab, marker="deployment-group-no_ordinary").click()
            await user.should_see(t("deployment_reference_count", count=1))
            for _ in range(20):
                if reference in samples:
                    break
                await asyncio.sleep(0.05)
            assert reference in samples
    finally:
        state.reset_app_state(keep_prefs=False)


async def test_overview_edits_deployment_and_opens_filtered_species_review(
    provider_with_project, monkeypatch
):
    dp, project, _ = provider_with_project
    (deployment,) = dp.get_project_deployments(project.id)
    monkeypatch.setattr(
        "review_app.app.components.deployment_thumbnail.generate_thumbnail", lambda *_: False
    )
    state.set_data_provider(dp)
    state.set_annotator_name("Alice")
    state.set_active_project(project.id)
    state.update_filters(selected_camera="another-camera")
    gui = GUI()
    try:
        async with user_simulation() as user:

            @ui.page("/overview", reconnect_timeout=3)
            async def overview():
                await gui.overview_page()

            @ui.page("/deployments", reconnect_timeout=3)
            async def deployments(deployment: str = ""):
                await gui.deployments_page(deployment=deployment)

            @ui.page("/review", reconnect_timeout=3)
            async def review():
                filters = state.get_filters()
                assert filters["selected_deployment"] == deployment["id"]
                assert filters["selected_camera"] == "All"
                ui.label("Filtered review opened")

            await user.open("/overview")
            await user.should_see(t("deployment_overview_title"), retries=30)
            await user.should_not_see(kind=ui.button, content=t("deployment_view"))
            await user.should_see(kind=ui.button, content=t("deployment_edit"), retries=30)
            user.find(kind=ui.button, content=t("deployment_edit")).click()
            name_label = t("deployment_field_optional", field=t("deployment_name"))
            await user.should_see(kind=ui.input, content=name_label)
            name = next(iter(user.find(kind=ui.input, content=name_label).elements))
            name.set_value("New deployment name")
            user.find(kind=ui.button, content=t("deployment_save")).click()
            await user.should_see(kind=ui.label, content="New deployment name", retries=20)
            assert dp.get_project_deployments(project.id)[0]["name"] == "New deployment name"
            user.find(kind=ui.button, content=t("deployment_open_review")).click()
            await user.should_see("Filtered review opened", retries=20)
            await user.open("/overview")
            await user.should_see(t("deployment_ordinary_count", count=2), retries=20)
            user.find(kind=ui.link, content=t("deployment_view_all")).click()
            await user.should_see(marker="deployment-group-no_reference", retries=20)
            await user.open("/overview")
            await user.should_see(kind=ui.label, content="New deployment name", retries=20)
            detail = next(
                link
                for link in user.client.elements.values()
                if isinstance(link, ui.link)
                and str(link.props["href"]).endswith(f"?deployment={deployment['id']}")
            )
            await user.open(detail.props["href"])
            await user.should_see(t("deployment_catalog_folder_clips", count=2), retries=20)
    finally:
        state.reset_app_state(keep_prefs=False)


async def test_distance_species_settings_drive_overview_progress(
    provider_with_project, monkeypatch
):
    dp, project, _ = provider_with_project
    video_id = dp.get_video_queue({}, active_project_id=project.id)[0]
    observations = [{"species": "deer"}, {"species": "fox"}]
    for number in range(1, 6):
        name = f"Species {number}"
        dp.add_custom_species(f"test {number}", name, name, "Animal", "Animal")
        observations.append({"species": f"test {number}"})
    dp.update_manual_review(video_id, observations, active_project_id=project.id)
    monkeypatch.setattr(
        "review_app.app.components.deployment_thumbnail.generate_thumbnail", lambda *_: False
    )
    state.set_data_provider(dp)
    state.set_annotator_name("Alice")
    state.set_active_project(project.id)
    gui = GUI()
    try:
        async with user_simulation() as user:

            @ui.page("/settings")
            def settings():
                render_species_section(dp, project.id, "en")

            @ui.page("/overview")
            async def overview():
                await gui.overview_page()

            @ui.page("/distance-review")
            def review():
                filters = state.get_distance_queue_state(project.id)
                assert filters["species_filter"] == next(
                    iter(dp.get_distance_target_species_options(project.id))
                )
                ui.label("Filtered Distance Review opened")

            @ui.page("/review")
            def species_review():
                assert state.get_filters()["selected_species"] == ["fox"]
                ui.label("Filtered Species Review opened")

            await user.open("/settings")
            selector = next(
                iter(user.find(kind=ui.select, content=t("distance_enabled_species")).elements)
            )
            selector.set_value(["deer"])
            # NiceGUI refreshes the requirement controls after the change handler returns.
            async with asyncio.timeout(3):
                while True:
                    ages = user.find(kind=ui.checkbox, content=t("distance_require_age"))
                    if len(ages.elements) == 1:
                        break
                    await asyncio.sleep(0.01)
            (age,) = ages.elements
            age.set_value(True)
            assert dp.get_distance_review_species(project.id) == {
                "deer": {"require_age": True, "require_sex": False}
            }

            await user.open("/overview")
            await user.should_see(t("review_overview_title"), retries=20)
            await user.should_see(t("review_overview_observations"))
            await user.should_not_see(t("behavior_dist_title"))
            await user.should_not_see(kind=ui.button, content="Species 5")
            user.find(kind=ui.button, content=t("review_overview_show_all")).click()
            await user.should_see(kind=ui.button, content="Species 5")
            user.find(kind=ui.button, content=t("review_overview_show_less")).click()
            await user.should_not_see(kind=ui.button, content="Species 5")
            status = t("distance_progress_counts", completed=0, total=1)
            assert len(user.find(kind=ui.button, content="Red Deer").elements) == 1
            user.find(kind=ui.button, content=status).click()
            await user.should_see("Filtered Distance Review opened", retries=20)
            await user.open("/overview")
            await user.should_see(kind=ui.button, content="Red Fox", retries=20)
            user.find(kind=ui.button, content="Red Fox").click()
            await user.should_see("Filtered Species Review opened", retries=20)
    finally:
        state.reset_app_state(keep_prefs=False)
