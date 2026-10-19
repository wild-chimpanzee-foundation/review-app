"""Distance drawer interactions against persisted animal attributes and samples."""

import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import app, ui
from nicegui.testing import User
from review_app.app import config as app_config
from review_app.app.pages.distance_review import anchor_drawing as anchor_module
from review_app.app.pages.distance_review import clock_dialog as clock_module
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.backend.db.models import Video

pytest_plugins = ["nicegui.testing.user_plugin"]


@pytest.fixture
def drawer_page(distance_data, monkeypatch):
    data, dp = distance_data, distance_data["dp"]
    second = dp.create_distance_individual(
        data["videos"][0], data["species_id"], 1000, anchor=data["anchor"]
    )
    tags = dp.get_all_tags()
    holder = {}
    monkeypatch.setattr(
        "review_app.app.pages.distance_review.page.get_annotator_name", lambda: "alice"
    )

    @ui.page("/distance-drawer-test")
    async def drawer():
        species = dp.get_distance_species_options(data["project"].id)
        page = DistanceReviewPage(dp, data["project"].id, species, tags)
        page.animal_frame_rate = 25
        page.queue = [{"video_id": data["videos"][0], "camera_id": "cam_x"}]
        page.individuals = dp.list_distance_individuals(data["videos"][0])
        page.individual_id = data["individual"]["id"]
        page.behavior_options = dp.get_distance_behavior_options()
        page.render_timing = SimpleNamespace(refresh=Mock())
        page.render_samples = SimpleNamespace(refresh=Mock())
        page.render_sample_actions = Mock()
        page.render_sample_navigation = SimpleNamespace(refresh=Mock())
        page.render_individuals()
        holder["page"] = page

    return holder, data, second


async def wait_for_saved(page, expected=lambda: True):
    # Event handlers and io_bound writes run in background tasks.
    for _ in range(100):
        await asyncio.sleep(0.01)
        fields_ready = all(
            element.enabled
            for element in page.client.elements.values()
            if isinstance(element, ui.select)
        )
        if (
            not page.busy
            and not page.individual_attributes_lock.locked()
            and fields_ready
            and expected()
        ):
            return
    raise AssertionError("Animal update did not finish")


async def test_only_selected_animal_expands(user: User, drawer_page):
    holder, data, second = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]

    def assert_cards(active_id):
        cards = [
            element
            for element in user.client.elements.values()
            if element.props.get("data-distance-individual")
        ]
        assert len(cards) == 2
        for card in cards:
            fields = [element for element in card.descendants() if isinstance(element, ui.select)]
            assert len(fields) == (4 if card.props["data-distance-individual"] == active_id else 1)
            buttons = [element for element in card.descendants() if isinstance(element, ui.button)]
            assert any(button.props.get("icon") == "delete" for button in buttons)
            assert not any(button.props.get("icon") == "save" for button in buttons)

    assert_cards(data["individual"]["id"])
    user.find("#2").click()
    await wait_for_saved(page, lambda: page.individual_id == second["id"])
    await user.should_see("First seen at 00:01.000")
    assert page.individual_id == second["id"]
    assert_cards(second["id"])


@pytest.mark.parametrize("active", [False, True])
async def test_completed_expanded_individual_is_green_and_checked(user: User, drawer_page, active):
    holder, data, second = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.individuals[1]["review_status"] = "completed"
    if active:
        page.individual_id = second["id"]

    def card():
        return next(
            element
            for element in page.client.elements.values()
            if element.props.get("data-distance-individual") == second["id"]
        )

    def select():
        return next(
            element
            for element in card().descendants()
            if element.props.get("data-distance-action") == "select-individual"
        )

    with user:
        page.render_individuals.refresh()
    await wait_for_saved(page, lambda: select().props.get("icon") == "check_box")
    assert select().props["color"] == "positive"
    assert select().props["aria-pressed"] == str(active).lower()
    assert f"var(--q-{'warning' if active else 'positive'})" in str(card().style)


async def test_individual_shortcut_targets_keep_card_selection_and_busy_guards(
    user: User, drawer_page
):
    holder, data, second = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.sample_offset = 3000
    page.sample_values = {
        "behavior_ids": ["unconfirmed-behavior"],
        "carries": False,
        "infant_id": "_new",
    }

    def selection_button(individual_id):
        card = next(
            element
            for element in page.client.elements.values()
            if element.props.get("data-distance-individual") == individual_id
        )
        return next(
            element
            for element in card.descendants()
            if element.props.get("data-distance-action") == "select-individual"
        )

    button = selection_button(second["id"])
    assert button.enabled

    async def wait_for_button(enabled):
        for _ in range(100):
            if button.enabled == enabled:
                return
            await asyncio.sleep(0.01)
        raise AssertionError("Individual selection binding did not update")

    page.busy = True
    await wait_for_button(False)
    await page.select_individual(second["id"])
    assert page.individual_id == data["individual"]["id"]
    page.busy = False
    await wait_for_button(True)
    user.find("#2").click()
    await wait_for_saved(page, lambda: page.individual_id == second["id"])
    assert page.sample_values["behavior_ids"] == []


async def test_sample_timing_is_below_video_and_progress_is_in_selected_card(
    user: User, drawer_page, monkeypatch
):
    holder, data, _ = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.queue[0].update(data["dp"].get_video_detail(data["videos"][0]))
    page.review = {"recorded_start": datetime(2026, 10, 12, 16, 2, 49)}
    page.offsets = [1000, 3000]
    page.sample_offset = 3000
    page.samples = [
        {"individual_id": page.individual_id, "offset_ms": 1000, "carried_by_individual_id": None}
    ]
    page.render_tracks = Mock()
    page.render_references = Mock()
    monkeypatch.setattr(page_module, "seek_paused", Mock())
    monkeypatch.setattr(page_module, "install_anchor_editor", Mock())
    monkeypatch.setattr(page_module, "install_manual_pause_snap", Mock())
    monkeypatch.setattr(
        page_module,
        "render_custom_video_player",
        lambda *_, **__: ui.video("/test-animal.mp4"),
    )
    del page.render_sample_navigation
    with page.client:
        page.render_workspace()
        page.render_individuals.refresh()
    await user.should_see("Camera timestamp: 2026-10-12 16:02:52")
    await user.should_see("1/2 samples reviewed")
    timing = next(
        element
        for element in page.client.elements.values()
        if "distance-animal-timing" in element.classes
    )
    player = page.animal_player
    timing_container = timing.parent_slot.parent
    assert player.parent_slot is timing_container.parent_slot
    siblings = player.parent_slot.children
    assert siblings.index(timing_container) == siblings.index(player) + 1
    assert any(
        isinstance(element, ui.button) and element.props.get("icon") == "replay"
        for element in timing.descendants()
    )
    assert not any(
        "samples reviewed" in (getattr(element, "text", "") or "")
        for element in timing.descendants()
    )
    queue = next(
        element for element in page.client.elements.values() if "distance-queue" in element.classes
    )
    assert timing not in list(queue.descendants())
    assert not any(
        "distance-timeline" in element.classes
        or element.props.get("icon") == "my_location"
        or element.props.get("data-testid") == "distance-sample"
        or element.props.get("data-distance-action") in ("previous-sample", "next-sample")
        for element in page.client.elements.values()
    )
    progress = next(
        element
        for element in page.client.elements.values()
        if "distance-individual-progress" in element.classes
    )
    active_card = next(
        element
        for element in page.client.elements.values()
        if element.props.get("data-distance-active") == "true"
    )
    assert progress in list(active_card.descendants())
    page.samples.append(
        {"individual_id": page.individual_id, "offset_ms": 3000, "carried_by_individual_id": None}
    )
    with page.client:
        page.refresh_sample_ui()
    await user.should_see("2/2 samples reviewed")
    page.busy = True
    with page.client:
        page.render_sample_navigation.refresh()
    await wait_for_saved_timing_disabled(page)
    page.busy = False
    page.sample_offset = 1000
    with page.client:
        page.render_sample_navigation.refresh()
    await user.should_see("Play to this sample")
    page.offsets = [0, 2000]
    page.sample_offset = 0
    with page.client:
        page.render_sample_navigation.refresh()
    await user.should_not_see("Play to this sample")


