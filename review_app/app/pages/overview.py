from nicegui import ui

from review_app.app.state import get_data_provider


def setup_overview():
    dp = get_data_provider()
    if not dp:
        ui.label("Error: Data provider not initialized")
        return

    stats = dp.get_overview_stats()

    with ui.column().classes("w-full max-w-6xl mx-auto p-4 gap-6"):
        ui.label("Overview").classes("text-2xl font-bold")

        v = stats.get("videos", {})
        l = stats.get("labeling", {})

        with ui.row().classes("w-full gap-4 flex-wrap"):
            with ui.card().classes("flex-grow"):
                ui.label("Total Videos").classes("text-lg font-semibold")
                ui.label(str(int(v.get("total", 0)))).classes("text-3xl")
            with ui.card().classes("flex-grow"):
                ui.label("Cameras").classes("text-lg font-semibold")
                ui.label(str(int(v.get("cameras", 0)))).classes("text-3xl")
            with ui.card().classes("flex-grow"):
                ui.label("Total Hours").classes("text-lg font-semibold")
                ui.label(f"{v.get('total_hours', 0):.1f}h").classes("text-3xl")
            with ui.card().classes("flex-grow"):
                ui.label("Labeled").classes("text-lg font-semibold")
                labeled = int(l.get("labeled", 0))
                total = int(l.get("total_videos", 1))
                pct = 100 * labeled / max(total, 1)
                ui.label(f"{labeled} ({pct:.0f}%)").classes("text-3xl")
            with ui.card().classes("flex-grow"):
                ui.label("Blank").classes("text-lg font-semibold")
                ui.label(str(int(l.get("blank", 0)))).classes("text-3xl")
            with ui.card().classes("flex-grow"):
                ui.label("Invalid").classes("text-lg font-semibold")
                ui.label(str(int(v.get("invalid", 0)))).classes("text-3xl text-red-600")

        with ui.card().classes("w-full"):
            ui.label("Species Observations").classes("text-lg font-semibold")
            species_counts = stats.get("species_counts", [])
            if species_counts:
                species_data = [
                    {"species": s["species"], "observations": s["observations"]}
                    for s in species_counts[:20]
                ]
                ui.bar_chart(
                    x="species",
                    y="observations",
                    data=species_data,
                    nrows=10,
                )
            else:
                ui.label("No manual observations yet").classes("text-gray-500")

        with ui.card().classes("w-full"):
            ui.label("Behavior Distribution").classes("text-lg font-semibold")
            behavior_counts = stats.get("behavior_counts", [])
            if behavior_counts:
                total_obs = sum(b["observations"] for b in behavior_counts)
                for b in behavior_counts:
                    pct = 100 * b["observations"] / max(total_obs, 1)
                    with ui.row().classes("w-full items-center"):
                        ui.label(b["behavior"]).classes("flex-grow")
                        ui.label(f"{b['observations']} ({pct:.1f}%)").classes(
                            "text-sm text-gray-600"
                        )
                        ui.linear_progress(value=pct / 100, show_value=False).props(
                            "color=primary"
                        )
            else:
                ui.label("No behavior data yet").classes("text-gray-500")

        with ui.card().classes("w-full"):
            ui.label("Per-Camera Summary").classes("text-lg font-semibold")
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
                ui.label("No camera data available").classes("text-gray-500")
