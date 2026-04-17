from pathlib import Path

from nicegui import run, ui

from review_app.app.state import get_data_provider, set_data_provider
from review_app.backend.local_data_provider import LocalDataProvider


async def setup_overview():
    dp = get_data_provider()
    if not dp:
        config_path = Path("config.yaml")
        if config_path.exists():
            dp = LocalDataProvider(str(config_path))
            set_data_provider(dp)
        else:
            with ui.column().classes("w-full q-pa-lg items-center"):
                ui.label("Error: Data provider not initialized").classes("text-h6 text-red-600")
                ui.button("Set up", on_click=lambda: ui.navigate.to("/setup"), icon="settings")
            return

    if not await run.io_bound(dp.has_videos_in_db):
        sync_container = ui.column().classes("w-full q-pa-lg items-center")

        with sync_container:
            ui.label("Syncing videos...").classes("text-h5 q-mb-md")
            progress = ui.linear_progress(value=0, show_value=False).props("color=primary")
            status = ui.label("Starting...")

        def update_progress(current, total, filename):
            if total > 0:
                progress.value = current / total
                status.text = f"Processing {current}/{total}: {filename}"
            else:
                status.text = f"Scanning: {filename}"

        await run.io_bound(dp.sync_videos, progress_callback=update_progress)
        progress.value = 1.0
        ui.notify("Videos synced!", type="positive")
        sync_container.clear()
        with sync_container:
            ui.label("Videos synced! Loading overview...").classes("text-h5")
        ui.timer(0.5, lambda: ui.navigate.to("/overview"), once=True)
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