@pytest.mark.parametrize("offset,start", [(1000, 0), (3000, 1000.01), (0, None)])
async def test_replay_includes_first_sample_lead_in(
    user: User, drawer_page, monkeypatch, offset, start
):
    holder, _, _ = drawer_page
    playback = AsyncMock()
    monkeypatch.setattr(page_module, "play_to_sample", playback)
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.review = {"recorded_start": datetime(2026, 10, 12, 16, 2, 49)}
    page.offsets = [1000, 3000] if offset else [0, 2000]
    page.sample_offset = offset
    page.animal_player = object()
    page.render_tracks = Mock()
    del page.render_sample_navigation
    with user:
        page.render_sample_navigation()
    before = list(page.samples)
    if start is None:
        await user.should_not_see("Play to this sample")
        await page.replay_interval()
        playback.assert_not_awaited()
    else:
        user.find("Play to this sample").click()
        await wait_for_saved(page, lambda: playback.await_count == 1)
        playback.assert_awaited_once_with(page.animal_player, start, offset + 0.01)
    assert page.sample_offset == offset
    assert page.samples == before


@pytest.mark.parametrize("empty_queue", [False, True])
async def test_annotation_sidebar_toggle_preserves_players_and_drafts(
    user: User, drawer_page, empty_queue, monkeypatch
):
    from review_app.app import entry_point

    monkeypatch.setattr(entry_point, "get_active_project_id", lambda: None)
    monkeypatch.setattr("review_app.app.update_checker.check_for_update", lambda: None)
    holder, _, _ = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.queue[0]["video_path"] = "animal.mp4"
    if empty_queue:
        page.queue = []
    page.render_animal = Mock()
    page.render_references = Mock()
    page.animal_player = object()
    page.reference_player = object()
    page.sample_offset = 1000
    page.sample_values = {"behavior_ids": ["draft-behavior"], "carries": True, "infant_id": "_new"}
    players = (page.animal_player, page.reference_player)
    active_id = page.individual_id
    values = page.sample_values.copy()
    with user:
        page.right_drawer = ui.right_drawer(value=True).props(
            "behavior=desktop width=400 mini-width=72"
        )
        left_drawer, _ = entry_point.shared_header(show_drawer=True, distance_interface=True)
        with page.right_drawer:
            page.render_sidebar_toggle()
            draft = ui.input("Unsaved draft", value="keep me")
        with ui.column() as workspace:
            page.render_workspace()
    header = next(
        element for element in page.client.elements.values() if isinstance(element, ui.header)
    )
    toggle = next(
        element
        for element in page.right_drawer.descendants()
        if element.props.get("data-testid") == "distance-toggle-annotations"
    )
    filters = next(
        element for element in header.descendants() if element.props.get("icon") == "filter_list"
    )
    assert toggle not in list(header.descendants())
    assert toggle in list(page.right_drawer.descendants())
    assert "distance-sidebar-toggle-row" in toggle.parent_slot.parent.classes
    assert toggle not in list(workspace.descendants())
    assert toggle.props["flat"] and toggle.props["color"] == "primary"
    assert toggle.text == "Collapse sidebar" and not toggle.props.get("round", False)
    assert toggle.props["icon"] == "chevron_right"
    assert toggle.props["aria-label"] == "Expand/collapse annotations"

    def click(button):
        interaction = user.find(ui.button)
        interaction.elements = {button}
        interaction.click()

    expected_renders = 0 if empty_queue else 1
    assert page.render_animal.call_count == expected_renders
    click(toggle)
    assert page.right_drawer.value is True and page.right_drawer.props["mini"] is True
    assert toggle.props["icon"] == "chevron_left"
    assert toggle.text == "" and toggle.props["round"] is True
    assert left_drawer.value is True
    click(filters)
    assert left_drawer.value is False and page.right_drawer.props["mini"] is True
    click(filters)
    click(toggle)
    assert page.right_drawer.value is True and not page.right_drawer.props.get("mini", False)
    assert toggle.props["icon"] == "chevron_right"
    assert toggle.text == "Collapse sidebar" and not toggle.props.get("round", False)
    assert left_drawer.value is True
    click(
        next(
            element
            for element in header.descendants()
            if element.props.get("icon") == "visibility_off"
        )
    )
    assert header.value is False
    restore = next(
        element
        for element in page.client.elements.values()
        if "distance-header-restore" in element.classes
    )
    click(restore)
    assert header.value is True
    assert toggle in list(page.right_drawer.descendants())
    assert draft.value == "keep me"
    assert (page.animal_player, page.reference_player) == players
    assert page.individual_id == active_id and page.sample_offset == 1000
    assert page.sample_values == values
    assert page.render_animal.call_count == expected_renders
    assert page.render_references.call_count == expected_renders


async def test_compact_rail_keeps_add_individuals_and_guarded_finish(user: User, drawer_page):
    holder, data, second = drawer_page
    data["dp"].verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    await user.open("/distance-drawer-test")
    page = holder["page"]
    await page.reload_review()
    page.sample_offset = 1000
    page.animal_player = object()
    page.add_individual_dialog = AsyncMock()
    page.select_individual = AsyncMock()
    page.finish_video = AsyncMock()
    page.individuals[1]["review_status"] = "completed"
    with user:
        page.render_sidebar_rail()

    def button(action, individual_id=None):
        return next(
            element
            for element in page.client.elements.values()
            if isinstance(element, ui.button)
            and element.props.get("data-distance-action") == action
            and (
                individual_id is None
                or element.props.get("data-distance-rail-individual") == individual_id
            )
        )

    def click(target):
        interaction = user.find(ui.button)
        interaction.elements = {target}
        interaction.click()

    add = button("rail-add")
    assert add.text == "A" and add.enabled
    first = button("rail-select-individual", data["individual"]["id"])
    other = button("rail-select-individual", second["id"])
    assert first.props["aria-pressed"] == "true"
    assert first.props["data-distance-complete"] == "false"
    assert other.props["data-distance-complete"] == "true"
    assert any(
        isinstance(item, ui.icon) and item.props["name"] == "check_box"
        for item in other.descendants()
    )
    assert not button("rail-finish-video").enabled
    # Rail entries do not duplicate the cards used by the existing J/K shortcut.
    assert (
        sum(
            bool(item.props.get("data-distance-individual"))
            for item in page.client.elements.values()
        )
        == 2
    )
    click(add)
    await wait_for_saved(page, lambda: page.add_individual_dialog.await_count == 1)
    click(other)
    await wait_for_saved(page, lambda: page.select_individual.await_count == 1)
    page.select_individual.assert_awaited_once_with(second["id"])

    page.individuals[0]["review_status"] = "completed"
    with user:
        page.render_sidebar_rail.refresh()
    await wait_for_saved(page, lambda: button("rail-finish-video").enabled)
    finish = button("rail-finish-video")
    assert finish.enabled and finish.props["color"] == "positive"
    click(finish)
    await wait_for_saved(page, lambda: page.finish_video.await_count == 1)
    page.busy = True
    with user:
        page.render_sidebar_rail.refresh()
    await asyncio.sleep(0.01)
    assert not button("rail-add").enabled
    assert not button("rail-select-individual", second["id"]).enabled
    assert not button("rail-finish-video").enabled


