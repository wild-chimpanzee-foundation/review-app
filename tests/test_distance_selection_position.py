"""Card, keyboard and explicit first-seen selection share inspection positioning."""

import json
import shutil
import subprocess
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage

NODE = shutil.which("node")


def keyboard_target(page, key, **flags):
    """Route the real browser shortcut to its card-selection target."""
    if NODE is None:
        pytest.skip("JavaScript checks require Node")
    source = Path(page_module.__file__).with_name("keyboard.js").read_text()
    state = {
        "active": page.individual_id,
        "ids": [row["id"] for row in page.individuals],
        "busy": page.busy,
        "key": key,
        **flags,
    }
    result = subprocess.run(
        [
            NODE,
            "-e",
            "const state = "
            + json.dumps(state)
            + """;
            let handler, clicked = null;
            global.window = {addEventListener(type, callback) {handler = callback;}};
            global.document = {
                querySelector() {return state.dialog ? {} : null;},
                querySelectorAll() {return state.ids.map(id => ({
                    dataset: {distanceActive: String(id === state.active)},
                    querySelector() {return {disabled: state.busy, click() {clicked = id;}};},
                }));},
            };
            """
            + source
            + """
            handler({...state, target: {closest() {return state.typing || null;}},
                preventDefault() {}, stopPropagation() {}});
            console.log(JSON.stringify(clicked));
            """,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    return json.loads(result.stdout)


@pytest.fixture
def selection_page(distance_data, monkeypatch):
    data = distance_data
    dp = data["dp"]
    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    page = DistanceReviewPage.__new__(DistanceReviewPage)
    page.client = nullcontext()
    page.dp = dp
    page.queue, page.index = [{"video_id": data["videos"][0], "duration_sec": 10}], 0
    page.individuals = dp.list_distance_individuals(data["videos"][0])
    page.individual_id = data["individual"]["id"]
    page.busy = False
    page.animal_player = object()
    page.animal_frame_rate = 25
    page.reference = page.reference_player = None
    page.references = []
    page.frames = [data["frame"]]
    page.selected_frame_id = data["frame"]["id"]
    page.sample_offset = 7000
    page.sample_drafts, page.sample_defaults = {}, {}
    for name in (
        "render_individuals",
        "render_samples",
        "render_sample_actions",
        "render_individual_progress",
        "render_tracks",
        "render_sample_navigation",
        "render_frames",
    ):
        setattr(page, name, SimpleNamespace(refresh=Mock()))
    page.seek_reference = Mock()
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    monkeypatch.setattr(page_module, "seek_paused", Mock())
    monkeypatch.setattr(page_module, "configure_anchor", Mock())
    monkeypatch.setattr(page_module, "anchor_mode", Mock())

    async def io_bound(callback, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(page_module.run, "io_bound", io_bound)
    return page, data


@pytest.mark.parametrize("entry", ["card", "j", "k"])
@pytest.mark.parametrize("reviewed", ["unreviewed", "annotated", "skipped", "mixed"])
@pytest.mark.parametrize(
    "first_seen,scheduled", [(0, 1000), (1000, 1000), (1040, 3000), (9500, 9000)]
)
async def test_card_and_jk_always_inspect_first_seen(
    selection_page, entry, reviewed, first_seen, scheduled
):
    page, data = selection_page
    target = page.dp.create_distance_individual(
        data["videos"][0], data["species_id"], first_seen, anchor=data["anchor"]
    )
    page.individuals = page.dp.list_distance_individuals(data["videos"][0])
    offsets = page.dp.get_distance_review_state(data["videos"][0], "alice")["offsets"]
    for index, offset in enumerate(offsets):
        if reviewed == "unreviewed" or (reviewed == "mixed" and index == 2):
            continue
        page.dp.save_distance_sample(
            target["id"],
            offset,
            reference_frame_id=data["frame"]["id"] if reviewed == "annotated" else None,
            skip_reason=None if reviewed == "annotated" else "individual absent",
            labeled_by="alice",
        )
    target_id = target["id"] if entry == "card" else keyboard_target(page, entry)
    assert target_id == target["id"]

    await page.select_individual(target_id)

    assert page.individual_id == target["id"]
    assert page.sample_offset == scheduled
    assert not page.busy
    assert (
        page.dp.get_distance_review_state(data["videos"][0], "alice")["active_individual_id"]
        == target["id"]
    )
    page_module.seek_paused.assert_called_once_with(page.animal_player, first_seen)
    page_module.configure_anchor.assert_called_once_with(
        page.animal_player, page.individual, individuals=page.individuals
    )
    # Returning from unsnapped inspection seeks the selected scheduled frame.
    page.seek_sample()
    page_module.seek_paused.assert_called_with(
        page.animal_player,
        page.sample_seek_offset(scheduled),
    )


@pytest.mark.parametrize("active", [False, True])
async def test_explicit_first_seen_repositions_sample_and_preserves_both_drafts(
    selection_page, active
):
    page, data = selection_page
    target = page.dp.create_distance_individual(
        data["videos"][0], data["species_id"], 1040, anchor=data["anchor"]
    )
    page.individuals = page.dp.list_distance_individuals(data["videos"][0])
    if active:
        page.individual_id = target["id"]
    old_key = (page.individual_id, 7000)
    old_draft = {
        "behavior_ids": [data["behavior_id"]],
        "carries": True,
        "infant_id": "_new",
        "reference_frame_id": None,
        "skip_reason": "midpoint not visible",
        "reference_video_id": None,
    }
    target_draft = {
        **old_draft,
        "behavior_ids": [],
        "carries": False,
        "skip_reason": "individual absent",
    }
    page.sample_drafts[old_key] = old_draft.copy()
    page.sample_drafts[(target["id"], 3000)] = target_draft.copy()
    page.selected_frame_id = "midpoint not visible"

    await page.go_to_first_seen(target["id"])

    assert page.individual_id == target["id"]
    assert page.sample_offset == 3000
    assert page.selected_skip_reason == "individual absent"
    assert page.sample_drafts[old_key] == old_draft
    assert page.sample_drafts[(target["id"], 3000)] == target_draft
    page_module.seek_paused.assert_called_once_with(page.animal_player, 1040)
    assert not page.busy


@pytest.mark.parametrize("entry", ["card", "j", "k"])
async def test_normal_switches_preserve_and_restore_unsaved_sample_choices(selection_page, entry):
    page, data = selection_page
    target = page.dp.create_distance_individual(
        data["videos"][0], data["species_id"], 1040, anchor=data["anchor"]
    )
    page.individuals = page.dp.list_distance_individuals(data["videos"][0])
    first_id = page.individual_id
    page.sample_offset = 1000
    page.selected_frame_id = "midpoint not visible"
    original = {
        "behavior_ids": [data["behavior_id"]],
        "carries": True,
        "infant_id": "_new",
        "reference_frame_id": None,
        "skip_reason": "midpoint not visible",
        "reference_video_id": None,
    }
    page.sample_drafts[(first_id, 1000)] = original.copy()
    target_draft = {**original, "behavior_ids": [], "skip_reason": "individual absent"}
    page.sample_drafts[(target["id"], 3000)] = target_draft.copy()

    for expected in (target["id"], first_id):
        selected = expected if entry == "card" else keyboard_target(page, entry)
        await page.select_individual(selected)

    assert page.individual_id == first_id
    assert page.sample_offset == 1000
    assert page.selected_skip_reason == "midpoint not visible"
    assert page.sample_drafts[(first_id, 1000)] == original
    assert page.sample_drafts[(target["id"], 3000)] == target_draft


async def test_session_resume_retains_first_unreviewed_sample_contract(selection_page):
    page, data = selection_page
    page.dp.save_distance_sample(
        page.individual_id, 1000, skip_reason="individual absent", labeled_by="alice"
    )
    await page.reload_review(resume=True)
    assert page.sample_offset == 3000
    page_module.seek_paused.assert_not_called()


async def test_jk_wrapping_uses_card_selection_position_in_both_directions(selection_page):
    page, data = selection_page
    second = page.dp.create_distance_individual(
        data["videos"][0], data["species_id"], 4040, anchor=data["anchor"]
    )
    page.individuals = page.dp.list_distance_individuals(data["videos"][0])
    first = data["individual"]
    for key, expected in [("j", second), ("j", first), ("k", second), ("k", first)]:
        await page.select_individual(keyboard_target(page, key))
        assert page.individual_id == expected["id"]
        page_module.seek_paused.assert_called_with(
            page.animal_player, expected["first_seen_offset_ms"]
        )


@pytest.mark.parametrize("flags", [{"typing": True}, {"dialog": True}, {"busy": True}])
@pytest.mark.parametrize("key", ["j", "k"])
async def test_jk_guards_do_not_change_selection_or_position(selection_page, flags, key):
    page, data = selection_page
    page.dp.create_distance_individual(
        data["videos"][0], data["species_id"], 1040, anchor=data["anchor"]
    )
    page.individuals = page.dp.list_distance_individuals(data["videos"][0])
    page.busy = flags.get("busy", False)
    assert keyboard_target(page, key, **flags) is None
    assert page.individual_id == data["individual"]["id"]
    assert page.sample_offset == 7000
    page_module.seek_paused.assert_not_called()


@pytest.mark.parametrize("action", ["select_individual", "go_to_first_seen"])
@pytest.mark.parametrize("guard", ["busy", "missing"])
async def test_selection_rejects_busy_or_stale_targets(selection_page, action, guard):
    page, data = selection_page
    target = page.dp.create_distance_individual(
        data["videos"][0], data["species_id"], 1040, anchor=data["anchor"]
    )
    page.individuals = page.dp.list_distance_individuals(data["videos"][0])
    page.busy = guard == "busy"
    await getattr(page, action)(target["id"] if guard == "busy" else "missing")
    assert page.individual_id == data["individual"]["id"]
    assert page.sample_offset == 7000
    page_module.seek_paused.assert_not_called()


async def test_selection_before_clock_alignment_still_inspects_first_seen(selection_page):
    page, data = selection_page
    # An unverified review has no scheduled samples yet.
    page.queue = [{"video_id": data["videos"][1], "duration_sec": 10}]
    target = page.dp.create_distance_individual(
        data["videos"][1], data["species_id"], 1040, anchor=data["anchor"]
    )
    page.individuals = page.dp.list_distance_individuals(data["videos"][1])
    await page.select_individual(target["id"])
    assert page.sample_offset is None
    page_module.seek_paused.assert_called_once_with(page.animal_player, 1040)
