"""Save/advance orchestration: consecutive playback, corrections, and navigation guards."""

from contextlib import nullcontext
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage


def test_completion_discrepancies_check_all_reviewed_species():
    page = DistanceReviewPage.__new__(DistanceReviewPage)
    page.species_filter = "deer"
    page.individuals = [
        {"species_id": "deer"},
        {"species_id": "deer"},
        {"species_id": "fox"},
    ]
    page.reviewed_species_counts = [
        {"species_id": "deer", "count": 2},
        {"species_id": "fox", "count": 2},
        {"species_id": "bear", "count": 1},
    ]
    assert page.completion_discrepancies() == [
        {"species_id": "fox", "count": 2, "distance_count": 1},
        {"species_id": "bear", "count": 1, "distance_count": 0},
    ]


@pytest.mark.parametrize("fail", [False, True])
async def test_completion_advances_only_after_success(distance_page, monkeypatch, fail):
    page = distance_page
    page.queue = [{"video_id": "current"}, {"video_id": "next"}]
    page.index = 0
    page.dp.finish_distance_video = Mock(side_effect=ValueError("incomplete") if fail else None)
    page.dp.get_distance_video_queue = Mock(return_value=page.queue)
    page.load_video = AsyncMock()
    monkeypatch.setattr(
        page_module.run,
        "io_bound",
        AsyncMock(side_effect=lambda func, *args, **kwargs: func(*args, **kwargs)),
    )
    assert await page.complete_video() is (not fail)
    if fail:
        assert page.index == 0
        page.load_video.assert_not_awaited()
    else:
        assert page.queue[page.index]["video_id"] == "next"
        page.load_video.assert_awaited_once()


@pytest.mark.parametrize(
    "target,active,reviewed,expected",
    [
        ("fox", "deer", ["deer"], "fox"),
        (None, "fox", ["deer"], "fox"),
        (None, None, ["deer", "fox"], "deer"),
        (None, None, ["missing", "fox"], "fox"),
        ("missing", None, ["deer"], "deer"),
        (None, None, [], None),
    ],
)
def test_default_species_prefills_without_restricting_other_choices(
    target, active, reviewed, expected
):
    page = DistanceReviewPage.__new__(DistanceReviewPage)
    page.species_options = {"deer": "Red Deer", "fox": "Red Fox"}
    page.species_filter = target
    page.individual_id = "adult" if active else None
    page.individuals = [{"id": "adult", "species_id": active}] if active else []
    page.reviewed_species = reviewed
    assert page.default_species() == expected
    assert page.species_options == {"deer": "Red Deer", "fox": "Red Fox"}


@pytest.fixture
def distance_page(monkeypatch):
    page = DistanceReviewPage.__new__(DistanceReviewPage)
    page.client = nullcontext()
    page.busy = False
    page.individual_id = "adult"
    page.individuals = [{"id": "adult", "age": "Adult", "species_id": "deer"}]
    page.project_id = "test-project"
    page.species_filter = page.camera_filter = page.status_filter = None
    monkeypatch.setattr(page_module, "set_distance_queue_state", Mock())
    page.species_options = {"deer": "Red Deer", "fox": "Red Fox"}
    page.saved_species_options = {}
    page.samples = []
    page.offsets = [1000, 3000, 5000, 7000]
    page.sample_offset = 1000
    page.review = None
    page.animal_frame_rate = 25
    page.animal_frame_rates = {}
    page.queue, page.index = [{"video_id": "current", "duration_sec": 10}], 0
    page.selected_frame_id = "frame"
    page.frames = [{"id": "frame", "distance_m": 7.25, "offset_ms": 500}]
    page.reference_player = None
    page.reference = None
    page.references = []
    page.sample_values = {"behavior_ids": ["walking"], "carries": False, "infant_id": "_new"}
    page.animal_player = object()
    page.dp = SimpleNamespace(
        save_distance_sample=Mock(), get_distance_species_options=Mock(return_value={})
    )

    def save_sample(individual_id, offset_ms, **values):
        page.samples = [
            row
            for row in page.samples
            if (row["individual_id"], row["offset_ms"]) != (individual_id, offset_ms)
        ]
        page.samples.append(
            {
                "individual_id": individual_id,
                "offset_ms": offset_ms,
                "reference_video_id": page.reference["id"] if page.reference else None,
                "carried_by_individual_id": None,
                **values,
            }
        )

    page.dp.save_distance_sample.side_effect = save_sample
    page.reload_individuals = AsyncMock()
    page.restore_sample = AsyncMock()
    page.seek_sample = Mock()
    page.render_samples = SimpleNamespace(refresh=Mock())
    page.render_sample_actions = SimpleNamespace(refresh=Mock())
    page.render_video_actions = SimpleNamespace(refresh=Mock())
    page.render_individual_progress = SimpleNamespace(refresh=Mock())
    page.render_tracks = SimpleNamespace(refresh=Mock())
    page.render_sample_navigation = SimpleNamespace(refresh=Mock())
    page.render_workspace = SimpleNamespace(refresh=Mock())
    page.render_sidebar = SimpleNamespace(refresh=Mock())
    page.render_frames = SimpleNamespace(refresh=Mock())
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    monkeypatch.setattr(page_module, "capture_offset", AsyncMock(return_value=1000))
    monkeypatch.setattr(page_module, "play_to_sample", AsyncMock())
    monkeypatch.setattr(page_module, "anchor_mode", Mock())
    monkeypatch.setattr(page_module.ui, "notify", Mock())

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(page_module.run, "io_bound", io_bound)
    return page


