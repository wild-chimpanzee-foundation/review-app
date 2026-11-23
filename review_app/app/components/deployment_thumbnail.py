"""Cached thumbnails shared by deployment views."""

import asyncio
import hashlib
from pathlib import Path

from nicegui import background_tasks, run, ui

from review_app.backend.path_matching import normalize_path_str
from review_app.backend.utils import generate_thumbnail

# Keep thumbnail work from occupying every IO worker.
_THUMBNAIL_JOBS = asyncio.Semaphore(2)


def render_deployment_thumbnail(thumbnails_dir: Path, sample_path: str | None) -> None:
    with ui.element("div").classes(
        "relative w-full aspect-video rounded bg-grey-3 dark:bg-grey-9 grid place-items-center"
    ):
        placeholder = ui.icon("videocam_off").classes("text-grey-6")
        if not sample_path:
            return
        img = ui.image("").classes("absolute inset-0 w-full h-full rounded").props("loading=lazy")
        img.set_visibility(False)

    filename = hashlib.sha256(sample_path.encode()).hexdigest() + ".jpg"
    thumb_path = thumbnails_dir / filename

    def show_thumbnail():
        img.set_source(f"/thumbnails/{filename}?v={int(thumb_path.stat().st_mtime)}")
        img.set_visibility(True)
        placeholder.set_visibility(False)

    async def load_thumbnail():
        async with _THUMBNAIL_JOBS:
            if img.is_deleted:
                return
            await run.io_bound(
                generate_thumbnail, Path(normalize_path_str(sample_path)), thumb_path
            )
            if not img.is_deleted and thumb_path.exists():
                show_thumbnail()

    if thumb_path.exists():
        show_thumbnail()
    else:
        background_tasks.create(load_thumbnail())