async def test_ordinary_header_does_not_add_annotation_toggle(user: User, monkeypatch):
    from review_app.app import entry_point

    monkeypatch.setattr(entry_point, "get_active_project_id", lambda: None)
    monkeypatch.setattr("review_app.app.update_checker.check_for_update", lambda: None)

    @ui.page("/ordinary-header-test")
    def ordinary_header():
        entry_point.shared_header(show_drawer=True)

    await user.open("/ordinary-header-test")
    assert not any(
        element.props.get("data-testid") == "distance-toggle-annotations"
        for element in user.client.elements.values()
    )


async def wait_for_saved_timing_disabled(page):
    for _ in range(100):
        if any(
            isinstance(element, ui.button)
            and element.props.get("icon") == "replay"
            and not element.enabled
            for element in page.client.elements.values()
        ):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Replay must be disabled while busy")


@pytest.mark.parametrize("dialog_kind", ["creation", "redraw"])
@pytest.mark.parametrize("finish", ["cancel", "save"])
@pytest.mark.parametrize("create_infant", [False, True])
async def test_fixed_frame_anchor_dialog_setup_guards_and_actions(
    user: User, drawer_page, monkeypatch, dialog_kind, finish, create_infant
):
    holder, data, _ = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    individual_id = data["individual"]["id"]
    row = page.individual
    offset_ms = row["first_seen_offset_ms"]
    main_player = SimpleNamespace(id=999, source="/main-video")
    preview = SimpleNamespace(id=1000, on=Mock())
    page.animal_player = main_player
    page.queue[0]["duration_sec"] = 10
    page.review = {"verified_by": "alice"}
    page.sample_offset = 3000
    sample_values = {"behavior_ids": ["unconfirmed-behavior"], "carries": False, "infant_id": None}
    page.sample_values = sample_values.copy()
    page.reload_individuals = AsyncMock()
    page.refresh_sample_ui = Mock()
    page.create_individual = AsyncMock(return_value=True)
    page.tag_options = {"test-tag": "Identified"}
    filters = {"brightness": 1.4, "contrast": 0.7}
    monkeypatch.setattr(page_module, "capture_offset", AsyncMock(return_value=offset_ms))
    monkeypatch.setattr(page_module, "image_filters", AsyncMock(return_value=filters))
    render = Mock(return_value=preview)
    install = Mock()
    configure = Mock()
    seek = Mock()
    mark = Mock()
    shortcuts = Mock()
    monkeypatch.setattr(anchor_module, "render_custom_video_player", render)
    monkeypatch.setattr(anchor_module, "install_anchor_editor", install)
    monkeypatch.setattr(anchor_module, "configure_anchor", configure)
    monkeypatch.setattr(anchor_module, "seek_paused", seek)
    monkeypatch.setattr(anchor_module, "anchor_mode", mark)
    monkeypatch.setattr(page_module, "playback_shortcuts", shortcuts)
    monkeypatch.setattr(anchor_module, "stop_player", Mock())

    if dialog_kind == "redraw":
        await page.redraw_anchor_dialog(individual_id)
        action_label = "Save"
    else:
        await page.add_individual_dialog()
        action_label = "Create individual"
        await user.should_see("Carries an infant")
        if create_infant:
            user.find("Carries an infant").click()
    dialog = next(
        element for element in page.client.elements.values() if isinstance(element, ui.dialog)
    )
    if dialog_kind == "creation" and create_infant:
        values = {"Age": "Adult", "Sex": "Female", "Individual tags": ["test-tag"]}
        for element in dialog.descendants():
            if isinstance(element, ui.select) and element.props.get("label") in values:
                element.set_value(values[element.props["label"]])
    render.assert_called_once()
    assert render.call_args.args[:2] == ("/main-video", 10)
    assert render.call_args.kwargs == {
        "autoplay": False,
        "keyboard": False,
        "compact": True,
        "image_state": filters,
        "paused_frame_only": True,
        "fixed_offset_ms": offset_ms,
    }
    seek.assert_called_once_with(preview, offset_ms)
    assert preview.on.call_args.args[0] == "loadedmetadata"
    preview.on.call_args.args[1]()
    assert seek.call_count == 2
    shortcuts.assert_not_called()
    user.find("Draw box").click()
    mark.assert_called_once_with(preview, "box")
    assert install.call_args.kwargs == {"frame_rate": 25, "initial_mode": "box"}
    draw, draft = install.call_args.args[1:3]
    geometry = {"x": 0.2, "y": 0.25, "width": 0.3, "height": 0.35}
    payload = {"individual_id": draft["id"], "offset_ms": offset_ms, "anchor": geometry}

    async def assert_ignored(candidate=payload):
        await draw(SimpleNamespace(args=candidate))
        configure.assert_not_called()
        assert all(not element.enabled for element in user.find(action_label).elements)

    await assert_ignored(dict(payload, individual_id="wrong-identity"))
    await assert_ignored(dict(payload, offset_ms=offset_ms + 20))  # Half-frame boundary.
    await assert_ignored({key: value for key, value in payload.items() if key != "offset_ms"})
    page.busy = True
    await assert_ignored()
    page.busy = False
    video_id = page.queue[0]["video_id"]
    page.queue[0]["video_id"] = "switched-video"
    await assert_ignored()
    page.queue[0]["video_id"] = video_id
    dialog.close()
    await assert_ignored()
    dialog.open()
    if dialog_kind == "redraw":
        row["first_seen_offset_ms"] += 1000
        await assert_ignored()
        row["first_seen_offset_ms"] = offset_ms
        individuals = page.individuals
        page.individuals = [item for item in individuals if item["id"] != individual_id]
        await assert_ignored()
        page.individuals = individuals

    await draw(SimpleNamespace(args=dict(payload, offset_ms=offset_ms + 19)))
    configure.assert_called_once_with(preview, draft)
    mark.assert_called_with(preview, None)
    assert draft["anchor_width"] == geometry["width"]
    assert all(element.enabled for element in user.find(action_label).elements)
    before = data["dp"].list_distance_individuals(video_id)
    user.find("Cancel" if finish == "cancel" else action_label).click()
    await wait_for_saved(page, lambda: not dialog.value)
    after = data["dp"].list_distance_individuals(video_id)
    if finish == "cancel":
        assert before == after
        page.create_individual.assert_not_awaited()
    elif dialog_kind == "creation":
        page.create_individual.assert_awaited_once_with(
            video_id,
            data["species_id"],
            offset_ms,
            geometry,
            create_infant=create_infant,
            age="Adult" if create_infant else None,
            sex="Female" if create_infant else None,
            tag_ids=["test-tag"] if create_infant else [],
        )
    else:
        saved = next(item for item in after if item["id"] == individual_id)
        assert saved["first_seen_offset_ms"] == offset_ms
        assert (saved["anchor_x"], saved["anchor_y"]) == (geometry["x"], geometry["y"])
    assert page.animal_player is main_player and page.sample_offset == 3000
    assert page.sample_values == sample_values