@pytest.mark.parametrize("reason", [None, "individual absent", "midpoint not visible"])
async def test_save_plays_to_consecutive_even_camera_second(distance_page, reason):
    page = distance_page

    async def play(player, start, end):
        assert page.busy  # No second write while the animal is between samples.
        assert page.sample_offset == 1000

    page_module.play_to_sample.side_effect = play
    await page.commit_sample(reason)

    assert not page.busy
    page.dp.save_distance_sample.assert_called_once_with(
        "adult",
        1000,
        reference_frame_id="frame" if reason is None else None,
        skip_reason=reason,
        behavior_ids=["walking"],
        infant_id=None,
        create_infant=False,
        creation_offset_ms=1000,
        labeled_by="alice",
    )
    page_module.play_to_sample.assert_awaited_once_with(page.animal_player, 1000.01, 3000.01)
    page.restore_sample.assert_awaited_once_with(seek=False)
    page.seek_sample.assert_not_called()
    assert not hasattr(page, "sample_drafts")
    page_module.ui.notify.assert_called_once_with(
        page_module.t("distance_sample_saved"), type="positive"
    )


async def test_forward_advance_stops_at_next_scheduled_sample_even_if_reviewed(distance_page):
    page = distance_page
    page.samples = [{"individual_id": "adult", "offset_ms": 3000}]
    await page.commit_sample("individual absent")
    assert page.sample_offset == 3000
    page_module.play_to_sample.assert_awaited_once_with(page.animal_player, 1000.01, 3000.01)
    page.seek_sample.assert_not_called()


@pytest.mark.parametrize("reason", [None, "individual absent", "midpoint not visible"])
@pytest.mark.parametrize("offsets", [[1000, 3000, 5000, 7000], [7000]])
async def test_final_sample_toasts_end_without_switching_or_filling_gaps(
    distance_page, reason, offsets
):
    page = distance_page
    page.offsets = offsets
    page.sample_offset = 7000
    page_module.capture_offset.return_value = 7000
    page.individuals.append({"id": "other", "age": "Adult", "species_id": "deer"})
    page.samples = [{"individual_id": "adult", "offset_ms": 5000}]
    await page.commit_sample(reason)
    assert page.sample_offset == 7000
    assert page.individual_id == "adult"
    assert page.index == 0
    page.dp.save_distance_sample.assert_called_once()
    assert page.dp.save_distance_sample.call_args.args == ("adult", 7000)
    page_module.play_to_sample.assert_not_awaited()
    page.seek_sample.assert_called_once()
    page_module.ui.notify.assert_called_once_with(
        page_module.t("distance_end_of_video"), type="positive"
    )


async def test_failed_final_sample_does_not_toast_end_of_video(distance_page):
    page = distance_page
    page.sample_offset = 7000
    page_module.capture_offset.return_value = 7000
    page.dp.save_distance_sample.side_effect = ValueError("Save failed")

    await page.commit_sample()

    page_module.ui.notify.assert_called_once_with("Save failed", type="warning")
    assert page.sample_offset == 7000
    assert page.sample_values["behavior_ids"] == ["walking"]
    assert not page.busy


@pytest.mark.parametrize("actual_offset", [999, 999.9999, 1251, 2400])
async def test_save_restores_sample_after_manual_inspection_without_writing(
    distance_page, actual_offset
):
    page = distance_page
    page_module.capture_offset.return_value = actual_offset
    await page.commit_sample()
    page.dp.save_distance_sample.assert_not_called()
    page.seek_sample.assert_called_once()
    page_module.play_to_sample.assert_not_awaited()
    assert page.sample_values["behavior_ids"] == ["walking"]
    assert not page.busy


@pytest.mark.parametrize("actual_offset", [1001, 1040, 1080, 1250])
async def test_save_rejects_a_frame_after_the_scheduled_sample(distance_page, actual_offset):
    page = distance_page
    page_module.capture_offset.return_value = actual_offset
    await page.commit_sample()
    if actual_offset == 1001:
        assert page.dp.save_distance_sample.call_args.args == ("adult", 1000)
        page.seek_sample.assert_not_called()
    else:
        page.dp.save_distance_sample.assert_not_called()
        page.seek_sample.assert_called_once()


async def test_manual_pause_selects_and_restores_latest_scheduled_sample(
    distance_page, monkeypatch
):
    page = distance_page
    page.choose_sample = AsyncMock()
    page.sample_seek_offset = Mock(return_value=3000.01)
    seek = Mock()
    monkeypatch.setattr(page_module, "seek_paused", seek)

    await page.on_manual_pause(SimpleNamespace(args={"offset_ms": 4800}))

    page.choose_sample.assert_awaited_once_with(3000)
    seek.assert_called_once_with(page.animal_player, 3000.01)


async def test_manual_pause_does_not_snap_while_busy(distance_page, monkeypatch):
    page = distance_page
    page.busy = True
    page.choose_sample = AsyncMock()
    seek = Mock()
    monkeypatch.setattr(page_module, "seek_paused", seek)

    await page.on_manual_pause(SimpleNamespace(args={"offset_ms": 4800}))

    page.choose_sample.assert_not_awaited()
    seek.assert_not_called()


async def test_opening_sample_still_restores_the_first_frame(distance_page):
    page = distance_page
    page.offsets = [0, 2000]
    page.sample_offset = 0
    page_module.capture_offset.return_value = 40
    await page.commit_sample()
    page.dp.save_distance_sample.assert_not_called()
    page.seek_sample.assert_called_once()


async def test_even_start_requires_opening_still_before_advancing(distance_page):
    page = distance_page
    page.offsets = [0, 2000, 4000]
    page.sample_offset = 0
    page_module.capture_offset.return_value = 0
    await page.commit_sample()
    assert page.dp.save_distance_sample.call_args.args == ("adult", 0)
    page_module.play_to_sample.assert_awaited_once_with(page.animal_player, 0, 2000.01)
    assert page.sample_offset == 2000


