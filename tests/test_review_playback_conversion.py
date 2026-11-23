"""Review screens start conversion and return to their own player."""

from pathlib import Path
from unittest.mock import Mock

import pytest
from nicegui import ui
from nicegui.testing.user_simulation import user_simulation
from review_app.app import state
from review_app.app.components import playback
from review_app.app.entry_point import GUI
from review_app.app.pages.distance_review import page as distance
from review_app.app.translations import t
from review_app.backend.db.models import Video
from review_app.backend.provider.video import ProbeResult
from review_app.backend.transcoding import (
    TranscodedVideoValidation,
    TranscodeResult,
    transcoded_cache_filename,
)
from review_app.backend.video_tools import VideoToolsStatus


async def test_species_conversion_reloads_the_player_from_the_shared_cache(
    provider_with_project, tmp_path, monkeypatch
):
    dp, project, _ = provider_with_project
    video_id = dp.get_video_queue({}, active_project_id=project.id)[0]
    path = tmp_path / transcoded_cache_filename(video_id)
    with dp.Session.begin() as session:
        session.get(Video, video_id).is_web_safe = False

    def transcode(selected_id):
        assert selected_id == video_id
        path.touch()
        with dp.Session.begin() as session:
            session.get(Video, selected_id).transcoded_path = str(path)
        return TranscodeResult(True, new_path=str(path))

    monkeypatch.setattr(dp, "transcode_video", transcode)
    monkeypatch.setattr(
        playback,
        "check_video_tools",
        lambda: VideoToolsStatus({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}),
    )
    monkeypatch.setattr(
        "review_app.backend.transcoding.validate_transcoded_video",
        lambda _path: TranscodedVideoValidation(True),
    )
    monkeypatch.setattr("review_app.app.update_checker.check_for_update", lambda: None)
    state.set_data_provider(dp)
    state.set_annotator_name("alice")
    state.set_active_project(project.id)
    state.set_auto_transcode(False)
    gui = GUI()

    try:
        async with user_simulation() as user:

            @ui.page("/review")
            async def review():
                await gui.review_page()

            await user.open(f"/review?v={video_id}")
            await user.should_see(t("transcode_for_playback"), retries=20)
            user.find(t("transcode_for_playback")).click()
            await user.should_see(ui.video, retries=20)
            player = next(iter(user.find(ui.video).elements))
            assert player.source == f"/transcoded/{path.name}"
    finally:
        state.reset_app_state(keep_prefs=False)


@pytest.mark.parametrize(
    ("automatic", "browser_error"), [(True, False), (False, False), (True, True)]
)
async def test_distance_animal_and_reference_use_project_playback_cache(
    distance_data, tmp_path, monkeypatch, automatic, browser_error
):
    dp, project = distance_data["dp"], distance_data["project"]
    root = Path(distance_data["directory"].path)
    source = root / "cam_x/ref/calibration.avi"
    source.parent.mkdir()
    source.touch()
    dp.sync_videos(active_project_id=project.id)
    animal_id = distance_data["videos"][0]
    with dp.Session.begin() as session:
        session.get(Video, animal_id).is_web_safe = False
        reference_id = session.query(Video.video_id).filter_by(video_path=str(source)).scalar()
    reference = next(
        row
        for row in dp.get_distance_reference_videos(animal_id)
        if row["source_video_id"] == reference_id
    )
    frame = dp.add_reference_frame(reference["id"], 3500, 5.0)
    calls = []

    def transcode(video_id):
        calls.append(video_id)
        path = tmp_path / transcoded_cache_filename(video_id)
        path.touch()
        with dp.Session.begin() as session:
            session.get(Video, video_id).transcoded_path = str(path)
        return TranscodeResult(True, new_path=str(path))

    monkeypatch.setattr(dp, "transcode_video", transcode)
    monkeypatch.setattr(
        playback,
        "check_video_tools",
        lambda: VideoToolsStatus({"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}),
    )
    monkeypatch.setattr(
        distance,
        "_probe_video",
        lambda path: ProbeResult(
            10, True, browser_error or path != source, None, None, None, None
        ),
    )
    monkeypatch.setattr(
        distance, "render_custom_video_player", lambda url, *_a, **_kw: ui.video(url)
    )
    for hook in (
        "seek_paused",
        "install_anchor_editor",
        "install_manual_pause_snap",
        "playback_shortcuts",
    ):
        monkeypatch.setattr(distance, hook, Mock())
    state.set_data_provider(dp)
    state.set_auto_transcode(automatic)
    holder = {}

    try:
        async with user_simulation() as user:

            @ui.page("/distance-conversion-test")
            def test_page():
                page = distance.DistanceReviewPage(dp, project.id, {}, [])
                page.queue = [
                    next(
                        row
                        for row in dp.get_distance_video_queue(project.id)
                        if row["video_id"] == animal_id
                    )
                ]
                page.references = [reference]
                page.reference_thumbnail_path = Mock(return_value=None)
                page.render_animal()
                page.render_references()
                ui.button("Open calibration clip", on_click=lambda: page.open_reference(reference))
                holder["page"] = page

            await user.open("/distance-conversion-test")
            if not automatic:
                await user.should_see(t("transcode_for_playback"), retries=20)
                user.find(t("transcode_for_playback")).click()
            await user.should_see(ui.video, retries=20)
            page = holder["page"]
            assert page.animal_player is not None
            assert page.video["transcoded_path"] == str(
                tmp_path / transcoded_cache_filename(animal_id)
            )
            assert calls == [animal_id]

            user.find("Open calibration clip").click()
            if browser_error:
                await user.should_see(t("distance_prepare_reference"), retries=20)
                assert calls == [animal_id]
                original = page.reference_player
                original.mark("original-reference-player")
                user.find(marker="original-reference-player").trigger("error", 4)
                await user.should_not_see(marker="original-reference-player", retries=20)
            if not automatic:
                await user.should_see(t("transcode_for_playback"), retries=20)
                user.find(t("transcode_for_playback")).click()
            await user.should_see(t("distance_prepare_reference"), retries=20)
            assert page.reference_player is not None
            assert page.reference["id"] == reference["id"]
            assert page.selected_frame_id == frame["id"]
            assert page.reference["transcoded_path"] == str(
                tmp_path / transcoded_cache_filename(reference_id)
            )
            assert calls == [animal_id, reference_id]
    finally:
        state.reset_app_state(keep_prefs=False)
