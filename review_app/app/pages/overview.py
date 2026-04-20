
from nicegui import run, ui

from review_app.app.state import get_data_provider
from review_app.app.utils import sync_with_progress


async def setup_overview():
    dp = get_data_provider()
    if not dp:
        with ui.column().classes("w-full q-pa-lg items-center"):
            ui.label("Error: Data provider not initialized").classes("text-h6 text-red-600")
            ui.button("Set up", on_click=lambda: ui.navigate.to("/setup"), icon="settings")
        return

    if not await run.io_bound(dp.has_videos_in_db):
        with ui.column().classes("w-full q-pa-lg items-center"):
            ui.label("No videos in database").classes("text-h5 q-mb-sm")
            ui.label("Sync videos when you're ready.").classes("text-body2 text-grey-7 q-mb-md")

            sync_dialog = ui.dialog()

            async def run_sync():
                sync_dialog.clear()
                with sync_dialog, ui.card().classes("q-pa-lg"):
                    ui.label("Syncing videos...").classes("text-h6 q-mb-md")
                    progress = ui.linear_progress(value=0, show_value=False).props("color=primary")
                    status = ui.label("Starting...")

                sync_dialog.open()
                await sync_with_progress(dp, progress=progress, status=status)
                ui.notify("Videos synced!", type="positive")
                sync_dialog.close()
                ui.navigate.to("/overview")

            ui.button("Sync Videos", icon="sync", color="primary", on_click=run_sync)
        return

    stats = await run.io_bound(dp.get_overview_stats)

    with ui.column().classes("w-full q-pa-lg"):
        with ui.row().classes("items-center q-mb-lg"):
            ui.label("Overview").classes("text-h5 text-primary font-weight-bold")

        v = stats.get("videos", {})
        lb = stats.get("labeling", {})

        with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
            stat_cards = [
                ("Total Videos", int(v.get("total", 0))),
                ("Cameras", int(v.get("cameras", 0))),
                ("Hours", f"{v.get('total_hours', 0):.1f}h"),
                (
                    "Labeled",
                    f"{int(lb.get('labeled', 0))} ({100 * int(lb.get('labeled', 0)) / max(int(lb.get('total_videos', 1)), 1):.0f}%)",
                ),
                ("Blank", int(lb.get("blank", 0))),
                ("Invalid", int(v.get("invalid", 0)), "text-negative"),
            ]
            for i, card_data in enumerate(stat_cards):
                label, value = card_data[0], card_data[1]
                extra_class = card_data[2] if len(card_data) > 2 else ""
                with ui.card().classes("col text-center q-pa-md"):
                    ui.label(str(value)).classes(f"text-h5 font-weight-bold {extra_class}")
                    ui.label(label).classes("text-caption text-grey-6")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-md"):
                ui.label("Species Observations").classes("text-subtitle1 font-weight-medium")
            species_counts = stats.get("species_counts", [])
            if species_counts:
                for s in species_counts[:10]:
                    with ui.row().classes("w-full items-center q-mb-sm"):
                        ui.label(s["species"]).classes("col text-body2")
                        ui.label(str(s["observations"])).classes("text-body2 text-grey-7")
            else:
                ui.label("No manual observations yet").classes("text-grey-5")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-md"):
                ui.label("Behavior Distribution").classes("text-subtitle1 font-weight-medium")
            behavior_counts = stats.get("behavior_counts", [])
            if behavior_counts:
                total_obs = sum(b["observations"] for b in behavior_counts)
                for b in behavior_counts:
                    pct = 100 * b["observations"] / max(total_obs, 1)
                    with ui.row().classes("w-full items-center q-mb-sm"):
                        ui.label(b["behavior"]).classes("col text-body2")
                        ui.label(f"{b['observations']} ({pct:.0f}%)").classes(
                            "text-caption text-grey-6 q-mr-sm"
                        )
                        ui.linear_progress(value=pct / 100, show_value=False).props(
                            "color=primary style=height: 6px"
                        )
            else:
                ui.label("No behavior data yet").classes("text-grey-5")

        with ui.card().classes("full-width"):
            with ui.row().classes("items-center q-mb-md"):
                ui.label("Per-Camera Summary").classes("text-subtitle1 font-weight-medium")
            camera_summary = stats.get("camera_summary", [])
            if camera_summary:
                columns = [
                    {"name": "camera_id", "label": "Camera", "field": "camera_id"},
                    {"name": "total", "label": "Total", "field": "total_videos"},
                    {"name": "labeled", "label": "Labeled", "field": "labeled"},
                    {"name": "hours", "label": "Hours", "field": "hours"},
                ]
                ui.table(columns=columns, rows=camera_summary)
            else:
                ui.label("No camera data available").classes("text-grey-5")
