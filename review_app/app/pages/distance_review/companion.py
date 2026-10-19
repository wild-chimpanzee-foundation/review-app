"""Animal-video companion: a second player surface, never a second review session."""

from __future__ import annotations

import asyncio
import json
import secrets
import uuid
import weakref
from pathlib import Path
from typing import TYPE_CHECKING

from nicegui import ui
from nicegui.awaitable_response import AwaitableResponse

from review_app.app.pages.distance_review.player import (
    configure_anchor,
    install_anchor_editor,
    install_manual_pause_snap,
    pause_programmatically,
    playback_shortcuts,
    seek_paused,
    stop_player,
)
from review_app.app.pages.review.video_player import render_custom_video_player
from review_app.app.translations import t

if TYPE_CHECKING:
    from review_app.app.pages.distance_review.page import DistanceReviewPage

_sessions: weakref.WeakValueDictionary[str, AnimalCompanion] = weakref.WeakValueDictionary()


def _connected(client) -> bool:
    return bool(client and client.has_socket_connection and client.id in client.instances)


class AnimalCompanion:
    """Main-owned player target; all commands address exactly one active video."""

    def __init__(self, page: DistanceReviewPage):
        self.page = page
        self.token = secrets.token_urlsafe(32)
        self.generation = 0
        self.pending_changed = asyncio.Event()
        self.inline = self.inline_host = self.remote = self.remote_client = None
        self.remote_render = None
        self.detached = False
        self.transitioning = False
        self.source = ""
        self.last_state: dict = {}
        _sessions[self.token] = self
        page.client.on_disconnect(self.main_disconnected)

    @property
    def active(self):
        # Never fall back to an invisible inline video when a companion is lost.
        return self.remote if self.detached else self.inline

    @property
    def id(self):
        return self.active.id if self.active else -1

    def accepts_inspection(self, player, generation: int) -> bool:
        return bool(
            self.active is player
            and self.generation == generation
            and not self.page.busy
            and _connected(self.page.client)
            and not any(
                isinstance(element, ui.dialog) and element.value
                for element in self.page.client.elements.values()
            )
        )

    def cancel_pending(self):
        self.pending_changed.set()
        self.pending_changed = asyncio.Event()

    def advance_generation(self):
        self.generation += 1
        self.cancel_pending()

    def run_player_javascript(self, code: str, *, timeout: float = 1):
        target, generation, changed = self.active, self.generation, self.pending_changed

        def current():
            return target is not None and target is self.active and generation == self.generation

        def send():
            if current() and _connected(target.client):
                target.client.run_javascript(code, timeout=timeout)

        async def wait():
            if not current() or (self.detached and not _connected(target.client)):
                return None
            try:
                # NiceGUI's normal JS await can wait indefinitely for a closed
                # client's socket. Bound connection and response waits together.
                async with asyncio.timeout(timeout + 3):
                    await target.client.connected(timeout=3)
                    if not current():
                        return None

                    async def execute():
                        return await target.client.run_javascript(code, timeout=timeout)

                    result = asyncio.create_task(execute())
                    invalidated = asyncio.create_task(changed.wait())
                    try:
                        done, _ = await asyncio.wait(
                            [result, invalidated], return_when=asyncio.FIRST_COMPLETED
                        )
                        return result.result() if result in done and current() else None
                    finally:
                        result.cancel()
                        invalidated.cancel()
                        await asyncio.gather(result, invalidated, return_exceptions=True)
            except (TimeoutError, RuntimeError):
                return None

        return AwaitableResponse(send, wait)

    def set_source(self, source: str):
        self.source = source
        if self.active:
            self.active.set_source(source)

    def bind(self, player, host):
        """Rebind after a main-page/video refresh, retaining the companion window."""
        self.advance_generation()
        self.inline, self.inline_host, self.source = player, host, player.source
        if self.detached:
            host.set_visibility(False)
            player.set_source("")
            self.last_state = {}
            if _connected(self.remote_client):
                with self.remote_client:
                    self.remote_render.refresh()
        self.changed()

    def changed(self):
        if self.page.client.id not in self.page.client.instances:
            return
        with self.page.client:
            grid = getattr(self.page, "comparison_grid", None)
            if grid:
                grid.classes(
                    add="distance-grid-detached" if self.detached else "",
                    remove="" if self.detached else "distance-grid-detached",
                )
            self.page.render_companion_actions.refresh()

    def open_script(self) -> str:
        path = f"/distance-review/animal/{self.token}"
        return """() => {
            const url = (window.path_prefix || '') + __PATH__;
            const windows = window.__distanceCompanionWindows ||= new Map();
            let companion = windows.get(__NAME__);
            if (companion && !companion.closed) companion.focus();
            else {
                companion = window.open(url, __NAME__, 'popup,width=1100,height=800');
                if (companion) windows.set(__NAME__, companion);
            }
            emit({opened: !!companion});
        }""".replace("__PATH__", json.dumps(path)).replace(
            "__NAME__", json.dumps(f"distance-animal-{self.token}")
        )

    def opened(self, event):
        if not event.args.get("opened"):
            ui.notify(t("distance_companion_blocked"), type="warning")

    async def snapshot(self) -> dict | None:
        return await self.run_player_javascript(
            f"""const video = getElement({self.id})?.$el;
            if (!video || video.seeking || video.readyState < 2 || !video._reviewPlayer) return null;
            video._distancePlayback = (video._distancePlayback || 0) + 1;
            if (!video.paused) video._distanceSuppressedPauseEvents =
                (video._distanceSuppressedPauseEvents || 0) + 1;
            video.pause();
            return video._reviewPlayer.getState();""",
            timeout=3,
        )

    async def restore(self, player, state: dict) -> bool:
        if not _connected(player.client):
            return False
        try:
            async with asyncio.timeout(12):
                return await self._restore(player, state)
        except (TimeoutError, RuntimeError):
            return False

    async def _restore(self, player, state: dict) -> bool:
        return bool(
            await player.client.run_javascript(
                f"""const video = getElement({player.id})?.$el;
                if (!video) return false;
                const state = {json.dumps(state)};
                return await new Promise(resolve => {{
                    let settled = false, applied = false;
                    const finish = value => {{
                        if (settled) return;
                        settled = true;
                        clearTimeout(watchdog);
                        clearInterval(poll);
                        video.removeEventListener('loadeddata', apply);
                        video.removeEventListener('seeked', ready);
                        resolve(value);
                    }};
                    const ready = () => {{
                        if (!video.seeking && video.readyState >= 2 &&
                            Math.abs(video.currentTime * 1000 - state.offset_ms) < 1)
                            finish(true);
                    }};
                    const apply = () => {{
                        if (!video._reviewPlayer || video.readyState < 2) return;
                        if (!applied) {{
                            applied = true;
                            video._reviewPlayer.setState(state);
                        }}
                        ready();
                    }};
                    const watchdog = setTimeout(() => finish(false), 8000);
                    const poll = setInterval(apply, 50);
                    video.addEventListener('loadeddata', apply);
                    video.addEventListener('seeked', ready);
                    apply();
                }});""",
                timeout=10,
            )
        )

    async def attach_remote(self, player, generation: int):
        if self.page.busy or self.transitioning or generation != self.generation:
            return False
        self.transitioning = self.page.busy = True
        self.changed()
        try:
            state = await self.snapshot()
            if state is None:
                if not self.detached:
                    return False
                # Closed/reloaded companions recover at the selected review frame.
                state = dict(self.last_state)
                state["offset_ms"] = self.page.sample_seek_offset(self.page.sample_offset or 0)
            if not await self.restore(player, state) or generation != self.generation:
                return False
            old_remote = self.remote
            self.advance_generation()
            self.remote, self.remote_client = player, player.client
            self.detached = True
            self.last_state = state
            self.inline_host.set_visibility(False)
            self.inline.set_source("")
            if (
                old_remote
                and old_remote.client is not player.client
                and _connected(old_remote.client)
            ):
                old_remote.client.run_javascript("window.close()")
            return True
        except (TimeoutError, RuntimeError):
            return False
        finally:
            self.transitioning = self.page.busy = False
            self.changed()
            if _connected(self.page.client):
                with self.page.client:
                    self.page.refresh_sample_ui()

    async def reattach(self):
        if self.page.busy or self.transitioning or not self.detached:
            return
        self.transitioning = self.page.busy = True
        generation = self.generation
        self.changed()
        try:
            state = await self.snapshot()
            if state is None:
                state = dict(self.last_state)
                state["offset_ms"] = self.page.sample_seek_offset(self.page.sample_offset or 0)
            self.inline_host.set_visibility(True)
            self.inline.set_source(self.source)
            if not await self.restore(self.inline, state) or generation != self.generation:
                ui.notify(t("distance_player_not_ready"), type="warning")
                self.inline_host.set_visibility(False)
                self.inline.set_source("")
                return
            self.advance_generation()
            self.detached = False
            self.inline_host.set_visibility(True)
            if self.remote and _connected(self.remote.client):
                with self.remote.client:
                    stop_player(self.remote)
                self.remote.client.run_javascript("window.close()")
            self.remote = self.remote_client = self.remote_render = None
            with self.page.client:
                configure_anchor(self, self.page.individual, individuals=self.page.individuals)
        finally:
            self.transitioning = self.page.busy = False
            self.changed()
            if _connected(self.page.client):
                self.page.refresh_sample_ui()

    def main_disconnected(self):
        self.cancel_pending()
        if _connected(self.remote_client):
            with self.remote_client:
                pause_programmatically(self.remote)

    def dispose(self):
        self.advance_generation()
        _sessions.pop(self.token, None)
        if _connected(self.remote_client):
            self.remote_client.run_javascript("window.close()")
        self.remote = self.remote_client = self.remote_render = None