@pytest.mark.parametrize("actual,accepted", [(1000, False), (1000.9999, False), (1001, True)])
async def test_save_uses_estimated_frame_but_preserves_even_sample_time(
    distance_page, actual, accepted
):
    page = distance_page
    page.animal_frame_rate = 30000 / 1001
    page_module.capture_offset.return_value = actual
    await page.commit_sample()
    if accepted:
        assert page.dp.save_distance_sample.call_args.args == ("adult", 1000)
        page_module.play_to_sample.assert_awaited_once_with(page.animal_player, 1001.01, 3003.01)
    else:
        page.dp.save_distance_sample.assert_not_called()
        page.seek_sample.assert_called_once()


def test_return_to_sample_seeks_exact_frame_after_forward_inspection(distance_page, monkeypatch):
    page = distance_page
    page.animal_frame_rate = 30000 / 1001
    seek = Mock()
    monkeypatch.setattr(page_module, "seek_paused", seek)

    DistanceReviewPage.seek_sample(page)

    seek.assert_called_once_with(page.animal_player, 1001.01)


@pytest.mark.parametrize(
    "rate,delta,expected",
    [
        (25, 0, 920.125),
        (25, 2000, 2920.125),
        (30000 / 1001, 2000, 2922.125),
        (None, 0, 920.125),
        (None, 2000, 2960.125),
    ],
)
def test_calibrated_sample_keeps_the_selected_frame_and_uses_its_phase(
    distance_page, rate, delta, expected
):
    page = distance_page
    page.review = {
        "clock_anchor_offset_ms": 920,
        "clock_anchor_playback_ms": 920.125,
        "opening_even_second": False,
    }
    page.animal_frame_rate = rate
    assert page.sample_frame_offset(920 + delta) == expected


def test_calibrated_even_opening_keeps_the_first_still_at_zero(distance_page):
    page = distance_page
    page.review = {
        "clock_anchor_offset_ms": 1080,
        "clock_anchor_playback_ms": 1080.125,
        "opening_even_second": True,
    }
    assert page.sample_frame_offset(0) == 0
    assert page.sample_seek_offset(0) == 0
    assert page.sample_frame_offset(1080) == 1080.125


async def test_only_frame_rate_metadata_is_read_once_for_each_displayed_file(
    distance_page, monkeypatch, tmp_path
):
    page = distance_page
    path = tmp_path / "animal.mp4"
    page.queue, page.index = [{"video_path": str(path)}], 0
    probe = Mock(return_value=30000 / 1001)
    monkeypatch.setattr(page_module, "_probe_frame_rate", probe)
    await page.load_sample_timing()
    await page.load_sample_timing()
    probe.assert_called_once_with(path, timeout_sec=2)
    assert page.sample_frame_offset(1000) == 1001
    converted = tmp_path / "converted.mp4"
    page.video["transcoded_path"] = str(converted)
    monkeypatch.setattr(
        page_module, "inspect_transcoded_cache", Mock(return_value=SimpleNamespace(current=True))
    )
    await page.load_sample_timing()
    assert probe.call_args.args == (converted,)
    assert probe.call_count == 2
    page.video.pop("transcoded_path")
    monkeypatch.setattr(
        page_module, "inspect_transcoded_cache", Mock(return_value=SimpleNamespace(current=False))
    )
    await page.load_sample_timing()
    assert probe.call_count == 2


async def test_busy_save_does_not_write_twice(distance_page):
    distance_page.busy = True
    await distance_page.commit_sample()
    distance_page.dp.save_distance_sample.assert_not_called()
    page_module.capture_offset.assert_not_awaited()


@pytest.mark.parametrize("delta,expected", [(1, 3000), (-1, 1000)])
async def test_left_right_navigates_scheduled_review_frames(distance_page, delta, expected):
    page = distance_page
    page.sample_offset = 1000 if delta > 0 else 3000
    await page.move_sample(delta)
    assert page.sample_offset == expected
    page.restore_sample.assert_awaited_once()
    page.dp.save_distance_sample.assert_not_called()


@pytest.mark.parametrize(
    "busy,offset,delta", [(True, 1000, 1), (False, 1000, -1), (False, 7000, 1)]
)
async def test_sample_arrows_respect_busy_state_and_boundaries(distance_page, busy, offset, delta):
    page = distance_page
    page.busy, page.sample_offset = busy, offset
    await page.move_sample(delta)
    assert page.sample_offset == offset
    page.restore_sample.assert_not_awaited()


def test_distance_arrows_use_numeric_order_and_skip_duplicate_distances(distance_page):
    page = distance_page
    page.frames = [
        {"id": "far", "distance_m": 12.0},
        {"id": "duplicate", "distance_m": 7.25},
        {"id": "close", "distance_m": 0.5},
        {"id": "frame", "distance_m": 7.25},
    ]
    page.preview_frame = Mock(
        side_effect=lambda frame: setattr(page, "selected_frame_id", frame["id"])
    )
    assert [frame["id"] for frame in page.distance_choices()] == [
        "close",
        "frame",
        "far",
        "midpoint not visible",
        "individual absent",
    ]
    page.move_distance(1)
    assert page.selected_frame_id == "far"
    page.move_distance(1)
    assert page.selected_skip_reason == "midpoint not visible"
    page.move_distance(1)
    assert page.selected_skip_reason == "individual absent"
    page.move_distance(1)
    assert page.preview_frame.call_count == 3
    page.move_distance(-1)
    assert page.selected_skip_reason == "midpoint not visible"
    page.move_distance(-1)
    assert page.selected_frame_id == "far"
    page.move_distance(-1)
    assert page.selected_frame_id == "duplicate"
    page.move_distance(-1)
    assert page.selected_frame_id == "close"
    page.move_distance(-1)
    assert page.preview_frame.call_count == 7
    page.busy = True
    page.move_distance(1)
    assert page.selected_frame_id == "close"


