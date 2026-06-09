from nicegui import run, ui

from review_app.app.components.location_map import MapMarker, render_location_map
from review_app.app.state import get_active_project_id, get_language, reset_filters, update_filters
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    render_uninitialized_state,
)


async def setup_overview():
    from review_app.app.entry_point import shared_header

    dp = await get_or_create_data_provider()
    if not dp:
        shared_header()
        render_uninitialized_state()
        return

    shared_header()

    pid = get_active_project_id()
    if not await run.io_bound(dp.has_videos_in_db, pid):
        with ui.column().classes("w-full q-pa-lg items-center"):
            ui.label(t("no_videos_synced")).classes("text-h6 text-grey-6")
            ui.label(t("no_videos_synced_hint")).classes("text-body2 text-grey-6")
            ui.button(
                t("go_to_settings"), on_click=lambda: ui.navigate.to("/settings"), icon="settings"
            )
        return

    stats, locations = (
        await run.io_bound(dp.get_overview_stats, pid),
        await run.io_bound(dp.get_video_locations, pid),
    )
    if stats is None:
        return

    def go_review(**filters):
        reset_filters()
        update_filters(**filters)
        ui.navigate.to("/review")

    with ui.column().classes("w-full q-pa-lg").style("max-width: 1600px; margin: 0 auto"):
        v = stats.get("videos", {})
        lb = stats.get("labeling", {})
        total = max(int(lb.get("total_videos", 1)), 1)
        labeled = int(lb.get("labeled", 0))
        review_later = int(lb.get("review_later", 0))
        with ui.row().classes("items-center justify-between q-mb-lg"):
            ui.label(t("overview_title")).classes("text-h5 text-primary font-weight-bold")
            with ui.row().classes("gap-2"):
                ui.button(
                    t("quick_review_unannotated"),
                    icon="rate_review",
                    color="primary",
                    on_click=lambda: go_review(
                        selected_annotation_status="Not Annotated",
                        selected_needs_review="Needs Review",
                    ),
                ).props("outline")
                if review_later:
                    ui.button(
                        t("quick_review_later"),
                        icon="bookmark",
                        color="warning",
                        on_click=lambda: go_review(selected_is_review_later=True),
                    ).props("outline")

        # Stat cards
        with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
            stat_cards = [
                (t("stat_total_videos"), int(v.get("total", 0))),
                (t("stat_cameras"), int(v.get("cameras", 0))),
                (t("stat_hours"), f"{v.get('total_hours', 0):.1f}h"),
                (t("stat_labeled"), f"{labeled} ({100 * labeled / total:.0f}%)"),
                (t("stat_blank"), int(lb.get("blank", 0))),
                (t("review_later"), review_later, "text-orange" if review_later else ""),
                (t("stat_invalid"), int(v.get("invalid", 0)), "text-negative"),
                (t("stat_unprobed"), int(v.get("unprobed", 0)), "text-warning"),
            ]
            for card_data in stat_cards:
                label, value = card_data[0], card_data[1]
                extra_class = card_data[2] if len(card_data) > 2 else ""
                with ui.card().classes("col text-center q-pa-md"):
                    ui.label(str(value)).classes(f"text-h5 font-weight-bold {extra_class}")
                    ui.label(label).classes("text-caption text-grey-6")

        # Missing videos banner
        missing_videos_df = stats.get("missing_videos")
        missing_count = 0 if missing_videos_df is None else len(missing_videos_df)
        if missing_count:
            with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
                with ui.card().classes("col q-pa-md"):
                    with (
                        ui.expansion(
                            t("missing_videos_banner", n=missing_count),
                            icon="warning",
                        )
                        .classes("full-width q-mb-lg text-warning")
                        .props("header-class='text-warning'")
                    ):
                        with ui.scroll_area().style("max-height: 300px"):
                            with ui.column().classes("q-pa-sm gap-xs"):
                                for _, mv in missing_videos_df.iterrows():
                                    ui.label(mv["video_path"]).classes(
                                        "text-body2 text-mono text-grey-8"
                                    )

        # Annotation progress bar
        blank = int(lb.get("blank", 0))
        non_blank = int(lb.get("non_blank", 0))
        unlabeled = max(total - blank - non_blank, 0)
        blank_pct = 100 * blank / total
        nonblank_pct = 100 * non_blank / total
        unlabeled_pct = 100 * unlabeled / total

        with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
            with ui.card().classes("col q-pa-md"):
                ui.label(t("annotation_progress")).classes(
                    "text-subtitle1 font-weight-medium q-mb-md"
                )
                with ui.element("div").style(
                    "display:flex; width:100%; height:12px; border-radius:6px; overflow:hidden"
                ):
                    if blank_pct > 0:
                        ui.element("div").style(
                            f"flex:{blank_pct:.3f}; background:#4caf50; height:100%"
                        )
                    if nonblank_pct > 0:
                        ui.element("div").style(
                            f"flex:{nonblank_pct:.3f}; background:#2196f3; height:100%"
                        )
                    if unlabeled_pct > 0:
                        ui.element("div").style(
                            f"flex:{unlabeled_pct:.3f}; background:#e0e0e0; height:100%"
                        )
                with ui.row().classes("gap-lg q-mt-md"):
                    for color, label, count in [
                        ("#4caf50", t("progress_blank"), blank),
                        ("#2196f3", t("progress_non_blank"), non_blank),
                        ("#e0e0e0", t("progress_unlabeled"), unlabeled),
                    ]:
                        with ui.row().classes("items-center gap-xs"):
                            ui.element("div").style(
                                f"width:10px; height:10px; border-radius:50%; background:{color}; flex-shrink:0"
                            )
                            ui.label(f"{label}: {count}").classes("text-body2")

        # Species and behaviors (side by side)
        species_map = await run.io_bound(dp.get_species_display_map, get_language())

        with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
            with ui.card().classes("col q-pa-md"):
                ui.label(t("species_obs_title")).classes(
                    "text-subtitle1 font-weight-medium q-mb-md"
                )
                species_counts = stats.get("species_counts", [])
                if species_counts:
                    with ui.scroll_area().style("max-height: 300px"):
                        for s in species_counts:
                            sci_name = s["species"]
                            common_name = species_map.get(sci_name, sci_name)
                            with ui.row().classes("w-full items-center q-mb-sm"):
                                ui.label(common_name).classes("col text-body2")
                                ui.label(str(s["observations"])).classes("text-body2 text-grey-7")
                else:
                    ui.label(t("no_obs_yet")).classes("text-grey-5")

            with ui.card().classes("col q-pa-md"):
                ui.label(t("behavior_dist_title")).classes(
                    "text-subtitle1 font-weight-medium q-mb-md"
                )
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

        # Location map
        if locations:
            with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
                with ui.card().classes("col q-pa-md"):
                    ui.label(t("location_map_title")).classes(
                        "text-subtitle1 font-weight-medium q-mb-md"
                    )
                    map_markers = [
                        MapMarker(
                            lat=loc["latitude"],
                            lon=loc["longitude"],
                            label=(
                                f"{loc['camera_id']}: {int(loc['video_count'])} video(s)"
                                if loc.get("camera_id")
                                else f"{int(loc['video_count'])} video(s)"
                            ),
                        )
                        for loc in locations
                    ]
                    render_location_map(map_markers)

        # Camera cards
        with (
            ui.expansion(t("camera_summary_title"), icon="photo_camera")
            .classes("full-width q-mb-lg")
            .props("content-inset-level=0 header-class='q-pa-md q-py-sm'")
        ):
            camera_summary = stats.get("camera_summary", [])
            if camera_summary:
                with ui.scroll_area().style("width: 100%"):
                    with ui.row().classes("no-wrap gap-md q-pb-sm"):
                        for cam in camera_summary:
                            labeled_pct = round(100 * cam["labeled"] / max(cam["total_videos"], 1))
                            cam_id = cam["camera_id"]
                            with (
                                ui.card()
                                .classes("q-pa-md cursor-pointer")
                                .style("min-width: 160px; max-width: 180px")
                                .on("click", lambda c=cam_id: go_review(selected_camera=c))
                            ):
                                ui.label(cam_id or t("col_camera")).classes(
                                    "text-body2 font-weight-medium ellipsis"
                                ).style("max-width: 150px")
                                ui.linear_progress(
                                    value=labeled_pct / 100, show_value=False
                                ).props("color=primary style=height:4px").classes("q-my-xs")
                                with ui.row().classes("w-full justify-between items-center"):
                                    ui.label(f"{labeled_pct}% {t('col_labeled').lower()}").classes(
                                        "text-caption text-grey-6"
                                    )
                                with ui.row().classes("w-full justify-between"):
                                    ui.label(
                                        f"{cam['total_videos']} {t('col_total').lower()}"
                                    ).classes("text-caption text-grey-5")
                                    ui.label(f"{cam['hours']:.1f}h").classes(
                                        "text-caption text-grey-5"
                                    )
            else:
                ui.label(t("no_camera_data")).classes("text-grey-5")

        # Assignment summary table
        with (
            ui.expansion(t("assignment_summary_title"), icon="assignment_ind")
            .classes("full-width q-mb-lg")
            .props("content-inset-level=0 header-class='q-pa-md q-py-sm'")
        ):
            assignment_summary = await run.io_bound(dp.get_assignment_summary, pid)
            if assignment_summary:
                rows = [
                    {
                        **r,
                        "labeled_pct": f"{round(100 * r['labeled'] / max(r['video_count'], 1))}%",
                    }
                    for r in assignment_summary
                ]
                columns = [
                    {"name": "annotator", "label": t("annotator_label"), "field": "annotator"},
                    {"name": "cameras", "label": t("col_cameras"), "field": "cameras"},
                    {"name": "videos", "label": t("col_videos"), "field": "video_count"},
                    {"name": "labeled", "label": t("col_labeled"), "field": "labeled"},
                    {"name": "labeled_pct", "label": t("col_labeled_pct"), "field": "labeled_pct"},
                    {"name": "blank", "label": t("col_blank"), "field": "blank"},
                    {"name": "hours", "label": t("col_hours"), "field": "hours"},
                ]
                ui.table(columns=columns, rows=rows).classes("q-pa-sm")
            else:
                ui.label(t("no_camera_data")).classes("text-grey-5 q-pa-sm")