@pytest.mark.parametrize("skip_reason", [None, "individual absent", "midpoint not visible"])
async def test_carried_infant_locks_distance_but_allows_behavior_corrections(
    user: User, drawer_page, monkeypatch, skip_reason
):
    holder, data, _ = drawer_page
    dp, video_id = data["dp"], data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    pair = dp.save_distance_sample(
        data["individual"]["id"],
        1000,
        reference_frame_id=None if skip_reason else data["frame"]["id"],
        skip_reason=skip_reason,
        create_infant=True,
        labeled_by="alice",
    )
    # The very next infant sample is independent, despite the earlier carrying.
    dp.save_distance_sample(
        pair["infant_id"], 3000, reference_frame_id=data["frame"]["id"], labeled_by="alice"
    )
    monkeypatch.setattr(page_module, "capture_offset", AsyncMock(return_value=1000))
    monkeypatch.setattr(page_module, "play_to_sample", AsyncMock())
    monkeypatch.setattr(page_module, "configure_anchor", Mock())
    monkeypatch.setattr(page_module, "anchor_mode", Mock())
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.individual_id = pair["infant_id"]
    await page.reload_individuals()
    await page.reload_review()
    page.sample_offset = 1000
    page.frames = [data["frame"]]
    page.animal_player = object()
    await page.restore_sample(seek=False)
    del page.render_samples
    del page.render_sample_actions
    with user:
        page.render_frames()
        page.render_samples()
        page.render_sample_actions()
    await user.should_see("Carried by individual #1")

    def distance_buttons():
        return [
            element
            for element in page.client.elements.values()
            if isinstance(element, ui.button)
            and (
                element.text in {"5 m", "Not visible", "Absent"}
                or element.props.get("data-distance-action") in {"distance-down", "distance-up"}
            )
        ]

    assert len(distance_buttons()) == 5
    assert all(not button.enabled for button in distance_buttons())
    selection = page.selected_frame_id
    page.preview_frame({"id": "midpoint not visible"})
    page.move_distance(1)
    assert page.selected_frame_id == selection
    assert page.apply_to_end_assignment() is None
    bulk = next(
        element
        for element in page.client.elements.values()
        if element.props.get("data-distance-action") == "apply-to-end"
    )
    assert not bulk.enabled

    # Confirm may update behaviors but must retain the exact carrier evidence.
    page.sample_values["behavior_ids"] = [data["behavior_id"]]
    await page.commit_sample()
    saved = next(
        row
        for row in dp.get_distance_review_state(video_id, "alice")["samples"]
        if row["individual_id"] == pair["infant_id"] and row["offset_ms"] == 1000
    )
    assert saved["carried_by_individual_id"] == data["individual"]["id"]
    assert saved["behavior_ids"] == [data["behavior_id"]]
    assert saved["reference_frame_id"] == pair["reference_frame_id"]
    assert saved["skip_reason"] == skip_reason
    assert page.sample_offset == 3000
    assert not page.distance_locked
    await wait_for_saved(page, lambda: any(button.enabled for button in distance_buttons()))
    assert any(button.enabled for button in distance_buttons())
    await user.should_not_see("Carried by individual #1")


async def test_carrier_button_selects_adult_at_the_same_sample(user: User, drawer_page):
    holder, data, _ = drawer_page
    dp, video_id = data["dp"], data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    pair = dp.save_distance_sample(
        data["individual"]["id"],
        5000,
        reference_frame_id=data["frame"]["id"],
        create_infant=True,
        labeled_by="alice",
    )
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.individual_id = pair["infant_id"]
    await page.reload_individuals()
    await page.reload_review()
    page.sample_offset = 5000
    page.frames = [data["frame"]]
    await page.restore_sample(seek=False)
    del page.render_samples
    with user:
        page.render_samples()
    button = next(
        element
        for element in user.find("Carried by individual #1").elements
        if isinstance(element, ui.button)
    )
    assert button.props["icon"] == "link"
    page.busy = True
    await page.select_carrier(data["individual"]["id"], 5000)
    assert page.individual_id == pair["infant_id"]
    page.busy = False
    user.find("Carried by individual #1").click()
    await wait_for_saved(page, lambda: page.individual_id == data["individual"]["id"])
    assert page.sample_offset == 5000
    assert page.selected_frame_id == data["frame"]["id"]
    assert not page.distance_locked
    assert (
        dp.get_distance_review_state(video_id, "alice")["active_individual_id"]
        == page.individual_id
    )


async def test_creation_carrying_survives_restore_and_reload_until_saved_choice(
    user: User, drawer_page
):
    holder, data, _ = drawer_page
    dp, video_id = data["dp"], data["videos"][0]
    dp.verify_distance_recording_time(video_id, datetime(2026, 10, 12, 16, 2, 49), "alice")
    carrier = dp.create_distance_individual(
        video_id, data["species_id"], 3040, anchor=data["anchor"], create_infant=True
    )
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.individual_id = carrier["id"]
    await page.reload_individuals()
    await page.reload_review()
    page.sample_offset = 5000
    await page.restore_sample(seek=False)
    assert page.sample_values["infant_id"] == carrier["infant_id"]
    assert page.sample_values["carries"]
    del page.render_samples
    with user:
        page.render_samples()
    checkbox = next(
        element
        for element in user.find("Carries an infant").elements
        if isinstance(element, ui.checkbox)
    )
    assert checkbox.value

    # Earlier automatic absence is not carrying; an explicit later choice wins.
    page.sample_offset = 3000
    await page.restore_sample(seek=False)
    assert not page.sample_values["carries"]
    dp.save_distance_sample(
        carrier["id"], 5000, reference_frame_id=data["frame"]["id"], labeled_by="alice"
    )
    await page.reload_individuals()
    page.sample_offset = 7000
    await page.restore_sample(seek=False)
    assert not page.sample_values["carries"]