@pytest.mark.parametrize("reason", ["midpoint not visible", "individual absent"])
async def test_arrow_selected_skip_is_confirmed_and_plays_to_next_sample(distance_page, reason):
    page = distance_page
    page.selected_frame_id = reason
    await page.commit_sample()
    saved = page.dp.save_distance_sample.call_args.kwargs
    assert saved["skip_reason"] == reason
    assert saved["reference_frame_id"] is None
    assert saved["behavior_ids"] == ["walking"]
    assert saved["infant_id"] is None and not saved["create_infant"]
    assert page.samples[0]["skip_reason"] == reason
    page_module.play_to_sample.assert_awaited_once_with(page.animal_player, 1000.01, 3000.01)


async def test_returning_to_skipped_sample_restores_choice_without_reference(distance_page):
    page = distance_page
    page.animal_player = None
    page.frames = []
    page.selected_frame_id = "frame"
    selection = {
        "reference_frame_id": None,
        "reference_video_id": None,
        "skip_reason": "individual absent",
    }
    page.samples = [{"individual_id": "adult", "offset_ms": 1000, **selection}]
    await DistanceReviewPage.restore_sample(page, seek=False)
    assert page.selected_skip_reason == "individual absent"
    page.move_distance(-1)
    assert page.selected_skip_reason == "midpoint not visible"
    page.dp.save_distance_sample.assert_not_called()


async def test_unsaved_skip_choice_is_discarded_by_frame_navigation(distance_page):
    page = distance_page
    del page.restore_sample
    page.selected_frame_id = "midpoint not visible"
    await page.choose_sample(3000)
    assert page.selected_frame_id == "frame"
    page.selected_frame_id = "frame"
    await page.choose_sample(1000)
    assert page.selected_frame_id == "frame"
    page.dp.save_distance_sample.assert_not_called()


@pytest.mark.parametrize("saved", [False, True])
async def test_new_review_frames_keep_distance_saved_frames_restore_evidence(distance_page, saved):
    page = distance_page
    page.sample_offset = 3000
    page.reference = {"id": "reference"}
    page.frames.append({"id": "other", "distance_m": 12.0, "offset_ms": 700})
    if saved:
        page.samples = [
            {
                "individual_id": "adult",
                "offset_ms": 3000,
                "reference_frame_id": "other",
                "reference_video_id": "reference",
            }
        ]
    await DistanceReviewPage.restore_sample(page, seek=False)
    assert page.selected_frame_id == ("other" if saved else "frame")
    page.seek_sample.assert_not_called()


@pytest.mark.parametrize("choice", ["frame", "individual absent", "midpoint not visible"])
@pytest.mark.parametrize(
    "saved_choice", [None, "other", "individual absent", "midpoint not visible"]
)
async def test_auto_advance_restores_saved_choice_and_prefills_unannotated_frames(
    distance_page, choice, saved_choice
):
    page = distance_page
    del page.restore_sample  # Exercise restoration as part of the actual auto-advance path.
    page.selected_frame_id = choice
    page.frames.append({"id": "other", "distance_m": 12, "offset_ms": 700})
    page.samples = (
        [
            {
                "individual_id": "adult",
                "offset_ms": 3000,
                "reference_frame_id": "other" if saved_choice == "other" else None,
                "reference_video_id": None,
                "skip_reason": saved_choice if saved_choice != "other" else None,
            }
        ]
        if saved_choice
        else []
    )
    await page.commit_sample()
    expected = saved_choice or choice
    assert page.sample_offset == 3000 and page.selected_frame_id == expected
    page.dp.save_distance_sample.assert_called_once()  # Advancing does not save the next frame.
    await DistanceReviewPage.restore_sample(page, seek=False)
    assert page.selected_frame_id == expected


async def test_auto_advance_restores_saved_choice_after_discarding_unconfirmed_correction(
    distance_page,
):
    page = distance_page
    del page.restore_sample
    page.samples = [
        {
            "individual_id": "adult",
            "offset_ms": 3000,
            "reference_frame_id": "frame",
            "reference_video_id": None,
            "skip_reason": None,
        }
    ]
    await page.choose_sample(3000)
    page.selected_frame_id = "individual absent"
    await page.choose_sample(1000)
    await page.commit_sample()
    assert page.sample_offset == 3000
    assert page.selected_frame_id == "frame"
    assert page.sample["reference_frame_id"] == "frame"
    page.dp.save_distance_sample.assert_called_once()


@pytest.mark.parametrize("individual_id", ["adult", "other"])
@pytest.mark.parametrize("choice", ["other-frame", "individual absent", "midpoint not visible"])
async def test_arrow_browsing_does_not_pin_inherited_choices_on_auto_advance(
    distance_page, individual_id, choice
):
    page = distance_page
    del page.restore_sample
    page.individuals.append({"id": "other", "age": "Adult", "species_id": "deer"})
    page.frames.append({"id": "other-frame", "distance_m": 12, "offset_ms": 700})
    # Browsing every stop must not retain any per-frame choices.
    for animal in ("adult", "other"):
        page.individual_id = animal
        page.sample_offset = 1000
        page.selected_frame_id = "frame"
        for _ in range(3):
            await page.move_sample(1)
    page.dp.save_distance_sample.assert_not_called()
    assert page.samples == []

    page.individual_id = individual_id
    await page.choose_sample(1000)
    page.selected_frame_id = choice
    for offset in (1000, 3000, 5000):
        page_module.capture_offset.return_value = offset
        await page.commit_sample()
        assert page.sample_offset == offset + 2000
        assert page.selected_frame_id == choice
    assert page.dp.save_distance_sample.call_count == 3
    assert all(
        call.args[0] == individual_id for call in page.dp.save_distance_sample.call_args_list
    )


