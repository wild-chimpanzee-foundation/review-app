"""Shared source selection and conversion controls for review footage."""

from dataclasses import dataclass
from pathlib import Path

from nicegui import run, ui

from review_app.app.components.video_tools import VideoToolsWarning
from review_app.app.state import get_state_val, is_auto_transcode, set_state_val
from review_app.app.translations import t
from review_app.app.utils import user_error_message
from review_app.backend.path_matching import normalize_path_str
from review_app.backend.transcoding import inspect_transcoded_cache
from review_app.backend.utils import needs_browser_transcode
from review_app.backend.video_tools import check_video_tools


@dataclass(frozen=True)
class PlaybackSource:
    path: Path | None
    converted: bool = False
    needs_conversion: bool = False


def playback_source(video: dict, *, validate_contents: bool = True) -> PlaybackSource:
    cached_path = video.get("transcoded_path")
    cache = inspect_transcoded_cache(cached_path, validate_contents=validate_contents)
    if cache.usable or (cache.current and not validate_contents):
        return PlaybackSource(Path(cached_path), converted=True)
    original_path = video.get("video_path")
    if original_path:
        known_incompatible = video.get("needs_transcode")
        return PlaybackSource(
            Path(normalize_path_str(original_path)),
            needs_conversion=(
                cache.exists
                or (
                    needs_browser_transcode(video)
                    if known_incompatible is None
                    else known_incompatible
                )
            ),
        )
    return PlaybackSource(None)


def watch_playback_errors(player, on_error):
    async def failed(event):
        if not player.is_deleted:
            # Browser media decode failure or unsupported source.
            await on_error(event.args in (3, 4))

    player.on("error", failed, js_handler="event => emit(event.target.error?.code)")


class PlaybackConversion:
    """Offer a conversion and hand the validated playback path to the current view."""

    def __init__(self, provider, video_id, on_converted, *, is_current=None, on_busy=None):
        self.provider = provider
        self.video_id = video_id
        self.on_converted = on_converted
        self.is_current = is_current
        self.on_busy = on_busy
        self.busy = False
        with ui.column().classes("w-full gap-2") as self.host:
            self.status = ui.label(t("playback_conversion_needed")).classes("text-caption")
            with ui.row().classes("items-center gap-2") as self.progress:
                ui.spinner()
                ui.label(t("transcoding_video"))
            self.progress.set_visibility(False)
            self.warning = ui.column().classes("w-full")
            self.button = ui.button(
                t("transcode_for_playback"), icon="movie", on_click=self.convert
            )
        self.host.set_visibility(False)

    def active(self):
        return not self.host.is_deleted and (self.is_current is None or self.is_current())

    async def offer(self, *, automatic=True) -> bool:
        if not self.active():
            return False
        self.host.set_visibility(True)
        attempted = get_state_val("transcode_attempted_ids", [])
        if automatic and is_auto_transcode() and self.video_id not in attempted:
            set_state_val("transcode_attempted_ids", [*attempted, self.video_id])
            return await self.convert()
        return False

    def watch_original(self, player):
        async def failed(convertible):
            if self.busy or not self.active():
                return
            self.status.set_text(t("video_load_error"))
            await self.offer(automatic=convertible)

        watch_playback_errors(player, failed)

    async def convert(self) -> bool:
        if self.busy or not self.active():
            return False
        self.busy = True
        self.button.disable()
        self.warning.clear()
        self.status.set_text("")
        self.progress.set_visibility(True)
        if self.on_busy:
            self.on_busy(True)
        try:
            tools = await run.io_bound(check_video_tools)
            if not self.active():
                return False
            if not tools.available:
                with self.warning:
                    VideoToolsWarning().show(tools.missing)
                return False
            result = await run.io_bound(self.provider.transcode_video, self.video_id)
            if not self.active():
                return False
            if result.success and result.new_path:
                await self.on_converted(Path(result.new_path))
                if self.active():
                    self.host.set_visibility(False)
                return True
            else:
                self.status.set_text(
                    t("transcode_failed", error=result.error or t("video_error_generic"))
                )
        except Exception as exc:
            if self.active():
                self.status.set_text(t("transcode_failed", error=user_error_message(exc)))
        finally:
            self.busy = False
            if self.on_busy:
                self.on_busy(False)
            if self.active():
                self.progress.set_visibility(False)
                self.button.enable()
        return False
