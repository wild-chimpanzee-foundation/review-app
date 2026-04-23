from nicegui import run, ui

from review_app.app.state import get_data_provider
from review_app.app.translations import t


async def setup_overview():
    dp = get_data_provider()
    if not dp or not await run.io_bound(dp.has_videos_in_db):
        with ui.column().classes("w-full q-pa-lg items-center"):
            ui.label(t("error_dp_init")).classes("text-h6 text-red-600")
            ui.button(t("setup_btn"), on_click=lambda: ui.navigate.to("/setup"), icon="settings")
        return

    stats = await run.io_bound(dp.get_overview_stats)

    with ui.column().classes("w-full q-pa-lg"):
        with ui.row().classes("items-center q-mb-lg"):
            ui.label(t("overview_title")).classes("text-h5 text-primary font-weight-bold")

        v = stats.get("videos", {})
        lb = stats.get("labeling", {})

        with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
            stat_cards = [
                (t("stat_total_videos"), int(v.get("total", 0))),
                (t("stat_cameras"), int(v.get("cameras", 0))),
                (t("stat_hours"), f"{v.get('total_hours', 0):.1f}h"),
                (
                    t("stat_labeled"),
                    f"{int(lb.get('labeled', 0))} ({100 * int(lb.get('labeled', 0)) / max(int(lb.get('total_videos', 1)), 1):.0f}%)",
                ),
                (t("stat_blank"), int(lb.get("blank", 0))),
                (t("stat_invalid"), int(v.get("invalid", 0)), "text-negative"),
            ]
            for i, card_data in enumerate(stat_cards):
                label, value = card_data[0], card_data[1]
                extra_class = card_data[2] if len(card_data) > 2 else ""
                with ui.card().classes("col text-center q-pa-md"):
                    ui.label(str(value)).classes(f"text-h5 font-weight-bold {extra_class}")
                    ui.label(label).classes("text-caption text-grey-6")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-md"):
                ui.label(t("species_obs_title")).classes("text-subtitle1 font-weight-medium")
            species_counts = stats.get("species_counts", [])
            if species_counts:
                for s in species_counts[:10]:
                    with ui.row().classes("w-full items-center q-mb-sm"):
                        ui.label(s["species"]).classes("col text-body2")
                        ui.label(str(s["observations"])).classes("text-body2 text-grey-7")
            else:
                ui.label(t("no_obs_yet")).classes("text-grey-5")

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-mb-md"):
                ui.label(t("behavior_dist_title")).classes("text-subtitle1 font-weight-medium")
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
                ui.label(t("no_behavior_yet")).classes("text-grey-5")

        with ui.card().classes("full-width"):
            with ui.row().classes("items-center q-mb-md"):
                ui.label(t("camera_summary_title")).classes("text-subtitle1 font-weight-medium")
            camera_summary = stats.get("camera_summary", [])
            if camera_summary:
                columns = [
                    {"name": "camera_id", "label": t("col_camera"), "field": "camera_id"},
                    {"name": "total", "label": t("col_total"), "field": "total_videos"},
                    {"name": "labeled", "label": t("col_labeled"), "field": "labeled"},
                    {"name": "hours", "label": t("col_hours"), "field": "hours"},
                ]
                ui.table(columns=columns, rows=camera_summary)
            else:
                ui.label(t("no_camera_data")).classes("text-grey-5")
