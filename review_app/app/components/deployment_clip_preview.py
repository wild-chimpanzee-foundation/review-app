"""Playback for deployment comparison and individual clip inspection."""

from pathlib import Path
from uuid import uuid4

from nicegui import app, run, ui

from review_app.app.components.playback import PlaybackConversion, playback_source
from review_app.app.translations import t
from review_app.backend.path_matching import normalize_path_str

_FRAME = "w-full rounded-borders bg-grey-10 overflow-hidden"
_FRAME_STYLE = "aspect-ratio:16/9"


def clip_placeholder(message="", *, loading=False):
    with (
        ui.column()
        .classes(f"{_FRAME} items-center justify-center gap-2 text-grey-4")
        .style(_FRAME_STYLE)
    ):
        if loading:
            ui.spinner(size="md")
        else:
            ui.icon("movie", size="sm")
        if message:
            ui.label(message).classes("text-body2 text-center q-px-md")


def _resolve_source(detail, root_path):
    playback = playback_source(detail)
    path = playback.path.resolve() if playback.path else None
    source_available = bool(
        root_path
        and path
        and path.is_relative_to(Path(normalize_path_str(root_path)).resolve())
        and path.is_file()
    )
    return playback, path, source_available


async def render_deployment_clip(dp, video_id, detail, root_path, host, *, is_current=None):
    def active():
        return not host.is_deleted and (is_current is None or is_current())

    playback, path, source_available = await run.io_bound(_resolve_source, detail, root_path)
    if not active():
        return
    host.clear()

    def render_player(path):
        player_slot.clear()
        with player_slot:
            # A clip can appear in two panes. Each player owns its own streaming route.
            url = app.add_media_file(local_file=path, url_path=f"/deployment-media/{uuid4().hex}")
            player = ui.video(url).classes(_FRAME).style(f"{_FRAME_STYLE};object-fit:contain")
            player.props('preload="auto" playsinline')
            player.auto_route = url
            return player

    def render_converted(path):
        render_player(path).on("error", lambda: ui.notify(t("video_load_error"), type="warning"))

    async def converted(path):
        render_converted(path)

    conversion = None
    with host:
        player_slot = ui.column().classes("w-full")
        if source_available and not playback.converted:
            conversion = PlaybackConversion(dp, video_id, converted, is_current=active)
        if playback.converted:
            render_converted(path)
        elif conversion and not playback.needs_conversion:
            conversion.watch_original(render_player(path))
        else:
            with player_slot:
                clip_placeholder("" if source_available else t("deployment_media_offline"))

    if conversion and playback.needs_conversion:
        await conversion.offer()


async def show_deployment_clip(dp, video_id, root_path):
    detail = await run.io_bound(dp.get_video_detail, video_id)
    if detail is None:
        ui.notify(t("deployment_media_offline"), type="warning")
        return

    dialog = ui.dialog()
    dialog.on("hide", dialog.delete)
    with dialog, ui.card().classes("q-pa-md").style("width:1000px; max-width:95vw"):
        ui.label(detail["video_path"]).classes("break-all")
        host = ui.column().classes("w-full")
        ui.button(t("close"), on_click=dialog.close).props("flat")
    dialog.open()
    await render_deployment_clip(
        dp, video_id, detail, root_path, host, is_current=lambda: dialog.value
    )