async def test_auto_advance_discards_unconfirmed_choice_on_previously_visited_frame(distance_page):
    page = distance_page
    del page.restore_sample
    await page.move_sample(1)
    page.selected_frame_id = "individual absent"
    await page.move_sample(-1)
    await page.commit_sample()
    assert page.sample_offset == 3000
    assert page.selected_frame_id == "frame"
    page.dp.save_distance_sample.assert_called_once()


async def test_unconfirmed_behavior_edit_is_discarded_on_navigation(distance_page):
    page = distance_page
    del page.restore_sample
    await page.move_sample(1)
    page.sample_values["behavior_ids"].append("resting")
    await page.move_sample(-1)
    page.selected_frame_id = "midpoint not visible"
    await page.commit_sample()
    assert page.sample_offset == 3000
    assert page.selected_skip_reason == "midpoint not visible"
    assert page.sample_values["behavior_ids"] == []


async def test_reference_cycle_wraps_and_respects_busy_state(distance_page):
    page = distance_page
    page.references = [{"id": "first"}, {"id": "second"}, {"id": "third"}]
    page.reference = page.references[-1]
    page.open_reference = AsyncMock()
    await page.cycle_reference()
    page.open_reference.assert_awaited_once_with(page.references[0])
    page.busy = True
    await page.cycle_reference()
    assert page.open_reference.await_count == 1


async def test_resume_selects_previous_saved_distance(distance_page):
    page = distance_page
    page.sample_offset = 3000
    page.reference = {"id": "reference"}
    page.frames.append({"id": "other", "distance_m": 12.0, "offset_ms": 700})
    page.selected_frame_id = None
    page.samples = [
        {
            "individual_id": "adult",
            "offset_ms": 1000,
            "reference_frame_id": "other",
            "reference_video_id": "reference",
        }
    ]
    await DistanceReviewPage.restore_sample(page, seek=False)
    assert page.selected_frame_id == "other"


def test_first_review_frame_preselects_smallest_distance(distance_page):
    page = distance_page
    page.selected_frame_id = None
    page.frames.append({"id": "close", "distance_m": 0.5, "offset_ms": 900})
    page.ensure_reference_selection()
    assert page.selected_frame_id == "close"


async def test_switching_frames_discards_unconfirmed_metadata(distance_page):
    page = distance_page
    del page.restore_sample
    page.individuals.append({"id": "infant", "age": "Infant", "species_id": "deer"})
    page.sample_values.update(carries=True, infant_id="infant")
    await page.choose_sample(3000)
    assert page.sample_values == {"behavior_ids": [], "carries": False, "infant_id": "_new"}
    page.sample_values["behavior_ids"].append("running")
    await page.choose_sample(1000)
    assert page.sample_values == {"behavior_ids": [], "carries": False, "infant_id": "_new"}
    await page.choose_sample(3000)
    assert page.sample_values["behavior_ids"] == []
    page.dp.save_distance_sample.assert_not_called()


def test_saved_frame_metadata_stays_individual_specific(
    distance_page,
):
    page = distance_page
    page.sample_offset = 3000
    page.samples = [
        {
            "individual_id": "adult",
            "offset_ms": 3000,
            "reference_frame_id": "frame",
            "behavior_ids": ["resting"],
            "carried_by_individual_id": None,
        }
    ]
    page.reset_sample_values()
    assert page.sample_values["behavior_ids"] == ["resting"] and not page.sample_values["carries"]
    page.individuals.append({"id": "other", "age": "Adult", "species_id": "deer"})
    page.individual_id = "other"
    page.reset_sample_values()
    assert page.sample_values["behavior_ids"] == []


async def test_saving_new_infant_preselects_created_identity_for_next_sample(
    distance_page, distance_data
):
    page, data = distance_page, distance_data
    del page.restore_sample
    page.dp = data["dp"]
    page.dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    page.queue = [{"video_id": data["videos"][0], "duration_sec": 10}]
    page.individual_id = data["individual"]["id"]
    page.individuals = page.dp.list_distance_individuals(data["videos"][0])
    page.frames = [data["frame"]]
    page.selected_frame_id = data["frame"]["id"]
    page.sample_values.update(carries=True, behavior_ids=[data["behavior_id"]])

    async def reload():
        page.individuals = page.dp.list_distance_individuals(data["videos"][0])
        await page.reload_review()

    page.reload_individuals.side_effect = reload
    await page.commit_sample()
    assert page.sample_values["carries"]
    assert page.sample_values["infant_id"] != "_new"
    assert page.sample_values["behavior_ids"] == [data["behavior_id"]]


async def test_failed_playback_does_not_seek_to_next_stop(distance_page):
    page = distance_page
    page_module.play_to_sample.side_effect = ValueError("distance_playback_interrupted")
    await page.commit_sample()
    assert page.dp.save_distance_sample.call_count == 1
    assert not page.busy
    assert page.sample_offset == 1000
    page.seek_sample.assert_not_called()
    page_module.ui.notify.assert_called_with(
        page_module.t("distance_playback_interrupted"), type="warning"
    )


