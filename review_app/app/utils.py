import asyncio

from review_app.app.translations import t


async def sync_with_progress(data_provider, progress=None, status=None, video_dir=None):
    """
    Run sync_videos in a thread that is independent of NiceGUI client context.

    Uses run_in_executor directly instead of run.io_bound so the background
    thread survives page navigation and new tabs being opened mid-sync.
    """
    from pathlib import Path as _Path
    loop = asyncio.get_event_loop()
    sync_progress = {"current": 0, "total": 0, "filename": ""}

    def update_progress(current, total, filename):
        sync_progress["current"] = current
        sync_progress["total"] = total
        sync_progress["filename"] = filename

    dir_path = _Path(video_dir) if video_dir else None
    future = loop.run_in_executor(
        None,
        lambda: data_provider.sync_videos(progress_callback=update_progress, video_dir=dir_path),
    )

    while not future.done():
        if progress is not None and status is not None:
            total = sync_progress["total"]
            current = sync_progress["current"]
            filename = sync_progress["filename"]
            if total > 0:
                progress.value = current / total
                status.text = t("sync_processing", current=current, total=total, filename=filename)
            elif filename:
                status.text = t("scanning_file", filename=filename)
        await asyncio.sleep(0.15)

    if status is not None:
        status.text = t("sync_complete")

    return future.result()


def render_uninitialized_state():
    from nicegui import ui

    from review_app.app.translations import t
    with ui.column().classes("w-full q-pa-lg items-center"):
        ui.label(t("error_dp_init")).classes("text-h6 text-red-600")
        ui.button(t("setup_btn"), on_click=lambda: ui.navigate.to("/setup"), icon="settings")


async def get_or_create_data_provider():
    from review_app.app.config import get_config_path
    from review_app.app.state import get_data_provider, set_data_provider
    from review_app.backend.local_data_provider import LocalDataProvider

    dp = get_data_provider()
    if not dp:
        config_path = get_config_path()
        if config_path.exists():
            try:
                dp = LocalDataProvider(str(config_path))
                set_data_provider(dp)
            except Exception:
                return None
    return dp
