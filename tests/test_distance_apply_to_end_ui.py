"""Generalized bulk assignment confirmation, overwrite, and navigation guards."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nicegui import ui
from nicegui.testing import User
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def bulk_page(distance_data, monkeypatch):
    data, dp = distance_data, distance_data["dp"]
    video_id, individual_id = data["videos"][0], data["individual"]["id"]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    dp.save_distance_sample(
        individual_id, 1000, skip_reason="individual absent", labeled_by="alice"
    )
    dp.save_distance_sample(
        individual_id, 5000, reference_frame_id=data["frame"]["id"], labeled_by="alice"
    )
    dp.save_distance_sample(
        individual_id, 7000, skip_reason="midpoint not visible", labeled_by="alice"
    )
    frame = dp.add_reference_frame(data["reference"]["id"], 4000, 7.25)
    data["bulk_frame"] = frame
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    monkeypatch.setattr(page_module, "seek_paused", Mock())
    holder = {}

    @ui.page("/distance-apply-to-end-test")
    async def setup():
        page = DistanceReviewPage(
            dp, data["project"].id, dp.get_distance_species_options(data["project"].id), []
        )
        page.queue = [{"video_id": video_id, "camera_id": "cam_x", "duration_sec": 10}]
        page.individuals = dp.list_distance_individuals(video_id)
        page.individual_id = individual_id
        await page.reload_review()
        page.sample_offset = 3000
        page.frames = [data["frame"], frame]
        page.selected_frame_id = frame["id"]
        page.animal_player = SimpleNamespace(id=999)
        page.behavior_options = dp.get_distance_behavior_options()
        page.sample_drafts[(individual_id, 3000)] = {
            "behavior_ids": [data["behavior_id"]],
            "carries": False,
            "infant_id": "_new",
        }
        page.sample_drafts[(individual_id, 5000)] = {"stale": True}
        page.sample_drafts[(individual_id, 1000)] = {"earlier": True}
        page.render_sample_actions()
        holder["page"] = page

    return holder, data


async def wait_until(predicate):
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Apply-to-end operation did not finish")


def action_button(page):
    return next(
        element
        for element in page.client.elements.values()
        if element.props.get("data-distance-action") == "apply-to-end"
    )


@pytest.mark.parametrize("choice", ["distance", "individual absent", "midpoint not visible"])
async def test_confirmation_overwrites_existing_results_and_jumps_to_final_sample(
    user: User, bulk_page, choice
):
    holder, data = bulk_page
    await user.open("/distance-apply-to-end-test")
    page = holder["page"]
    if choice != "distance":
        page.selected_frame_id = choice
        page.render_sample_actions.refresh()
    before = list(page.samples)
    user.find("Apply to end").click()
    label = {
        "distance": "7.25 m",
        "individual absent": "Absent",
        "midpoint not visible": "Not visible",
    }[choice]
    await user.should_see(
        f"Apply {label} to 4 samples for individual #1, from 00:03.000 to the end?"
    )
    await user.should_see("This overwrites all existing annotations and skips", kind=ui.label)
    assert page.samples == before
    user.find("Apply and overwrite").click()
    await user.should_see("Applied selection to 4 samples.")
    await wait_until(lambda: not page.busy and len(page.samples) == 5)
    await user.should_see("Individual completed")
    await user.should_not_see("Absent to end")
    await user.should_not_see("Undo absent to end")
    assert page.review["completed_at"] is None
    assert page.sample_offset == 9000
    assert page.individual_id == data["individual"]["id"]
    assert page.selected_frame_id == (data["bulk_frame"]["id"] if choice == "distance" else choice)
    assert page.sample_drafts[(page.individual_id, 1000)] == {"earlier": True}
    assert (page.individual_id, 5000) not in page.sample_drafts
    assert page.current_sample_draft()["behavior_ids"] == [data["behavior_id"]]
    assert action_button(page).enabled  # Reviewed samples can always be overwritten again.
    page_module.seek_paused.assert_called_once_with(
        page.animal_player, page.sample_seek_offset(9000)
    )
    rows = data["dp"].get_distance_review_state(data["videos"][0], "alice")["samples"]
    assert next(row for row in rows if row["offset_ms"] == 1000) == before[0]
    for row in rows:
        if row["offset_ms"] < 3000:
            continue
        assert row["behavior_ids"] == [data["behavior_id"]]
        assert row["skip_reason"] == (None if choice == "distance" else choice)
        assert row["reference_frame_id"] == (
            data["bulk_frame"]["id"] if choice == "distance" else None
        )


async def test_cancelling_does_not_save_or_discard_drafts(user: User, bulk_page):
    holder, _ = bulk_page
    await user.open("/distance-apply-to-end-test")
    page = holder["page"]
    before, drafts = list(page.samples), dict(page.sample_drafts)
    user.find("Apply to end").click()
    await user.should_see("Apply and overwrite")
    dialog = next(
        element for element in user.client.elements.values() if isinstance(element, ui.dialog)
    )
    user.find("Cancel").click()
    await wait_until(lambda: not dialog.value)
    assert page.samples == before
    assert page.sample_drafts == drafts
    assert page.sample_offset == 3000
    page_module.seek_paused.assert_not_called()


@pytest.mark.parametrize(
    "changed", ["sample", "individual", "video", "busy", "choice", "behaviors", "infant"]
)
async def test_stale_dialog_cannot_save_changed_selection(user: User, bulk_page, changed):
    holder, data = bulk_page
    await user.open("/distance-apply-to-end-test")
    page = holder["page"]
    before = list(page.samples)
    user.find("Apply to end").click()
    await user.should_see("Apply and overwrite")
    if changed == "sample":
        page.sample_offset = 7000
    elif changed == "individual":
        page.individual_id = "another"
    elif changed == "video":
        page.queue = [{"video_id": data["videos"][1]}]
    elif changed == "busy":
        page.busy = True
    elif changed == "choice":
        page.selected_frame_id = "individual absent"
    elif changed == "behaviors":
        page.current_sample_draft()["behavior_ids"].clear()
    else:
        page.current_sample_draft()["carries"] = True
    user.find("Apply and overwrite").click()
    await asyncio.sleep(0.05)
    assert data["dp"].get_distance_review_state(data["videos"][0], "alice")["samples"] == before


@pytest.mark.parametrize("choice", [None, "missing-frame"])
async def test_missing_selection_disables_bulk_action(user: User, bulk_page, choice):
    holder, _ = bulk_page
    await user.open("/distance-apply-to-end-test")
    page = holder["page"]
    page.selected_frame_id = choice
    page.render_sample_actions.refresh()
    await wait_until(lambda: not action_button(page).enabled)
    await page.apply_to_end_dialog()
    await user.should_not_see("Apply and overwrite")


async def test_final_sample_can_be_overwritten_and_corrected(user: User, bulk_page):
    holder, data = bulk_page
    await user.open("/distance-apply-to-end-test")
    page = holder["page"]
    page.sample_offset = 9000
    page.selected_frame_id = "individual absent"
    page.render_sample_actions.refresh()
    assert action_button(page).enabled
    user.find("Apply to end").click()
    await user.should_see("Apply Absent to 1 samples")
    user.find("Apply and overwrite").click()
    await user.should_see("Applied selection to 1 samples.")
    await wait_until(lambda: not page.busy)
    assert page.sample_offset == 9000
    page_module.seek_paused.assert_called_once_with(
        page.animal_player, page.sample_seek_offset(9000)
    )
    data["dp"].save_distance_sample(
        page.individual_id, 9000, reference_frame_id=data["frame"]["id"], labeled_by="bob"
    )
    state = data["dp"].get_distance_review_state(data["videos"][0], "alice")
    assert (
        next(row for row in state["samples"] if row["offset_ms"] == 9000)["status"] == "annotated"
    )


@pytest.mark.parametrize("choice", ["distance", "individual absent", "midpoint not visible"])
@pytest.mark.parametrize("new_infant", [False, True])
async def test_carrying_bulk_choice_uses_one_infant_and_invalidates_affected_drafts(
    user: User, bulk_page, choice, new_infant
):
    holder, data = bulk_page
    await user.open("/distance-apply-to-end-test")
    page, dp = holder["page"], data["dp"]
    infant = dp.create_distance_individual(
        data["videos"][0],
        data["species_id"],
        0,
        age="Infant",
        sex="Unknown",
        anchor=data["anchor"],
    )
    page.individuals = dp.list_distance_individuals(data["videos"][0])
    page.sample_drafts[(infant["id"], 5000)] = {"infant-draft": True}
    page.sample_drafts[(infant["id"], 1000)] = {"keep": True}
    page.sample_defaults[infant["id"]] = {"stale": True}
    draft = page.current_sample_draft()
    draft.update(carries=True, infant_id="_new" if new_infant else infant["id"])
    if choice != "distance":
        page.selected_frame_id = choice
    user.find("Apply to end").click()
    await user.should_see("Apply and overwrite")
    user.find("Apply and overwrite").click()
    await user.should_see("Applied selection to 4 samples.")
    await wait_until(lambda: not page.busy)
    children = [
        row for row in page.samples if row["carried_by_individual_id"] == page.individual_id
    ]
    assert len(children) == 4
    infant_ids = {row["individual_id"] for row in children}
    assert len(infant_ids) == 1
    infant_id = infant_ids.pop()
    assert (infant_id != infant["id"]) == new_infant
    for row in children:
        assert row["behavior_ids"] == [data["behavior_id"]]
        assert row["skip_reason"] == (None if choice == "distance" else choice)
    assert page.current_sample_draft()["infant_id"] == infant_id
    assert page.current_sample_draft()["carries"]
    assert page.sample_drafts[(infant["id"], 1000)] == {"keep": True}
    if new_infant:
        assert page.sample_drafts[(infant["id"], 5000)] == {"infant-draft": True}
        assert infant["id"] in page.sample_defaults
    else:
        assert (infant["id"], 5000) not in page.sample_drafts
        assert infant["id"] not in page.sample_defaults


async def test_failed_write_preserves_drafts_position_and_dialog(
    user: User, bulk_page, monkeypatch
):
    holder, data = bulk_page
    await user.open("/distance-apply-to-end-test")
    page = holder["page"]
    before, drafts = list(page.samples), dict(page.sample_drafts)
    monkeypatch.setattr(
        data["dp"], "apply_distance_to_end", Mock(side_effect=ValueError("Cannot apply"))
    )
    user.find("Apply to end").click()
    await user.should_see("Apply and overwrite")
    user.find("Apply and overwrite").click()
    await user.should_see("Cannot apply")
    await wait_until(lambda: not page.busy)
    assert page.sample_offset == 3000
    assert page.samples == before
    assert page.sample_drafts == drafts
    page_module.seek_paused.assert_not_called()
    await user.should_see("Apply and overwrite")


async def test_carry_off_invalidates_only_formerly_linked_infant_drafts(user: User, bulk_page):
    holder, data = bulk_page
    await user.open("/distance-apply-to-end-test")
    page, dp = holder["page"], data["dp"]
    saved = dp.save_distance_sample(
        page.individual_id,
        5000,
        reference_frame_id=data["frame"]["id"],
        create_infant=True,
        labeled_by="alice",
    )
    infant_id = saved["infant_id"]
    dp.save_distance_sample(
        infant_id,
        7000,
        skip_reason="midpoint not visible",
        labeled_by="alice",
    )
    await page.reload_individuals()
    page.sample_drafts[(infant_id, 5000)] = {"linked": True}
    page.sample_drafts[(infant_id, 7000)] = {"independent": True}
    assert not page.current_sample_draft()["carries"]
    user.find("Apply to end").click()
    await user.should_see("Apply and overwrite")
    user.find("Apply and overwrite").click()
    await user.should_see("Applied selection to 4 samples.")
    await wait_until(lambda: not page.busy)
    assert (infant_id, 5000) not in page.sample_drafts
    assert page.sample_drafts[(infant_id, 7000)] == {"independent": True}
    infant_samples = [row for row in page.samples if row["individual_id"] == infant_id]
    assert [row["offset_ms"] for row in infant_samples] == [1000, 3000, 7000]