@pytest.mark.parametrize("selected", ["adult", "other"])
async def test_first_seen_control_activates_its_own_individual_even_if_active(
    distance_page, selected
):
    page = distance_page
    page.individual_id = selected
    page.individuals = [
        {"id": "adult", "first_seen_offset_ms": 500},
        {"id": "other", "first_seen_offset_ms": 2500},
    ]

    async def activate(individual_id):
        assert page.busy
        page.individual_id = individual_id

    page.activate_individual = AsyncMock(side_effect=activate)
    await page.go_to_first_seen("adult")
    page.activate_individual.assert_awaited_once_with("adult")
    assert page.individual_id == "adult"
    assert not page.busy


async def test_first_seen_control_does_not_seek_another_identity_when_busy(
    distance_page, monkeypatch
):
    page = distance_page
    page.busy = True
    page.select_individual = AsyncMock()
    seek = Mock()
    monkeypatch.setattr(page_module, "seek_paused", seek)
    await page.go_to_first_seen("other")
    page.select_individual.assert_not_awaited()
    seek.assert_not_called()


async def test_reloading_cards_uses_saved_attributes_and_linked_species(
    distance_page, monkeypatch
):
    page = distance_page
    page.queue, page.index = [{"video_id": "current"}], 0
    page.individuals = [
        {"id": "adult", "species_id": "deer", "age": "Adult", "sex": "Female", "tag_ids": []},
        {"id": "infant", "species_id": "deer", "age": "Infant", "sex": "Unknown", "tag_ids": []},
    ]
    page.dp.list_distance_individuals = Mock(
        return_value=[
            {**page.individuals[0], "species_id": "fox", "tag_ids": ["injured"]},
            {**page.individuals[1], "species_id": "fox"},
        ]
    )
    page.reload_review = AsyncMock()
    page.render_individuals = SimpleNamespace(refresh=Mock())
    page.render_timing = SimpleNamespace(refresh=Mock())
    monkeypatch.setattr(page_module, "configure_anchor", Mock())
    await DistanceReviewPage.reload_individuals(page)
    assert page.individual_id == "adult"
    assert all(row["species_id"] == "fox" for row in page.individuals)
    assert page.individuals[0]["tag_ids"] == ["injured"]
    assert not hasattr(page, "individual_drafts")


@pytest.mark.parametrize("review", [None, {"completed_at": None}, {"completed_at": "today"}])
@pytest.mark.parametrize("delta", [-2, -1, 1, 2])
async def test_video_navigation_allows_any_review_status(distance_page, review, delta):
    page = distance_page
    page.queue = [{"video_id": str(index)} for index in range(5)]
    page.index = 2
    page.review = review
    page.load_video = AsyncMock()
    await page.move_video(delta)
    page_module.set_distance_queue_state.assert_called_once_with(
        "test-project",
        species_filter=None,
        camera_filter=None,
        status_filter=None,
        video_id=str(2 + delta),
    )
    assert page.index == 2 + delta
    page.load_video.assert_awaited_once()
    page_module.ui.notify.assert_not_called()


