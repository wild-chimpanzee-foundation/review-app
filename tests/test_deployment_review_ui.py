"""Deployment review compares footage and previews changes on selection."""

import asyncio
from pathlib import Path

import pytest
from nicegui import app, ui
from nicegui.testing.user_simulation import user_simulation
from review_app.app import state
from review_app.app.components import playback
from review_app.app.entry_point import GUI
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
    target_alternate = root / cam559 / "a559-ordinary.mp4"
    for clip in (
        left_first,
        left_second,
        second_folder,
        target_first,
        target_alternate,
        root / cam489 / "ref/00-reference.mp4",
        root / cam489 / "z489.mp4",
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

            @ui.page("/deployments")
            async def deployments(review: str = ""):
                await gui.deployments_page(review=review)

            await user.open("/deployments")
            await user.should_see(kind=ui.button, content=t("deployment_review_open"), retries=20)
            user.find(kind=ui.button, content=t("deployment_review_open")).click()
            await user.should_see(t("deployment_review_reference_mismatch"), retries=20)
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
            assert dp.get_deployment_summary(project.id)["unmatched_references"] == 3
            picker.set_value(inventory[cam559])
            right = await player_for(user, "target", streamed_clips, target_alternate)
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
            apply = user.find(kind=ui.button, content=t("deployment_review_apply_next"))
            assert not next(iter(apply.elements)).enabled

            user.find(kind=ui.button, content=t("deployment_review_skip")).click()
            await user.should_see(t("deployment_review_position", current=2, total=2), retries=20)
            await player_for(user, "source", streamed_clips, second_folder)
            await player_for(user, "target", streamed_clips, target_alternate)
            await user.should_not_see(t("deployment_review_reference_mismatch"))
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
            await user.should_see(t("deployment_review_preview_help"), retries=20)
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