async def test_redraw_dialog_save_and_cancel_preserve_identity_player_and_current_form(
    user: User, drawer_page, monkeypatch
):
    holder, data, _ = drawer_page
    dp = data["dp"]
    individual_id = data["individual"]["id"]
    saved = dp.list_distance_individuals(data["videos"][0])[0]
    dp.set_distance_individual_anchor(individual_id, saved["first_seen_offset_ms"])
    await user.open("/distance-drawer-test")
    page = holder["page"]
    page.animal_player = SimpleNamespace(id=999, source="/main-video", on=Mock())
    page.queue[0].update(duration_sec=10, video_path="/isolated/animal.mp4")
    page.sample_offset = 3000
    page.sample_values = {
        "behavior_ids": ["unconfirmed-behavior"],
        "carries": True,
        "infant_id": "infant-id",
    }
    page.refresh_sample_ui = Mock()
    page.reload_individuals = AsyncMock()
    preview = SimpleNamespace(id=1000, source="/preview", on=Mock())
    monkeypatch.setattr(
        page_module, "image_filters", AsyncMock(return_value={"brightness": 1.4, "contrast": 0.7})
    )
    render_player = Mock(return_value=preview)
    monkeypatch.setattr(anchor_module, "render_custom_video_player", render_player)
    monkeypatch.setattr(page_module, "playback_shortcuts", Mock())
    monkeypatch.setattr(anchor_module, "seek_paused", Mock())
    monkeypatch.setattr(anchor_module, "stop_player", Mock())
    callbacks = []
    monkeypatch.setattr(
        anchor_module,
        "install_anchor_editor",
        lambda _p, callback, *_a, **_k: callbacks.append(callback),
    )

    async def open_redraw():
        with user:
            page.render_individual_card(page.individual)
        actions = [
            element
            for element in user.client.elements.values()
            if element.props.get("data-distance-action") == "redraw-anchor"
        ]
        assert actions[-1].enabled  # No-anchor legacy identities can still be redrawn.
        await page.redraw_anchor_dialog(individual_id)
        await user.should_see("Redraw box in dialog")
        assert callbacks
        assert page.animal_player is main_player
        assert render_player.call_args.kwargs["paused_frame_only"] is True
        assert render_player.call_args.kwargs["fixed_offset_ms"] == saved["first_seen_offset_ms"]
        page_module.playback_shortcuts.assert_not_called()

    main_player = page.animal_player
    await open_redraw()
    dialog = next(
        element for element in user.client.elements.values() if isinstance(element, ui.dialog)
    )
    original = dp.list_distance_individuals(data["videos"][0])[0]
    user.find("Cancel").click()
    await wait_for_saved(page, lambda: not dialog.value)
    after_cancel = dp.list_distance_individuals(data["videos"][0])[0]
    assert after_cancel["id"] == original["id"]
    assert after_cancel["first_seen_offset_ms"] == original["first_seen_offset_ms"]
    assert after_cancel["anchor_width"] is None
    assert page.animal_player is main_player and page.sample_offset == 3000
    assert page.sample_values["infant_id"] == "infant-id"

    await open_redraw()
    payload = {
        "individual_id": individual_id,
        "offset_ms": original["first_seen_offset_ms"],
        "anchor": {"x": 0.2, "y": 0.25, "width": 0.3, "height": 0.35},
    }
    await callbacks[-1](SimpleNamespace(args=payload))
    user.find("Save").click()
    await wait_for_saved(
        page,
        lambda: (
            not next(
                element
                for element in user.client.elements.values()
                if isinstance(element, ui.dialog)
            ).value
        ),
    )
    after_save = dp.list_distance_individuals(data["videos"][0])[0]
    assert after_save["id"] == original["id"]
    assert after_save["first_seen_offset_ms"] == original["first_seen_offset_ms"]
    assert (after_save["anchor_x"], after_save["anchor_y"]) == (0.2, 0.25)
    assert page.animal_player is main_player and page.sample_offset == 3000
    assert page.sample_values["infant_id"] == "infant-id"


