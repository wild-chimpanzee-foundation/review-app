import asyncio


async def sync_with_progress(data_provider, progress=None, status=None):
    """
    Run sync_videos with real-time progress updates.

    Args:
        data_provider: LocalDataProvider instance
        progress: Optional ui.linear_progress element to update
        status: Optional ui.label element to update

    Returns:
        The result of sync_videos()
    """
    from nicegui import run

    sync_progress = {"current": 0, "total": 0, "filename": "", "done": False}

    def update_progress(current, total, filename):
        sync_progress["current"] = current
        sync_progress["total"] = total
        sync_progress["filename"] = filename

    async def poll_progress():
        while not sync_progress["done"]:
            if progress is not None and status is not None:
                if sync_progress["total"] > 0:
                    progress.value = sync_progress["current"] / sync_progress["total"]
                    status.text = f"Processing {sync_progress['current']}/{sync_progress['total']}: {sync_progress['filename']}"
                else:
                    status.text = f"Scanning: {sync_progress['filename']}"
            await asyncio.sleep(0.1)

    async def run_sync():
        result = await run.io_bound(data_provider.sync_videos, progress_callback=update_progress)
        sync_progress["done"] = True
        if progress is not None:
            progress.value = 1.0
        if status is not None:
            status.text = "Sync complete!"
        return result

    await asyncio.gather(poll_progress(), run_sync())
