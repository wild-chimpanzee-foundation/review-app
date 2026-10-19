"""Reference visibility choices affect presentation, not the reference player lifecycle."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import SKIP_CHOICES, DistanceReviewPage

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def reference_page(monkeypatch, tmp_path, request):
    holder = {}
    frames = [{"id": "frame", "distance_m": 5, "offset_ms": 400}]
    references = []
    for name in ("first", "second"):
        path = tmp_path / f"{name}.mp4"
        path.touch()
        references.append({"id": name, "video_path": str(path), "is_tagged": True})
    dp = SimpleNamespace(
        list_reference_frames=Mock(return_value=frames), save_distance_sample=Mock()
    )
    saved_choice = getattr(request, "param", None)

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(page_module.run, "io_bound", io_bound)
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    monkeypatch.setattr(
        page_module,
        "_probe_video",
        Mock(return_value=SimpleNamespace(is_valid=True, is_web_safe=True, duration_sec=10)),
    )
    monkeypatch.setattr(page_module, "seek_paused", Mock())
    monkeypatch.setattr(page_module, "stop_player", Mock())
    monkeypatch.setattr(page_module, "capture_offset", AsyncMock(return_value=1000))
    monkeypatch.setattr(page_module, "anchor_mode", Mock())
    monkeypatch.setattr(page_module, "play_to_sample", AsyncMock())

    async def switch(player, source, offset):
        player.set_source(source)
        return True

    monkeypatch.setattr(page_module, "switch_source", AsyncMock(side_effect=switch))
    render = Mock(wraps=page_module.render_custom_video_player)
    monkeypatch.setattr(page_module, "render_custom_video_player", render)

    @ui.page("/distance-reference-dimming-test")
    async def reference_view():
        ui.add_css((page_module.Path(page_module.__file__).with_name("styles.css")).read_text())
        page = DistanceReviewPage(dp, "project", {"deer": "Deer"}, [])
        page.queue = [{"video_id": "animal", "camera_id": "camera", "duration_sec": 10}]
        page.individuals = [{"id": "adult", "age": "Adult", "species_id": "deer"}]
        page.individual_id = "adult"
        page.review = {"recorded_start": datetime(2026, 10, 12, 16, 2, 49)}
        page.animal_frame_rate = 25
        page.offsets = [1000, 3000, 5000]
        page.sample_offset = 1000
        page.references = references
        page.reference = references[0]
        page.reference_duration = 10
        page.frames = frames
        page.selected_frame_id = "frame"
        page.render_samples = Mock()
        page.render_sample_actions = Mock()
        page.render_tracks = SimpleNamespace(refresh=Mock())
        page.render_sample_navigation = SimpleNamespace(refresh=Mock())
        page.reference_thumbnail_path = Mock(return_value=None)
        page.load_reference_thumbnail = AsyncMock()
        page.reload_individuals = AsyncMock()

        def save_sample(individual_id, offset_ms, **values):
            page.samples = [row for row in page.samples if row["offset_ms"] != offset_ms]
            choice = values.get("skip_reason") or values["reference_frame_id"]
            page.samples.append(sample(offset_ms, choice, reference=page.reference["id"]))

        dp.save_distance_sample.side_effect = save_sample
        if saved_choice:
            # Visibility rows need not retain a reference source; load_video must
            # preserve their status while falling back to the available clip.
            dp.get_distance_reviewed_species = Mock(return_value=["deer"])
            dp.get_distance_reviewed_species_counts = Mock(
                return_value=[{"species_id": "deer", "count": 1}]
            )
            dp.list_distance_individuals = Mock(return_value=list(page.individuals))
            dp.get_distance_reference_videos = Mock(return_value=references)
            page.load_saved_species_options = AsyncMock()

            async def reload_saved_review(*, resume=False):
                page.sample_offset = 1000
                page.offsets = [1000, 3000, 5000]
                page.samples = [sample(1000, saved_choice, reference=None)]

            page.reload_review = AsyncMock(side_effect=reload_saved_review)
            await page.load_video()
        page.render_references()
        holder.update(page=page, render=render, frames=frames, references=references)

    return holder


async def assert_dimmed(page, expected):
    for _ in range(100):
        if ("distance-reference-dimmed" in page.reference_player.classes) == expected:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"Reference dimming did not become {expected}")


def sample(offset, choice="frame", reference="first"):
    skipped = choice in {row["id"] for row in SKIP_CHOICES}
    return {
        "individual_id": "adult",
        "offset_ms": offset,
        "reference_video_id": reference,
        "reference_frame_id": None if skipped else choice,
        "skip_reason": choice if skipped else None,
        "behavior_ids": [],
        "carried_by_individual_id": None,
    }


@pytest.mark.parametrize("choice", SKIP_CHOICES, ids=lambda choice: choice["id"])
async def test_visibility_choice_dims_and_distance_restores_without_player_interference(
    user: User, reference_page, choice
):
    await user.open("/distance-reference-dimming-test")
    page = reference_page["page"]
    player = page.reference_player
    # Stand in for the filter values maintained by the client-side image controls.
    player.style("filter:brightness(1.4) contrast(0.7)")
    original_style = dict(player.style)
    page_module.seek_paused.reset_mock()

    user.find(page_module.t(choice["label"])).click()
    await assert_dimmed(page, True)
    assert page.selected_skip_reason == choice["skip_reason"]
    page_module.seek_paused.assert_not_called()
    page_module.switch_source.assert_not_awaited()
    page_module.stop_player.assert_not_called()
    page_module.play_to_sample.assert_not_awaited()
    assert dict(player.style) == original_style

    user.find("5 m").click()
    await assert_dimmed(page, False)
    assert page.selected_frame_id == "frame"
    assert page.reference_player is player
    reference_page["render"].assert_called_once()
    page_module.seek_paused.assert_called_once_with(player, 400)
    assert dict(player.style) == original_style
    # The CSS composes on the media container, not the video or its controls.
    css = (page_module.Path(page_module.__file__).with_name("styles.css")).read_text()
    assert ".vp-video-container:has(> video.distance-reference-dimmed)" in css
    assert "filter:grayscale(1) brightness(.45)" in css
    assert "pointer-events" not in css


@pytest.mark.parametrize(
    "reference_page", ["individual absent", "midpoint not visible"], indirect=True
)
async def test_initial_saved_visibility_choice_renders_dimmed(user: User, reference_page):
    await user.open("/distance-reference-dimming-test")
    page = reference_page["page"]
    await assert_dimmed(page, True)
    assert page.selected_skip_reason == page.samples[0]["skip_reason"]
    assert page.reference["id"] == "first"
    page.reload_review.assert_awaited_once_with(resume=True)
    reference_page["render"].assert_called_once()


@pytest.mark.parametrize("choice", ["individual absent", "midpoint not visible"])
async def test_saved_samples_restore_reference_dimming_and_discard_unconfirmed_edits(
    user: User, reference_page, choice
):
    await user.open("/distance-reference-dimming-test")
    page = reference_page["page"]
    player = page.reference_player
    page.samples = [sample(1000), sample(3000, choice, reference="second")]
    await page.choose_sample(3000)
    await assert_dimmed(page, True)
    assert page.reference["id"] == "second"
    assert page.selected_skip_reason == choice

    with page.client:
        page.preview_frame(reference_page["frames"][0])
    await assert_dimmed(page, False)
    await page.choose_sample(1000)
    await assert_dimmed(page, False)
    await page.choose_sample(3000)
    await assert_dimmed(page, True)
    assert page.selected_skip_reason == choice  # Saved visibility wins over unconfirmed edits.

    with page.client:
        page.preview_frame(next(row for row in SKIP_CHOICES if row["id"] == choice))
    await assert_dimmed(page, True)
    await page.choose_sample(1000)
    await assert_dimmed(page, False)
    await page.choose_sample(3000)
    await assert_dimmed(page, True)
    assert page.selected_skip_reason == choice
    assert page.reference_player is player
    reference_page["render"].assert_called_once()
    page_module.stop_player.assert_not_called()


@pytest.mark.parametrize("choice", SKIP_CHOICES, ids=lambda choice: choice["id"])
async def test_reference_browsing_retains_visibility_and_dim_without_recreation(
    user: User, reference_page, choice
):
    await user.open("/distance-reference-dimming-test")
    page = reference_page["page"]
    player = page.reference_player
    user.find(page_module.t(choice["label"])).click()
    await assert_dimmed(page, True)
    await page.cycle_reference()
    await assert_dimmed(page, True)
    assert page.reference["id"] == "second"
    assert page.selected_skip_reason == choice["skip_reason"]
    await page.open_reference(reference_page["references"][0])
    await assert_dimmed(page, True)
    assert page.reference["id"] == "first"
    assert page.selected_skip_reason == choice["skip_reason"]
    assert page.reference_player is player
    reference_page["render"].assert_called_once()
    assert page_module.switch_source.await_count == 2
    page_module.stop_player.assert_not_called()
    page_module.play_to_sample.assert_not_awaited()


@pytest.mark.parametrize("choice", SKIP_CHOICES, ids=lambda choice: choice["id"])
@pytest.mark.parametrize("saved_next_distance", [False, True])
async def test_auto_advance_restores_or_carries_visibility_after_playback(
    user: User, reference_page, choice, saved_next_distance
):
    await user.open("/distance-reference-dimming-test")
    page = reference_page["page"]
    player = page.reference_player
    user.find(page_module.t(choice["label"])).click()
    await assert_dimmed(page, True)
    if saved_next_distance:
        page.samples = [sample(3000)]

    async def play(animal, start, end):
        assert page.sample_offset == 1000
        assert "distance-reference-dimmed" in player.classes
        reference_page["render"].assert_called_once()

    page_module.play_to_sample.side_effect = play
    await page.commit_sample()
    await assert_dimmed(page, not saved_next_distance)
    assert page.sample_offset == 3000
    assert page.selected_frame_id == ("frame" if saved_next_distance else choice["id"])
    page_module.play_to_sample.assert_awaited_once()
    page_module.switch_source.assert_not_awaited()
    page_module.stop_player.assert_not_called()
    assert page.reference_player is player
    reference_page["render"].assert_called_once()


async def test_empty_reference_refresh_and_first_player_keep_header_actions_live(
    user: User, reference_page
):
    await user.open("/distance-reference-dimming-test")
    page = reference_page["page"]
    page.references = []
    page.reference = page.reference_player = None
    page.frames = []
    page.dp.repair_distance_camera_layout = Mock()
    page.dp.get_distance_reference_videos = Mock(return_value=reference_page["references"])
    page.render_references.refresh()
    refresh = next(
        element
        for element in user.find(ui.button).elements
        if element.props.get("icon") == "refresh"
    )
    assert refresh.enabled  # Discover newly tagged clips even when none were loaded.
    refresh.mark("reference-refresh")
    user.find(marker="reference-refresh").click()
    for _ in range(100):
        if page.dp.repair_distance_camera_layout.called and not page.busy:
            break
        await asyncio.sleep(0.01)
    page.dp.repair_distance_camera_layout.assert_called_once_with("project")
    await user.should_see("first.mp4")
    await page.open_reference(reference_page["references"][0])
    await user.should_see("5 m")
    refresh = next(
        element
        for element in user.find(ui.button).elements
        if element.props.get("icon") == "refresh"
    )
    prepare = next(
        element
        for element in user.find(ui.button).elements
        if element.props.get("icon") == "bookmark_add"
    )
    for _ in range(100):
        if prepare.enabled and refresh.enabled and not page.busy:
            break
        await asyncio.sleep(0.01)
    assert prepare.enabled and refresh.enabled and not page.busy