@pytest.mark.parametrize("verified", [False, True])
@pytest.mark.parametrize("selected_offset", [1000, 5000, 9000])
@pytest.mark.parametrize("frame_delta", [0, 40])
@pytest.mark.parametrize("create_infant", [False, True])
async def test_creating_individual_preserves_playback_and_skips_prior_samples(
    distance_page,
    distance_data,
    monkeypatch,
    verified,
    selected_offset,
    frame_delta,
    create_infant,
):
    page, data = distance_page, distance_data
    page.dp = data["dp"]
    page.project_id = data["project"].id
    page.queue = [{"video_id": value} for value in data["videos"]]
    page.index = 0
    page.individuals = page.dp.list_distance_individuals(data["videos"][0])
    page.individual_id = data["individual"]["id"]
    page.render_individuals = SimpleNamespace(refresh=Mock())
    page.render_timing = SimpleNamespace(refresh=Mock())
    del page.reload_individuals  # Exercise the actual reload/selection path.
    configure = Mock()
    seek = Mock()
    monkeypatch.setattr(page_module, "configure_anchor", configure)
    monkeypatch.setattr(page_module, "seek_paused", seek)
    if verified:
        page.dp.verify_distance_recording_time(
            data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
        )
        page.dp.save_distance_sample(
            page.individual_id, 1000, skip_reason="individual absent", labeled_by="alice"
        )
    page.sample_offset = selected_offset if verified else None
    first_seen_offset = selected_offset + frame_delta
    old_id = page.individual_id

    async def restore(*, seek=True):
        assert page.busy
        new_id = page.individual_id
        await page.select_individual(old_id)
        await page.choose_sample(3000)
        await page.move_video(1)
        assert page.individual_id == new_id and page.index == 0
        await DistanceReviewPage.restore_sample(page, seek=seek)

    page.restore_sample.side_effect = restore
    assert await page.create_individual(
        data["videos"][0],
        data["species_id"],
        first_seen_offset,
        {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        create_infant=create_infant,
    )
    assert not page.busy
    assert page.individual_id != old_id
    assert page.sample_values["carries"] == create_infant
    if create_infant:
        infant = next(
            row for row in page.individuals if row["id"] == page.sample_values["infant_id"]
        )
        assert infant["age"] == "Infant"
        assert infant["first_seen_offset_ms"] == first_seen_offset
    assert (
        page.dp.get_distance_review_state(data["videos"][0], "alice")["active_individual_id"]
        == page.individual_id
    )
    expected_next = min(selected_offset + (2000 if frame_delta else 0), 9000)
    assert page.sample_offset == (expected_next if verified else None)
    assert page.individual["first_seen_offset_ms"] == first_seen_offset
    page.restore_sample.assert_awaited_once_with(seek=False)
    assert configure.call_args.args[1]["id"] == page.individual_id
    seek.assert_not_called()
    page.seek_sample.assert_not_called()
    assert page.reviewed_offsets() == (
        {offset for offset in page.offsets if offset < first_seen_offset} if verified else set()
    )
    assert all(
        sample["status"] == "skipped" and sample["skip_reason"] == "individual absent"
        for sample in page.samples
        if sample["individual_id"] == page.individual_id
    )

    if verified:
        # Ordinary selection inspects first seen, independently of review gaps.
        new_id = page.individual_id
        page.restore_sample.side_effect = None
        await page.select_individual(old_id)
        assert page.sample_offset == 1000
        page.restore_sample.assert_awaited_with(seek=False)
        seek.assert_called_with(page.animal_player, 500)
        await page.select_individual(new_id)
        assert page.sample_offset == expected_next
        page.restore_sample.assert_awaited_with(seek=False)
        seek.assert_called_with(page.animal_player, first_seen_offset)


@pytest.mark.parametrize("busy,video_id", [(True, "current"), (False, "previous")])
async def test_creation_ignores_busy_or_stale_dialogs(distance_page, busy, video_id):
    page = distance_page
    page.queue, page.index = [{"video_id": "current"}], 0
    page.busy = busy
    page.dp.create_distance_individual = Mock()
    assert not await page.create_individual(
        video_id, "deer", 500, {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}
    )
    page.dp.create_distance_individual.assert_not_called()


async def test_failed_selection_restores_navigation_without_switching_identity(distance_page):
    page = distance_page
    page.queue, page.index = [{"video_id": "current"}], 0
    page.individuals.append({"id": "other"})
    page.dp.set_distance_active_individual = Mock(side_effect=ValueError("Selection failed"))
    with pytest.raises(ValueError, match="Selection failed"):
        await page.select_individual("other")
    assert not page.busy and page.individual_id == "adult"


async def test_marker_save_blocks_navigation_until_individuals_reload(distance_page):
    page = distance_page
    page.queue, page.index = [{"video_id": "current"}, {"video_id": "next"}], 0
    page.dp.set_distance_individual_anchor = Mock()

    async def reload():
        assert page.busy
        await page.move_video(1)
        await page.choose_sample(3000)
        assert page.index == 0 and page.sample_offset == 1000

    page.reload_individuals.side_effect = reload
    await page.save_anchor(
        SimpleNamespace(
            args={
                "individual_id": "adult",
                "offset_ms": 500,
                "anchor": {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
            }
        )
    )
    assert not page.busy
    page.dp.set_distance_individual_anchor.assert_called_once()


@pytest.mark.parametrize("fail", [False, True])
async def test_sample_restore_blocks_other_navigation_and_releases_guard(distance_page, fail):
    page = distance_page
    page.queue, page.index = [{"video_id": "current"}, {"video_id": "next"}], 0

    async def restore():
        assert page.busy
        await page.choose_sample(5000)
        await page.move_video(1)
        assert page.index == 0 and page.sample_offset == 3000
        if fail:
            raise ValueError("Reference unavailable")

    page.restore_sample.side_effect = restore
    if fail:
        with pytest.raises(ValueError, match="Reference unavailable"):
            await page.choose_sample(3000)
    else:
        await page.choose_sample(3000)
    assert not page.busy


@pytest.mark.parametrize("restoring", [False, True])
@pytest.mark.parametrize("fail", [False, True])
async def test_reference_loading_keeps_navigation_guard_until_complete(
    distance_page, restoring, fail
):
    page = distance_page
    page.queue, page.index = [{"video_id": "current"}, {"video_id": "next"}], 0
    page.busy = restoring

    async def load(reference, **kwargs):
        assert page.busy
        await page.choose_sample(5000)
        await page.move_video(1)
        assert page.index == 0 and page.sample_offset == 1000
        if fail:
            raise ValueError("Reference unavailable")

    page.load_reference = AsyncMock(side_effect=load)
    if fail:
        with pytest.raises(ValueError, match="Reference unavailable"):
            await page.open_reference({"id": "reference"}, restoring=restoring)
    else:
        await page.open_reference({"id": "reference"}, restoring=restoring)
    assert page.busy == restoring


async def test_advance_finishes_playback_before_restoring_another_reference(distance_page):
    page = distance_page
    events = []

    async def play(*args):
        assert page.sample_offset == 1000
        # UI refreshes during playback still refer to the frame being left.
        page.refresh_sample_ui()
        events.append("playback")

    async def restore(**kwargs):
        assert page.sample_offset == 3000
        assert not hasattr(page, "sample_drafts")
        events.append("reference-switch")

    page_module.play_to_sample.side_effect = play
    page.restore_sample.side_effect = restore
    await page.commit_sample()
    assert events == ["playback", "reference-switch"]


@pytest.mark.parametrize("restoring", [False, True])
@pytest.mark.parametrize("mounted", [False, True])
async def test_switching_reference_only_seeks_animal_for_manual_selection(
    distance_page, monkeypatch, tmp_path, restoring, mounted
):
    page = distance_page
    path = tmp_path / "reference.mp4"
    path.touch()
    reference = {"id": "new-reference", "video_path": str(path)}
    page.queue, page.index = [{"video_id": "current"}], 0
    page.references = [reference]
    page.dp.list_reference_frames = Mock(
        return_value=[
            {"id": "closer-frame", "distance_m": 1, "offset_ms": 300},
            {"id": "new-frame", "distance_m": 7.25, "offset_ms": 700},
        ]
    )
    page.render_references = SimpleNamespace(refresh=Mock())
    page.render_reference_controls = SimpleNamespace(refresh=Mock())
    page.media_url = Mock(return_value="/reference.mp4")
    original_player = object() if mounted else None
    page.reference_player = original_player
    switch = AsyncMock(return_value=True)
    monkeypatch.setattr(page_module, "switch_source", switch)
    monkeypatch.setattr(
        page_module,
        "_probe_video",
        Mock(return_value=SimpleNamespace(is_valid=True, is_web_safe=True, duration_sec=10)),
    )
    await page.open_reference(reference, restoring=restoring)
    if restoring:
        page.seek_sample.assert_not_called()
    else:
        page.seek_sample.assert_called_once()
        assert page.selected_frame_id == "new-frame"
    assert page.reference == reference
    assert page.reference_player is original_player
    if mounted:
        switch.assert_awaited_once_with(
            original_player, "/reference.mp4", 300 if restoring else 700
        )
        page.render_references.refresh.assert_not_called()
        page.render_reference_controls.refresh.assert_called_once()
    else:
        switch.assert_not_awaited()
        page.render_references.refresh.assert_called_once()


@pytest.mark.parametrize("timeout", [False, True])
async def test_failed_reference_switch_invalidates_confirmation_and_allows_retry(
    distance_page, monkeypatch, tmp_path, timeout
):
    page = distance_page
    path = tmp_path / "reference.mp4"
    path.touch()
    reference = {"id": "new-reference", "video_path": str(path)}
    page.references = [reference]
    page.reference_player = object()
    page.render_references = SimpleNamespace(refresh=Mock())
    page.render_reference_controls = SimpleNamespace(refresh=Mock())
    page.media_url = Mock(return_value="/reference.mp4")
    frame = {"id": "new-frame", "distance_m": 4, "offset_ms": 333, "playback_offset_ms": 333.334}
    page.dp.list_reference_frames = Mock(return_value=[frame])
    monkeypatch.setattr(page_module, "stop_player", Mock())
    monkeypatch.setattr(
        page_module,
        "_probe_video",
        Mock(return_value=SimpleNamespace(is_valid=True, is_web_safe=True, duration_sec=10)),
    )
    monkeypatch.setattr(
        page_module,
        "switch_source",
        AsyncMock(side_effect=TimeoutError() if timeout else None, return_value=False),
    )
    await page.open_reference(reference)
    assert not page.busy
    assert page.reference is None and page.reference_player is None
    assert page.selected_frame_id is None and page.frames == []
    await page.commit_sample()
    page.dp.save_distance_sample.assert_not_called()
    await page.open_reference(reference)
    assert page.reference == reference and page.frames == [frame]
    assert page.selected_frame_id == "new-frame"


async def test_saved_reference_switch_loads_the_saved_frame_before_revealing_player(
    distance_page, monkeypatch, tmp_path
):
    page = distance_page
    del page.restore_sample
    path = tmp_path / "reference.mp4"
    path.touch()
    reference = {"id": "new-reference", "video_path": str(path)}
    page.queue, page.index = [{"video_id": "current"}], 0
    page.references = [reference]
    page.reference_player = object()
    page.media_url = Mock(return_value="/reference.mp4")
    page.render_reference_controls = SimpleNamespace(refresh=Mock())
    page.dp.list_reference_frames = Mock(
        return_value=[
            {"id": "closest", "distance_m": 1, "offset_ms": 300},
            {"id": "saved", "distance_m": 7.25, "offset_ms": 700, "playback_offset_ms": 700.123},
        ]
    )
    page.samples = [
        {
            "individual_id": "adult",
            "offset_ms": 1000,
            "reference_video_id": reference["id"],
            "reference_frame_id": "saved",
        }
    ]
    monkeypatch.setattr(
        page_module,
        "_probe_video",
        Mock(return_value=SimpleNamespace(is_valid=True, is_web_safe=True, duration_sec=10)),
    )
    switch = AsyncMock(return_value=True)
    monkeypatch.setattr(page_module, "switch_source", switch)
    monkeypatch.setattr(page_module, "seek_paused", Mock())
    await page.restore_sample(seek=False)
    switch.assert_awaited_once_with(page.reference_player, "/reference.mp4", 700.123)
    assert page.selected_frame_id == "saved"
    page.seek_sample.assert_not_called()


@pytest.mark.parametrize("present", [False, True])
async def test_reference_refresh_preserves_player_until_reference_is_removed(
    distance_page, monkeypatch, present
):
    page = distance_page
    page.queue, page.index = [{"video_id": "current"}], 0
    reference = {"id": "reference"}
    page.reference = reference
    original_player = page.reference_player = object()
    calls = Mock()
    page.dp.repair_distance_camera_layout = calls.repair
    page.dp.get_distance_reference_videos = calls.references
    calls.references.return_value = [reference] if present else []
    page.render_reference_controls = SimpleNamespace(refresh=Mock())
    page.render_references = SimpleNamespace(refresh=Mock())
    stop = Mock()
    monkeypatch.setattr(page_module, "stop_player", stop)
    await page.refresh_references()
    assert calls.method_calls == [
        ("repair", (page.project_id,), {}),
        ("references", ("current",), {}),
    ]
    if present:
        assert page.reference_player is original_player
        stop.assert_not_called()
        page.render_references.refresh.assert_not_called()
        page.render_reference_controls.refresh.assert_called_once()
    else:
        assert page.reference_player is None and page.reference is None
        assert page.frames == [] and page.selected_frame_id is None
        stop.assert_called_once_with(original_player)
        page.render_references.refresh.assert_called_once()
