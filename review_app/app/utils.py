import asyncio

from review_app.app.translations import t


async def sync_with_progress(data_provider, progress=None, status=None):
    """
    Run sync_videos in a thread that is independent of NiceGUI client context.

    Uses run_in_executor directly instead of run.io_bound so the background
    thread survives page navigation and new tabs being opened mid-sync.
    """
    loop = asyncio.get_event_loop()
    sync_progress = {"current": 0, "total": 0, "filename": ""}

    def update_progress(current, total, filename):
        sync_progress["current"] = current
        sync_progress["total"] = total
        sync_progress["filename"] = filename

    future = loop.run_in_executor(
        None,
        lambda: data_provider.sync_videos(progress_callback=update_progress),
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
