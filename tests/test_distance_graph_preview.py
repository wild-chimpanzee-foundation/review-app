"""Unsaved distance/visibility choices update the graph, never annotation evidence."""

import asyncio
import copy
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.app.pages.distance_review.tracks import PREVIEW_COLOR, track_options
from review_app.backend.db.models import (
    DistanceIndividual,
    DistanceReviewProgress,
    DistanceSample,
    DistanceVideoReview,
)

pytest_plugins = ["nicegui.testing.user_plugin"]


def snapshot(dp):
    with dp.engine.connect() as conn:
        return {
            model.__tablename__: conn.execute(
                model.__table__.select().order_by(*model.__table__.primary_key.columns)
            ).all()
            for model in (
                DistanceIndividual,
                DistanceSample,
                DistanceReviewProgress,
                DistanceVideoReview,
            )
        }


@pytest.fixture
def preview_page(distance_data, monkeypatch):
    data, dp = distance_data, distance_data["dp"]
    video_id = data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    dp.save_distance_sample(
        data["individual"]["id"], 1000, reference_frame_id=data["frame"]["id"], labeled_by="alice"
    )
    frames = [data["frame"], dp.add_reference_frame(data["reference"]["id"], 4000, 9)]
    holder = {}
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    monkeypatch.setattr(page_module, "seek_paused", Mock())
    monkeypatch.setattr(page_module, "configure_anchor", Mock())
    monkeypatch.setattr(page_module, "anchor_mode", Mock())
    monkeypatch.setattr(page_module, "capture_offset", AsyncMock(return_value=9000))

    @ui.page("/distance-graph-preview-test")
    async def view():
        page = DistanceReviewPage(dp, data["project"].id, {data["species_id"]: "Deer"}, [])
        page.queue = [{"video_id": video_id, "camera_id": "cam_x", "duration_sec": 10}]
        page.individuals = dp.list_distance_individuals(video_id)
        page.individual_id = data["individual"]["id"]
        page.reference = data["reference"]
        page.references = [data["reference"]]
        page.frames = frames
        page.animal_frame_rate = 25
        await page.reload_review()
        page.sample_offset = 1000
        await page.restore_sample(seek=False)
        page.render_frames()
        page.render_tracks()
        holder["page"] = page

    return holder, data, frames


def chart(page):
    return next(
        element for element in page.client.elements.values() if isinstance(element, ui.echart)
    )


@pytest.mark.parametrize("choice", ["9 m", "Absent", "Not visible"])
async def test_preview_updates_chart_and_navigation_discards_it(user: User, preview_page, choice):
    holder, data, _ = preview_page
    await user.open("/distance-graph-preview-test")
    page = holder["page"]
    original_samples = copy.deepcopy(page.samples)
    before = snapshot(data["dp"])
    baseline = copy.deepcopy(chart(page).options)
    user.find(choice).click()
    await user.should_see("Unsaved preview")
    options = chart(page).options
    if choice == "9 m":
        point = options["series"][0]["data"][0]
        assert point["value"] == [1, 9]
        assert point["symbol"] == "diamond" and point["itemStyle"]["color"] == PREVIEW_COLOR
        assert options["yAxis"]["max"] >= 9
    else:
        assert options["series"][0]["data"][0] == [1, None]
        interval = options["series"][1]["data"][0]
        assert interval["value"][3:] == [int(choice == "Not visible"), 1]
        assert interval["previewLabel"] == "Unsaved preview"
    assert page.samples == original_samples
    assert snapshot(data["dp"]) == before

    await page.choose_sample(3000)
    assert page.graph_preview() is None
    await user.should_not_see("Unsaved preview")
    assert chart(page).options["series"][0]["data"] == baseline["series"][0]["data"]
    await page.choose_sample(1000)
    for _ in range(100):
        if chart(page).options == baseline:
            break
        await asyncio.sleep(0.01)
    assert chart(page).options == baseline
    assert snapshot(data["dp"]) == before


async def test_confirm_persists_preview_as_ordinary_evidence(user: User, preview_page):
    holder, data, _ = preview_page
    await user.open("/distance-graph-preview-test")
    page = holder["page"]
    await page.choose_sample(9000)
    page.animal_player = SimpleNamespace(id=123)
    user.find("9 m").click()
    await user.should_see("Unsaved preview")
    assert page.graph_preview()["distance_m"] == 9
    await page.commit_sample()
    assert page.sample_offset == 9000
    assert page.graph_preview() is None
    await user.should_not_see("Unsaved preview")
    assert chart(page).options["series"][0]["data"][-1] == [9, 9]
    saved = data["dp"].get_distance_review_state(data["videos"][0], "alice")["samples"]
    assert next(row for row in saved if row["offset_ms"] == 9000)["distance_m"] == 9


async def test_keyboard_preview_is_scoped_and_cannot_override_carried_evidence(
    user: User, preview_page
):
    holder, data, _ = preview_page
    await user.open("/distance-graph-preview-test")
    page = holder["page"]
    before = snapshot(data["dp"])
    page.move_distance(1)
    await user.should_see("Unsaved preview")
    assert page.graph_preview()["distance_m"] == 9
    page.individual_id = None
    assert page.graph_preview() is None
    page.individual_id = data["individual"]["id"]
    page.sample_offset = 3000
    assert page.graph_preview() is None
    page.sample_offset = 1000
    page.sample["carried_by_individual_id"] = "carrier"
    assert page.graph_preview() is None
    page.preview_frame({"id": "individual absent"})
    assert page.selected_frame_id != "individual absent"
    assert snapshot(data["dp"]) == before


@pytest.mark.parametrize("saved_reason", ["individual absent", "midpoint not visible"])
def test_distance_preview_replaces_status_without_mutating_saved_samples(saved_reason):
    individuals = [{"id": "one", "individual_number": 1}, {"id": "two", "individual_number": 2}]
    samples = [
        {"individual_id": "one", "offset_ms": 1000, "skip_reason": saved_reason},
        {"individual_id": "two", "offset_ms": 1000, "distance_m": 3},
    ]
    before = copy.deepcopy(samples)
    preview = {"individual_id": "one", "offset_ms": 1000, "distance_m": 12}
    options = track_options(individuals, samples, [1000], 2, "one", preview=preview)
    assert options["series"][0]["data"][0]["value"] == [1, 12]
    assert options["series"][1]["data"] == []
    assert options["series"][3]["data"] == [[1, 3]]
    assert samples == before
    restored = track_options(individuals, samples, [1000], 2, "one")
    assert restored["series"][1]["data"][0]["statusLabel"] in ("Absent", "Not visible")
