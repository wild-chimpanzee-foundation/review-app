"""Isolated real-browser harness; never reads or writes the user's workspace."""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

from nicegui import app, ui
from review_app.app import config
from review_app.app.pages.distance_review import page as page_module
from review_app.app.pages.distance_review.page import DistanceReviewPage
from review_app.backend.db import backup
from review_app.backend.provider import local_data_provider

root, port = Path(sys.argv[1]), int(sys.argv[2])
(root / "db").mkdir(exist_ok=True)
(root / "species.csv").write_text("scientific_name;english_name\ndeer;Red Deer\n")
(root / "behaviors.csv").write_text("scientific_name;key;name_en;name_fr\n*;grazing;Grazing;\n")
config.get_bundled_species_csv = lambda: str(root / "species.csv")
config.get_bundled_behaviors_csv = lambda: str(root / "behaviors.csv")
config.get_user_data_dir = lambda: root / "db"
local_data_provider.get_user_data_dir = lambda: root / "db"
page_module.get_user_data_dir = lambda: root / "db"
backup.get_user_data_dir = lambda: root / "db"
dp = local_data_provider.LocalDataProvider()
project = dp.create_project("Companion browser test", str(root / "videos"))
dp.sync_videos(video_dir=root / "videos", active_project_id=project.id)
reference_video = next(
    video
    for video in dp.get_distance_video_queue(project.id)
    if Path(video["video_path"]).name == "reference.mp4"
)
dp.set_video_tags(reference_video["video_id"], ["reference_video"])
reference = dp.register_reference_video(project.id, "cam_x", reference_video["video_path"])
dp.add_reference_frame(reference["id"], 0, 5)
dp.add_reference_frame(reference["id"], 2000, 10)
species = dp.get_distance_species_options(project.id)
for video in dp.get_distance_video_queue(project.id):
    dp.align_distance_on_even_frame(video["video_id"], 1000, "alice")
    dp.create_distance_individual(
        video["video_id"],
        next(iter(species)),
        0,
        anchor={"x": 0.1, "y": 0.1, "width": 0.4, "height": 0.4},
        labeled_by="alice",
    )
holder = {}


@ui.page("/")
async def main():
    app.storage.user["annotator_name"] = "alice"
    ui.add_css(Path("review_app/app/pages/distance_review/styles.css").read_text())
    page = DistanceReviewPage(dp, project.id, species, dp.get_all_tags())
    page.behavior_options = dp.get_distance_behavior_options()
    await page.load_queue()
    await page.load_video()
    page.right_drawer = ui.right_drawer(value=True).props("width=350 mini-width=72")
    with ui.left_drawer(value=True).props("width=240"), ui.column().classes("w-full"):
        page.render_filters()
    with ui.column().classes("distance-workspace"):
        page.render_workspace()
    with page.right_drawer, ui.column().classes("distance-sidebar"):
        page.render_sidebar_toggle()
        page.render_sidebar()
    event_name = f"test-sample-nav-{uuid.uuid4().hex}"

    @page.client_handler
    async def navigate(event):
        await page.move_sample(-1 if event.args["direction"] == "previous" else 1)

    ui.on(event_name, navigate)
    ui.run_javascript(
        Path("review_app/app/pages/distance_review/keyboard.js")
        .read_text()
        .replace("__DISTANCE_SAMPLE_NAV_EVENT__", event_name)
    )
    holder["page"] = page


@app.get("/test-state")
def state():
    page = holder.get("page")
    if not page:
        return {}
    return {
        "busy": page.busy,
        "detached": page.animal_companion.detached,
        "sample_offset": page.sample_offset,
        "selected_frame_id": page.selected_frame_id,
        "video_id": page.video["video_id"],
        "draft": page.sample_values,
        "samples": dp.get_distance_review_state(page.video["video_id"], "alice")["samples"],
    }


ui.run(host="127.0.0.1", port=port, reload=False, show=False, storage_secret="companion-test-only")