@ui.page("/distance-review/animal/{token}")
def animal_companion_page(token: str):
    """A capability-scoped view of an existing main tab, with no provider/review."""
    session = _sessions.get(token)
    if session is None or session.inline is None or session.page.video is None:
        ui.label(t("distance_companion_expired")).classes("text-warning")
        return
    page = session.page
    client = ui.context.client
    ui.add_css(Path(__file__).with_name("styles.css").read_text())
    ui.add_css("""
        .nicegui-content {height:100dvh;padding:4px;gap:4px;overflow:hidden}
        body {background:#0f172a}
        .distance-companion-player {flex:1;min-height:0}
        .distance-companion-player > [id^=vp-wrap-] {flex:1;min-height:0}
        .distance-companion-player .vp-video-container {flex:1 1 0;min-height:0}
        .distance-companion-player .vp-controls,
        .distance-companion-player details {flex-shrink:0}
    """)
    status = ui.label(t("distance_companion_connecting")).classes("text-caption text-grey-6")

    @ui.refreshable
    def render_player():
        generation = session.generation
        with ui.column().classes("w-full distance-player distance-companion-player gap-1") as host:
            player = render_custom_video_player(
                session.source,
                page.video.get("duration_sec") or 0,
                str(uuid.uuid4()),
                autoplay=False,
                keyboard=False,
                compact=True,
                image_state=session.last_state,
            )
            playback_shortcuts(host, player)

            async def manual_pause(event):
                if session.accepts_inspection(player, generation):
                    with page.client:
                        await page.on_manual_pause(event)

            async def anchor(event):
                if session.accepts_inspection(player, generation):
                    with page.client:
                        await page.save_anchor(event)

            install_manual_pause_snap(player, manual_pause)
            install_anchor_editor(
                player,
                anchor,
                page.individual,
                individuals=page.individuals,
                frame_rate=page.animal_frame_rate,
            )

            def remember(event):
                if session.active is player and isinstance(event.args, dict):
                    session.last_state = event.args

            event_name = f"distance-companion-state-{player.id}"
            ui.on(event_name, remember)
            ui.run_javascript(f"""
                let attempts = 0;
                const install = () => {{
                    const video = getElement({player.id})?.$el;
                    if (!video?._reviewPlayer) {{
                        if (++attempts < 100) setTimeout(install, 50);
                        return;
                    }}
                    const wrapper = video.closest('[id^="vp-wrap-"]');
                    let pending;
                    const report = () => {{
                        clearTimeout(pending);
                        pending = setTimeout(() => {{
                            if (video.isConnected)
                                emitEvent({json.dumps(event_name)}, video._reviewPlayer.getState());
                        }}, 100);
                    }};
                    for (const event of ['timeupdate', 'seeked', 'pause']) video.addEventListener(event, report);
                    for (const event of ['input', 'wheel', 'pointerup']) wrapper.addEventListener(event, report);
                }};
                install();
            """)

            if session.detached and session.remote_client is client:
                # Main changed videos: replace only the remote player, not its window.
                session.remote = player
                seek_paused(player, page.sample_seek_offset(page.sample_offset or 0))
                player.on(
                    "loadedmetadata",
                    lambda: seek_paused(player, page.sample_seek_offset(page.sample_offset or 0)),
                )
            else:
                host.props("inert")

                async def connect():
                    nonlocal generation
                    await client.connected(timeout=10)
                    if session is not _sessions.get(token):
                        return
                    if await session.attach_remote(player, generation):
                        generation = session.generation
                        session.remote_render = render_player
                        host.props(remove="inert")
                        status.set_text(t("distance_companion_active"))
                    else:
                        status.set_text(t("distance_companion_unavailable"))
                        stop_player(player)
                        client.run_javascript("window.close()")
                        with page.client:
                            ui.notify(t("distance_player_not_ready"), type="warning")

                # Defer until the browser has constructed the shared player controls.
                ui.timer(0.25, connect, once=True)

    render_player()

    def disconnected():
        if session.remote_client is client:
            session.cancel_pending()
            if session.token in _sessions:
                session.changed()  # Reattachment remains explicit in the main tab.

    client.on_disconnect(disconnected)
    client.on_connect(disconnected)
