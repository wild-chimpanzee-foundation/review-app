"""Main-owned player dispatch, lost-window safety, and transactional reattachment."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from nicegui import core, ui
from review_app.app.pages.distance_review import player as player_module
from review_app.app.pages.distance_review.companion import AnimalCompanion, _sessions


class ClientStub:
    def __init__(self):
        self.id = "client"
        self.has_socket_connection = True
        self.instances = {self.id: self}
        self.elements = {}
        self.on_disconnect = Mock()
        self.connected = AsyncMock()
        self.run_javascript = AsyncMock(return_value=3120.125)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass


@pytest.fixture
async def companion(monkeypatch):
    monkeypatch.setattr(core, "loop", asyncio.get_running_loop())
    page = SimpleNamespace(
        client=ClientStub(),
        busy=False,
        render_companion_actions=SimpleNamespace(refresh=Mock()),
        refresh_sample_ui=Mock(),
        individual=None,
        individuals=[],
        sample_offset=3000,
        sample_seek_offset=lambda offset: offset + 0.125,
        sample_values={"behavior_ids": ["draft-behavior"], "carries": False},
    )
    session = AnimalCompanion(page)
    session.inline = SimpleNamespace(id=1, client=ClientStub(), set_source=Mock())
    session.inline_host = SimpleNamespace(set_visibility=Mock())
    session.remote = SimpleNamespace(id=2, client=ClientStub(), set_source=Mock())
    session.remote_client = session.remote.client
    session.detached = True
    session.source = "/animal.mp4"
    monkeypatch.setattr(player_module.ui, "run_javascript", Mock())
    monkeypatch.setattr("review_app.app.pages.distance_review.companion.configure_anchor", Mock())
    yield session
    await asyncio.sleep(0)
    _sessions.pop(session.token, None)


def test_companion_inspection_respects_main_modal_busy_and_generation_guards(companion):
    assert companion.accepts_inspection(companion.remote, companion.generation)
    assert not companion.accepts_inspection(companion.inline, companion.generation)
    assert not companion.accepts_inspection(companion.remote, companion.generation - 1)
    companion.page.busy = True
    assert not companion.accepts_inspection(companion.remote, companion.generation)
    companion.page.busy = False
    companion.page.client.elements["dialog"] = Mock(spec=ui.dialog, value=True)
    assert not companion.accepts_inspection(companion.remote, companion.generation)
    companion.page.client.elements.clear()
    companion.page.client.has_socket_connection = False
    assert not companion.accepts_inspection(companion.remote, companion.generation)


async def test_capture_reads_the_companion_not_the_inline_player(companion):
    assert await player_module.capture_offset(companion, exact=True) == 3120.125
    companion.remote.client.run_javascript.assert_awaited_once()
    assert "getElement(2)" in companion.remote.client.run_javascript.call_args.args[0]
    companion.inline.client.run_javascript.assert_not_called()
    player_module.ui.run_javascript.assert_not_called()


async def test_closed_companion_never_falls_back_to_hidden_inline_player(companion):
    companion.remote.client.has_socket_connection = False
    with pytest.raises(ValueError, match="distance_player_not_ready"):
        await player_module.capture_offset(companion, exact=True)
    companion.inline.client.run_javascript.assert_not_called()
    companion.remote.client.run_javascript.assert_not_called()
    assert companion.detached


@pytest.mark.parametrize("change", ["video", "disconnect"])
async def test_pending_commands_are_cancelled_when_the_surface_changes(companion, change):
    started = asyncio.Event()

    async def pending(*_, **__):
        started.set()
        await asyncio.Event().wait()

    companion.remote.client.run_javascript.side_effect = pending
    command = asyncio.create_task(player_module.play_to_sample(companion, 1000, 3000))
    await asyncio.wait_for(started.wait(), timeout=1)
    if change == "video":
        companion.advance_generation()
    else:
        companion.remote.client.has_socket_connection = False
        companion.cancel_pending()
    with pytest.raises(ValueError, match="distance_playback_interrupted"):
        await asyncio.wait_for(command, timeout=1)


@pytest.mark.parametrize("ready", [False, True])
async def test_attachment_transfers_player_only_after_target_is_ready(companion, ready):
    remote = companion.remote
    companion.detached = False
    companion.remote = companion.remote_client = None
    view = {"offset_ms": 3000.125, "brightness": 1.4, "scale": 1.5}
    companion.snapshot = AsyncMock(return_value=view)
    companion.restore = AsyncMock(return_value=ready)
    draft = companion.page.sample_values
    assert await companion.attach_remote(remote, companion.generation) is ready
    assert companion.detached is ready
    assert companion.active is (remote if ready else companion.inline)
    assert companion.page.sample_values is draft
    assert not companion.page.busy and not companion.transitioning
    if ready:
        companion.inline.set_source.assert_called_once_with("")
        companion.inline_host.set_visibility.assert_called_once_with(False)
    else:
        companion.inline.set_source.assert_not_called()


@pytest.mark.parametrize("ready", [False, True])
async def test_reattachment_preserves_drafts_and_only_switches_on_success(
    companion, monkeypatch, ready
):
    state = {"offset_ms": 5120.125, "brightness": 1.6, "scale": 2, "pan_x": -0.2}
    companion.snapshot = AsyncMock(return_value=state)
    companion.restore = AsyncMock(return_value=ready)
    monkeypatch.setattr("review_app.app.pages.distance_review.companion.ui.notify", Mock())
    # Fire-and-forget window-close notifications are ordinary synchronous calls.
    companion.remote.client.run_javascript = Mock()
    draft = companion.page.sample_values
    await companion.reattach()
    companion.restore.assert_awaited_once_with(companion.inline, state)
    assert companion.detached is not ready
    assert companion.page.sample_values is draft
    assert companion.page.sample_offset == 3000
    assert not companion.page.busy and not companion.transitioning
    if ready:
        assert companion.active is companion.inline
        companion.inline_host.set_visibility.assert_called_with(True)
    else:
        assert companion.active is companion.remote
        companion.inline_host.set_visibility.assert_called_with(False)
        companion.inline.set_source.assert_called_with("")


async def test_closed_window_reattaches_at_selected_sample_with_last_image_settings(companion):
    companion.snapshot = AsyncMock(return_value=None)
    companion.last_state = {"offset_ms": 9900, "brightness": 1.7, "scale": 2}
    companion.restore = AsyncMock(return_value=True)
    companion.remote.client.run_javascript = Mock()
    await companion.reattach()
    assert companion.restore.await_args.args[1] == {
        "offset_ms": 3000.125,
        "brightness": 1.7,
        "scale": 2,
    }
    assert not companion.detached


def test_popup_opens_inside_browser_click_handler_and_uses_per_session_window(companion):
    source = companion.open_script()
    assert "window.open(" in source
    assert "window.path_prefix" in source
    assert companion.token in source
    assert "companion.focus()" in source
    assert "emit({opened: !!companion})" in source
