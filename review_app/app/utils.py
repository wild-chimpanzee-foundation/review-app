import asyncio

from review_app.app.translations import t


def user_error_message(exc: Exception) -> str:
    if hasattr(exc, "user_message_key"):
        return t(exc.user_message_key)
    return str(exc)


async def sync_with_progress(
    data_provider, progress=None, status=None, video_dir=None, active_project_id=None
):
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
        lambda: data_provider.sync_videos(
            progress_callback=update_progress,
            video_dir=dir_path,
            active_project_id=active_project_id,
        ),
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

    if progress is not None:
        progress.value = 1.0
    if status is not None:
        status.text = t("sync_complete")

    return future.result()


def render_uninitialized_state():
    from nicegui import ui

    from review_app.app.translations import t

    with ui.column().classes("w-full q-pa-lg items-center"):
        ui.label(t("error_dp_init")).classes("text-h6 text-red-600")
        ui.button(t("setup_btn"), on_click=lambda: ui.navigate.to("/setup"), icon="settings")


def switch_project(dp, project_id: str) -> None:
    """Update all session state when activating a project. Caller handles navigation."""
    from pathlib import Path

    from review_app.app.media import set_media_dirs
    from review_app.app.state import (
        reset_filters,
        set_active_project,
        set_current_idx,
        set_queue,
        set_selections,
    )

    dp.touch_project(project_id)
    set_active_project(project_id)
    reset_filters()
    set_queue([])
    set_current_idx(0)
    set_selections([])

    dirs = dp.get_project_dirs(project_id) or []
    missing = [d.path for d in dirs if not Path(d.path).exists()]
    set_media_dirs([Path(d.path) for d in dirs])
    return missing


async def get_or_create_data_provider():
    from review_app.app.config import get_default_db_path
    from review_app.app.state import get_data_provider, set_data_provider
    from review_app.backend.local_data_provider import LocalDataProvider

    dp = get_data_provider()
    if not dp:
        if get_default_db_path().exists():
            try:
                dp = LocalDataProvider()
                set_data_provider(dp)
            except Exception:
                return None
    return dp


def get_probability_color(prob: float) -> str:
    """
    Map a probability (0.0 to 1.0) to a continuous hex color scale.
    Red (#c10015) -> Yellow (#f2c037) -> Green (#21ba45)
    """
    if prob is None:
        return "#9e9e9e"  # grey

    # Ensure prob is within [0, 1]
    try:
        prob = float(prob)
    except (ValueError, TypeError):
        return "#9e9e9e"

    prob = max(0.0, min(1.0, prob))

    # We interpolate between:
    # 0.0: Red    (193, 0, 21)   #c10015
    # 0.5: Yellow (242, 192, 55) #f2c037
    # 1.0: Green  (33, 186, 69)  #21ba45

    if prob < 0.5:
        # Interpolate Red to Yellow
        t = prob / 0.5
        r = int(193 + (242 - 193) * t)
        g = int(0 + (192 - 0) * t)
        b = int(21 + (55 - 21) * t)
    else:
        # Interpolate Yellow to Green
        t = (prob - 0.5) / 0.5
        r = int(242 + (33 - 242) * t)
        g = int(192 + (186 - 192) * t)
        b = int(55 + (69 - 55) * t)

    return f"#{r:02x}{g:02x}{b:02x}"