async def test_redraw_draw_and_save_ignore_stale_video_or_identity(
    user: User, drawer_page, monkeypatch
):
    holder, data, _ = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    individual_id = data["individual"]["id"]
    page.animal_player = SimpleNamespace(id=999, source="/main-video", on=Mock())
    page.queue[0].update(duration_sec=10, video_path="/isolated/animal.mp4")
    page.refresh_sample_ui = Mock()
    page.reload_individuals = AsyncMock()
    preview = SimpleNamespace(id=1000, source="/preview", on=Mock())
    monkeypatch.setattr(page_module, "image_filters", AsyncMock(return_value={}))
    monkeypatch.setattr(anchor_module, "render_custom_video_player", Mock(return_value=preview))
    monkeypatch.setattr(anchor_module, "seek_paused", Mock())
    monkeypatch.setattr(anchor_module, "stop_player", Mock())
    callbacks = []
    monkeypatch.setattr(
        anchor_module,
        "install_anchor_editor",
        lambda _p, callback, *_a, **_k: callbacks.append(callback),
    )
    await page.redraw_anchor_dialog(individual_id)
    dialog = next(
        element for element in user.client.elements.values() if isinstance(element, ui.dialog)
    )
    row = next(row for row in page.individuals if row["id"] == individual_id)
    original_first_seen = row["first_seen_offset_ms"]
    payload = {
        "individual_id": individual_id,
        "offset_ms": original_first_seen,
        "anchor": {"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.2},
    }
    page.queue[0]["video_id"] = "switched-video"
    await callbacks[0](SimpleNamespace(args=payload))
    assert all(not element.enabled for element in user.find("Save").elements)
    page.queue[0]["video_id"] = data["videos"][0]
    wrong_identity = dict(payload, individual_id="another-individual")
    await callbacks[0](SimpleNamespace(args=wrong_identity))
    assert all(not element.enabled for element in user.find("Save").elements)
    await callbacks[0](SimpleNamespace(args=payload))
    page.individuals[0]["first_seen_offset_ms"] += 1000
    user.find("Save").click()
    await asyncio.sleep(0.05)
    assert not page.busy and dialog.value
    saved = next(
        item
        for item in data["dp"].list_distance_individuals(data["videos"][0])
        if item["id"] == individual_id
    )
    assert saved["first_seen_offset_ms"] == original_first_seen
    user.find("Cancel").click()


async def test_attributes_save_immediately_and_keep_rapid_changes(user: User, drawer_page):
    holder, data, _ = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    user.find("Age").click()
    user.find("Adult").click()
    user.find("Sex").click()
    user.find("Female").click()
    user.find("Individual tags").click()
    user.find("Injured").click()

    def saved_attributes():
        return data["dp"].list_distance_individuals(data["videos"][0])[0]

    await wait_for_saved(
        page,
        lambda: (
            saved_attributes()["age"] == "Adult"
            and saved_attributes()["sex"] == "Female"
            and len(saved_attributes()["tag_ids"]) == 1
        ),
    )
    saved = data["dp"].list_distance_individuals(data["videos"][0])[0]
    assert saved["age"] == "Adult" and saved["sex"] == "Female"
    assert len(saved["tag_ids"]) == 1
    assert saved["labeled_by"] == "alice"
    await user.should_see("Adult")
    await user.should_see("Female")
    await user.should_see("Injured")
    user.find("Age").clear()
    user.find("Sex").clear()
    user.find("Individual tags").clear()
    await wait_for_saved(
        page,
        lambda: (
            saved_attributes()["age"] is None
            and saved_attributes()["sex"] is None
            and saved_attributes()["tag_ids"] == []
        ),
    )
    saved = data["dp"].list_distance_individuals(data["videos"][0])[0]
    assert saved["age"] is None and saved["sex"] is None and saved["tag_ids"] == []


async def test_inactive_species_saves_without_expanding_card(user: User, drawer_page):
    holder, data, second = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    inactive_card = next(
        element
        for element in user.client.elements.values()
        if element.props.get("data-distance-individual") == second["id"]
    )
    species = next(
        element for element in inactive_card.descendants() if isinstance(element, ui.select)
    )
    interaction = user.find(ui.select)
    interaction.elements = {species}
    interaction.click()
    fox_id, fox_label = next(
        (key, label) for key, label in page.species_options.items() if "Fox" in label
    )
    user.find(fox_label).click()
    await wait_for_saved(
        page,
        lambda: data["dp"].list_distance_individuals(data["videos"][0])[1]["species_id"] == fox_id,
    )
    assert page.individual_id == data["individual"]["id"]
    assert len(user.find("Age").elements) == 1


async def test_rejected_age_change_restores_saved_value_and_allows_next_edit(
    user: User, drawer_page
):
    holder, data, _ = drawer_page
    dp, individual_id = data["dp"], data["individual"]["id"]

    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    pair = dp.save_distance_sample(
        individual_id,
        1000,
        reference_frame_id=data["frame"]["id"],
        create_infant=True,
        labeled_by="alice",
    )
    await user.open("/distance-drawer-test")
    page = holder["page"]
    user.find("#3").click()
    await wait_for_saved(page)
    await user.should_see("Infant")
    user.find("Age").click()
    user.find("Adult").click()
    await user.should_see("An individual with carried samples must retain age Infant")
    await wait_for_saved(page)
    user.find("Sex").click()
    user.find("Female").click()
    await wait_for_saved(
        page,
        lambda: (
            next(
                row
                for row in dp.list_distance_individuals(data["videos"][0])
                if row["id"] == pair["infant_id"]
            )["sex"]
            == "Female"
        ),
    )
    infant = next(row for row in page.individuals if row["id"] == pair["infant_id"])
    assert infant["age"] == "Infant" and infant["sex"] == "Female"


async def test_delete_active_and_last_animal_restores_drawer(user: User, drawer_page):
    holder, data, second = drawer_page
    data["dp"].verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    await user.open("/distance-drawer-test")
    page = holder["page"]

    def active_delete_button():
        card = next(
            element
            for element in user.client.elements.values()
            if element.props.get("data-distance-active") == "true"
        )
        return next(
            element
            for element in card.descendants()
            if element.props.get("data-distance-action") == "delete-individual"
        )

    # Scope the click to the active card's delete button.
    interaction = user.find(ui.button)
    interaction.elements = {active_delete_button()}
    interaction.click()
    await wait_for_saved(page, lambda: page.individual_id == second["id"])
    await user.should_not_see("#1")
    assert page.individual_id == second["id"]
    assert (
        data["dp"].get_distance_review_state(data["videos"][0], "alice")["active_individual_id"]
        == second["id"]
    )
    interaction = user.find(ui.button)
    interaction.elements = {active_delete_button()}
    interaction.click()
    await wait_for_saved(page, lambda: page.individual_id is None)
    await user.should_not_see("#2")
    await user.should_see("Pause at an animal's first appearance, then add an individual.")
    assert page.individual_id is None and page.sample_offset in page.offsets
    assert data["dp"].list_distance_individuals(data["videos"][0]) == []


@pytest.mark.parametrize(
    "opening_even,offset,duration,change",
    [
        (False, 920.125, 10, None),
        (False, 920.125, 10, "stepped"),
        (False, 920.125, 10, "unavailable"),
        (False, 920.125, 10, "stale"),
        (False, 920.125, 10, "stale_after_capture"),
        (False, 920.125, 10, "closed"),
        (True, 1080.125, 10, None),
        (True, 0, 0.5, None),
    ],
)
async def test_clock_confirmation_uses_current_frame_without_selection_controls(
    user: User, distance_data, monkeypatch, tmp_path, opening_even, offset, duration, change
):
    monkeypatch.setattr(app_config, "DISTANCE_CLOCK_ALIGNMENT_MODE", "exact")
    data, holder = distance_data, {}
    dp, video_id = data["dp"], data["videos"][0]
    monkeypatch.setattr(page_module, "get_annotator_name", lambda: "alice")
    if duration < 10:
        with dp.Session.begin() as session:
            session.get(Video, video_id).duration_sec = duration
    current = {"offset": offset}
    dialog_holder = {}

    async def capture_current_frame(player, *, exact=False):
        assert exact is True
        if change == "unavailable":
            raise ValueError("distance_player_not_ready")
        if change == "closed":
            dialog_holder["dialog"].close()
        if change == "stale_after_capture":
            holder["page"].queue[0]["video_id"] = "stale-video"
        return current["offset"]

    async def step_current_frame(player, delta, rate):
        current["offset"] += delta * 1000 / rate

    capture = AsyncMock(side_effect=capture_current_frame)
    monkeypatch.setattr(clock_module, "capture_offset", capture)
    monkeypatch.setattr(page_module, "_probe_frame_rate", Mock(return_value=25))
    step = AsyncMock(side_effect=step_current_frame)
    monkeypatch.setattr(clock_module, "step_frames", step)
    video_path = tmp_path / "clock.mp4"
    video_path.write_bytes(b"preview")

    @ui.page("/distance-clock-test")
    async def clock_page():
        page = DistanceReviewPage(
            dp, data["project"].id, dp.get_distance_species_options(data["project"].id), []
        )
        page.queue = [
            {
                "video_id": video_id,
                "camera_id": "cam_x",
                "video_path": str(video_path),
                "duration_sec": duration,
                "created_at": datetime(2026, 10, 12, 16, 2, 49),
            }
        ]
        page.animal_player = SimpleNamespace(source=page.media_url(video_path), id=0)
        page.restore_sample = AsyncMock()
        with ui.right_drawer() as sidebar:
            page.render_sidebar()
        with ui.column() as workspace:
            page.render_samples()
            page.render_sample_navigation()
        holder.update(page=page, sidebar=sidebar, workspace=workspace)

    await user.open("/distance-clock-test")
    page = holder["page"]

    def labels(root):
        return {getattr(element, "text", None) for element in root.descendants()}

    assert "Verify recording time" in labels(holder["sidebar"])
    assert "Verify recording time" not in labels(holder["workspace"])
    assert not any(
        isinstance(element, (ui.label, ui.button, ui.card))
        for element in holder["workspace"].descendants()
    )
    # The compact rail retains its Add tooltip; guard the actual actions instead.
    assert not any(
        element.props.get("data-distance-action") == "add"
        for element in holder["sidebar"].descendants()
    )
    rail_add = next(
        element
        for element in holder["sidebar"].descendants()
        if element.props.get("data-distance-action") == "rail-add"
    )
    assert not rail_add.enabled
    await page.add_individual_dialog()
    capture.assert_not_awaited()
    clock_action = next(
        element
        for element in holder["sidebar"].descendants()
        if element.props.get("data-distance-action") == "verify-clock"
    )
    assert any(
        isinstance(element, ui.badge) and element.text == "↵ Enter"
        for element in clock_action.descendants()
    )
    assert any(
        isinstance(element, ui.badge) and element.text == "↵ Enter"
        for element in rail_add.parent_slot.parent.descendants()
    )
    interaction = user.find(ui.button)
    interaction.elements = {clock_action}
    interaction.click()  # Same DOM action selected by Enter before clock setup.
    await user.should_see("Camera timestamp visible on the displayed frame")
    clock_input = next(
        element
        for element in user.client.elements.values()
        if isinstance(element, ui.input)
        and element.props.get("data-testid") == "distance-clock-time"
    )
    assert clock_input.value == "2026-10-12 16:02:50"
    assert clock_input.props["type"] == "text"
    assert clock_input.props["mask"] == "####-##-## ##:##:##"
    assert clock_input.props["placeholder"] == "YYYY-MM-DD HH:mm:ss"
    dialog_holder["dialog"] = next(
        element
        for element in user.client.elements.values()
        if isinstance(element, ui.dialog) and element.props.get("data-distance-clock-dialog")
    )
    await user.should_not_see("Use current frame")
    await user.should_not_see("I checked the selected frame against the even camera timestamp.")
    buttons = {
        element.text for element in user.client.elements.values() if isinstance(element, ui.button)
    }
    assert {"-1", "+1", "-5", "+5", "-20", "+20"}.issubset(buttons)
    if opening_even:
        user.find("The opening still already shows an even second.").click()
    if change == "stepped":
        step_button = user.find(ui.button)
        step_button.elements = {
            element
            for element in step_button.elements
            if element.props.get("data-distance-clock-step") == "1"
        }
        step_button.click()
        await wait_for_saved(page, lambda: step.await_count == 1)
        assert step.await_args.args[1:] == (1, 25)
        offset += 40
    if change == "stale":
        page.queue[0]["video_id"] = "stale-video"
    user.find("Confirm clock alignment").click()
    if change == "stale":
        await wait_for_saved(page)
        capture.assert_not_awaited()
        assert data["dp"].get_distance_review_state(video_id, "alice")["review"] is None
        return
    if change in ("closed", "stale_after_capture"):
        await wait_for_saved(page)
        capture.assert_awaited_once()
        assert data["dp"].get_distance_review_state(video_id, "alice")["review"] is None
        return
    if change == "unavailable":
        await user.should_see("Wait for the video to load before saving a frame.")
        assert page.review is None
        capture.assert_awaited_once()
        assert capture.await_args.kwargs == {"exact": True}
        return
    await wait_for_saved(page, lambda: page.review is not None)
    assert page.review["verified_by"] == "alice"
    assert page.review["alignment_mode"] == "exact"
    assert not any(isinstance(element, ui.radio) for element in user.client.elements.values())
    assert page.review["clock_anchor_playback_ms"] == offset
    assert page.review["clock_anchor_offset_ms"] == int(offset)
    assert page.review["opening_even_second"] is opening_even
    if duration == 0.5:
        assert page.offsets == [0]
    elif opening_even:
        assert page.offsets[:2] == [0, int(offset)]
    else:
        assert page.offsets[:2] == [int(offset), int(offset) + 2000]
    assert "Verify recording time" not in labels(holder["sidebar"])
    add = next(
        element
        for element in holder["sidebar"].descendants()
        if element.props.get("data-distance-action") == "add"
    )
    assert add.enabled


async def test_sample_shows_selected_and_saved_reference_distance(user: User, drawer_page):
    holder, data, _ = drawer_page
    dp = data["dp"]
    frame = dp.add_reference_frame(data["reference"]["id"], 6500, 7.25)
    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    dp.save_distance_sample(
        data["individual"]["id"], 1000, reference_frame_id=frame["id"], labeled_by="alice"
    )
    await user.open("/distance-drawer-test")
    page = holder["page"]
    await page.reload_review()
    page.sample_offset = 1000
    page.frames = [frame]
    page.selected_frame_id = frame["id"]
    del page.render_samples  # Render the sample controls alongside the animal cards.
    with user:
        page.render_samples()
        page.render_frames()
    await user.should_see("Saved distance: 7.25 m")
    await user.should_see("Selected reference: 7.25 m")
    await user.should_see("7.25 m")


async def test_accepting_reference_plays_to_next_frame_with_distance_preselected(
    user: User, drawer_page, monkeypatch
):
    holder, data, _ = drawer_page
    dp = data["dp"]
    frame = dp.add_reference_frame(data["reference"]["id"], 6500, 7.25)
    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    monkeypatch.setattr(page_module, "capture_offset", AsyncMock(return_value=1000))
    playback = AsyncMock()
    monkeypatch.setattr(page_module, "play_to_sample", playback)
    monkeypatch.setattr(page_module, "configure_anchor", Mock())
    monkeypatch.setattr(page_module, "anchor_mode", Mock())
    await user.open("/distance-drawer-test")
    page = holder["page"]
    await page.reload_review()
    page.sample_offset = 1000
    page.frames = [frame]
    page.selected_frame_id = frame["id"]
    page.animal_player = object()
    page.seek_sample = Mock()
    del page.render_samples
    del page.render_sample_actions
    with user:
        page.render_samples()
        page.render_sample_actions()
        page.render_frames()
    await user.should_not_see(ui.input)
    user.find("Sample behaviors").click()
    user.find("Grazing").click()
    user.find("Carries an infant").click()
    interaction = user.find(ui.button)
    interaction.elements = {
        element
        for element in user.client.elements.values()
        if element.props.get("data-distance-action") == "confirm"
    }
    interaction.click()
    await wait_for_saved(
        page, lambda: len(dp.get_distance_review_state(data["videos"][0], "alice")["samples"]) == 2
    )
    saved = dp.get_distance_review_state(data["videos"][0], "alice")["samples"][0]
    assert saved["distance_m"] == 7.25 and saved["reference_frame_id"] == frame["id"]
    assert page.sample_offset == 3000
    playback.assert_awaited_once_with(page.animal_player, 1000.01, 3000.01)
    page.seek_sample.assert_not_called()
    assert page.selected_frame_id == frame["id"]
    await user.should_see("Selected reference: 7.25 m")
    assert page.sample_values["behavior_ids"] == [data["behavior_id"]]
    assert page.sample_values["carries"]
    assert page.sample_values["infant_id"] != "_new"


async def test_sample_details_and_skip_actions_render_with_reference_distances(
    user: User, drawer_page
):
    holder, data, _ = drawer_page
    dp = data["dp"]
    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    dp.save_distance_sample(
        data["individual"]["id"], 1000, reference_frame_id=data["frame"]["id"], labeled_by="alice"
    )
    await user.open("/distance-drawer-test")
    page = holder["page"]
    await page.reload_review()
    page.sample_offset = 1000
    page.frames = [data["frame"]]
    page.selected_frame_id = data["frame"]["id"]
    page.render_timing = Mock()
    del page.render_samples
    del page.render_sample_actions
    with user:
        with ui.column() as reference_area:
            page.render_references()
        with ui.column() as sidebar:
            page.render_sidebar()

    def labels(root):
        return {
            getattr(element, "text", None) or element.props.get("label")
            for element in root.descendants()
        }

    sample_labels = {
        "Saved distance: 5 m",
        "Sample behaviors",
        "Carries an infant",
        "Selected reference: 5 m",
        "Absent",
        "Not visible",
    }
    assert sample_labels <= labels(reference_area)
    assert sample_labels.isdisjoint(labels(sidebar))
    assert {"Confirm selection and advance", "Apply to end"} <= labels(reference_area)
    assert {"Confirm selection and advance", "Apply to end"}.isdisjoint(labels(sidebar))
    assert any(
        element.props.get("data-distance-action") == "finish-video"
        for element in sidebar.descendants()
    )
    assert not any(
        element.props.get("data-distance-action") == "finish-video"
        for element in reference_area.descendants()
    )
    distance_strip = next(
        element for element in reference_area.descendants() if "distance-frames" in element.classes
    )
    visibility_row = next(
        element
        for element in reference_area.descendants()
        if "distance-visibility-choices" in element.classes
    )
    assert "5 m" in labels(distance_strip)
    assert {"Not visible", "Absent"}.isdisjoint(labels(distance_strip))
    assert {"Not visible", "Absent"} <= labels(visibility_row)
    assert "5 m" not in labels(visibility_row)
    assert distance_strip.parent_slot is visibility_row.parent_slot
    siblings = distance_strip.parent_slot.children
    assert siblings.index(distance_strip) < siblings.index(visibility_row)
    visibility_buttons = [
        element for element in visibility_row.descendants() if isinstance(element, ui.button)
    ]
    assert len(visibility_buttons) == 2
    assert all(button.props["color"] == "grey-7" for button in visibility_buttons)
    assert "distance-choice-toolbar" in visibility_row.parent_slot.parent.classes
    fields = next(
        element
        for element in reference_area.descendants()
        if "distance-sample-fields" in element.classes
    )
    assert {"Sample behaviors", "Carries an infant", "Carried infant"} <= labels(fields)
    summary = next(
        element
        for element in reference_area.descendants()
        if "distance-sample-summary" in element.classes
    )
    assert {"Saved distance: 5 m", "Selected reference: 5 m"} <= labels(summary)
    browser = next(
        element
        for element in reference_area.descendants()
        if "distance-reference-browser" in element.classes
    )
    assert "Reference clip" in labels(browser)
    assert any("distance-reference-clips" in element.classes for element in browser.descendants())
    actions = next(
        element
        for element in reference_area.descendants()
        if "distance-actions" in element.classes
    )
    # The refreshable action slot sits directly before the clip picker.
    action_slot = actions.parent_slot.parent
    assert action_slot.parent_slot is browser.parent_slot
    siblings = browser.parent_slot.children
    assert siblings.index(action_slot) + 1 == siblings.index(browser)
    # Reference changes refresh both the central selection and sidebar confirmation.
    other = dp.add_reference_frame(data["reference"]["id"], 6500, 7.25)
    page.frames.append(other)
    with user:
        page.move_distance(1)
    await user.should_see("Selected reference: 7.25 m")
    assert "Selected reference: 7.25 m" in labels(reference_area)
    assert "Saved distance: 5 m" in labels(reference_area)


@pytest.mark.parametrize(
    "steps,label,reason",
    [(1, "Not visible", "midpoint not visible"), (2, "Absent", "individual absent")],
)
@pytest.mark.parametrize("annotated", [False, True])
@pytest.mark.parametrize("browsed", [False, True])
async def test_visibility_confirmation_restores_saved_distance_or_prefills_unannotated_sample(
    user: User, drawer_page, monkeypatch, steps, label, reason, annotated, browsed
):
    holder, data, _ = drawer_page
    dp = data["dp"]
    dp.verify_distance_recording_time(
        data["videos"][0], datetime(2026, 10, 12, 16, 2, 49), "alice"
    )
    if annotated:
        # A saved next sample must still be the next automatic stop.
        dp.save_distance_sample(
            data["individual"]["id"],
            3000,
            reference_frame_id=data["frame"]["id"],
            labeled_by="alice",
        )
    monkeypatch.setattr(page_module, "capture_offset", AsyncMock(return_value=1000))
    playback = AsyncMock()
    monkeypatch.setattr(page_module, "play_to_sample", playback)
    monkeypatch.setattr(page_module, "seek_paused", Mock())
    monkeypatch.setattr(page_module, "configure_anchor", Mock())
    monkeypatch.setattr(page_module, "anchor_mode", Mock())
    await user.open("/distance-drawer-test")
    page = holder["page"]
    await page.reload_review()
    next_saved = next((row.copy() for row in page.samples if row["offset_ms"] == 3000), None)
    page.sample_offset = 1000
    page.frames = [data["frame"]]
    page.selected_frame_id = data["frame"]["id"]
    page.animal_player = object()
    page.seek_sample = Mock()
    del page.render_samples
    del page.render_sample_actions
    with user:
        page.render_frames()
        page.render_samples()
        page.render_sample_actions()

    if browsed:
        before = list(page.samples)
        for offset in page.offsets[1:]:
            await page.choose_sample(offset)
        await page.choose_sample(1000)
        assert page.samples == before
        assert dp.get_distance_review_state(data["videos"][0], "alice")["samples"] == before

    def click_action(action):
        interaction = user.find(ui.button)
        interaction.elements = {
            element
            for element in user.client.elements.values()
            if element.props.get("data-distance-action") == action
        }
        interaction.click()

    for _ in range(steps):
        click_action("distance-up")
    await user.should_see(f"Selected: {label}")
    assert len(dp.get_distance_review_state(data["videos"][0], "alice")["samples"]) == int(
        annotated
    )
    click_action("confirm")
    await wait_for_saved(page, lambda: page.sample_offset == 3000)
    saved = next(
        row
        for row in dp.get_distance_review_state(data["videos"][0], "alice")["samples"]
        if row["offset_ms"] == 1000
    )
    assert saved["skip_reason"] == reason and saved["reference_frame_id"] is None
    playback.assert_awaited_once_with(page.animal_player, 1000.01, 3000.01)
    if annotated:
        assert page.selected_frame_id == data["frame"]["id"]
        await user.should_see("Selected reference: 5 m")
        await user.should_see("Saved distance: 5 m")
    else:
        assert page.selected_skip_reason == reason
        await user.should_see(f"Selected: {label}")
        await user.should_not_see("Saved distance: 5 m")
    assert page.sample == next_saved

    # Manual navigation restores the frame's own saved evidence.
    await page.move_sample(-1)
    assert page.selected_skip_reason == reason
    await page.move_sample(1)
    assert page.selected_frame_id == (data["frame"]["id"] if annotated else reason)
    assert page.sample == next_saved
    persisted_samples = dp.get_distance_review_state(data["videos"][0], "alice")["samples"]
    assert next((row for row in persisted_samples if row["offset_ms"] == 3000), None) == next_saved
    assert len(persisted_samples) == 1 + int(annotated)


async def test_media_routes_survive_player_deletion_and_are_released_with_page(
    user: User, drawer_page, tmp_path
):
    holder, _, _ = drawer_page
    await user.open("/distance-drawer-test")
    page = holder["page"]
    path = tmp_path / "reference with spaces.mp4"
    path.write_bytes(b"reference-video")
    source = page.media_url(path)
    assert page.media_url(path) == source
    with user:
        first = ui.video(source)
        second = ui.video(source)
        first.delete()
    assert first.auto_route is None and second.auto_route is None
    assert any(getattr(route, "path", None) == source for route in app.routes)
    with user:
        other_page = DistanceReviewPage(page.dp, page.project_id, page.species_options, [])
    other_source = other_page.media_url(path)
    assert other_source != source
    page.release_media()
    assert not any(getattr(route, "path", None) == source for route in app.routes)
    assert any(getattr(route, "path", None) == other_source for route in app.routes)
    other_page.release_media()
