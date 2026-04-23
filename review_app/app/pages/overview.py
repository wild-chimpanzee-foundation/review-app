from nicegui import run, ui

from review_app.app.state import get_data_provider
from review_app.app.translations import get_language, t


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
        total = max(int(lb.get("total_videos", 1)), 1)
        labeled = int(lb.get("labeled", 0))

        # Stat cards
        with ui.row().classes("w-full q-col-gutter-md q-mb-lg"):
            stat_cards = [
                (t("stat_total_videos"), int(v.get("total", 0))),
                (t("stat_cameras"), int(v.get("cameras", 0))),
                (t("stat_hours"), f"{v.get('total_hours', 0):.1f}h"),
                (
                    t("stat_labeled"),
                    f"{labeled} ({100 * labeled / total:.0f}%)",
                ),
                (t("stat_blank"), int(lb.get("blank", 0))),
                (t("stat_invalid"), int(v.get("invalid", 0)), "text-negative"),
                (t("stat_unprobed"), int(v.get("unprobed", 0)), "text-warning"),
            ]
            for card_data in stat_cards:
                label, value = card_data[0], card_data[1]
                extra_class = card_data[2] if len(card_data) > 2 else ""
                with ui.card().classes("col text-center q-pa-md"):
                    ui.label(str(value)).classes(f"text-h5 font-weight-bold {extra_class}")
                    ui.label(label).classes("text-caption text-grey-6")

        # Annotation progress bar
        blank = int(lb.get("blank", 0))
        non_blank = int(lb.get("non_blank", 0))
        unlabeled = int(lb.get("unlabeled", 0))
        blank_pct = 100 * blank / total
        nonblank_pct = 100 * non_blank / total
        unlabeled_pct = 100 * unlabeled / total

        with ui.card().classes("full-width q-mb-lg"):
            with ui.row().classes("items-center q-pa-md q-pb-sm"):
                ui.label(t("annotation_progress")).classes("text-subtitle1 font-weight-medium")
            with ui.column().classes("w-full q-px-md q-pb-md gap-sm"):
                with ui.row().classes("w-full overflow-hidden").style(
                    "height:12px; border-radius:6px"
                ):
                    if blank_pct > 0:
                        ui.element("div").style(
                            f"width:{blank_pct:.1f}%; background:#4caf50; height:100%"
                        )
                    if nonblank_pct > 0:
                        ui.element("div").style(
                            f"width:{nonblank_pct:.1f}%; background:#2196f3; height:100%"
                        )
                    if unlabeled_pct > 0:
                        ui.element("div").style(
                            f"width:{unlabeled_pct:.1f}%; background:#e0e0e0; height:100%"
                        )
                with ui.row().classes("gap-lg"):
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
                    for s in species_counts[:10]:
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

        # Camera summary table
        with ui.expansion(t("camera_summary_title"), icon="photo_camera").classes(
            "full-width q-mb-lg"
        ).props("content-inset-level=0 header-class='q-pa-md q-py-sm'"):
            camera_summary = stats.get("camera_summary", [])
            if camera_summary:
                rows = [
                    {
                        **row,
                        "labeled_pct": f"{round(100 * row['labeled'] / max(row['total_videos'], 1))}%",
                    }
                    for row in camera_summary
                ]
                columns = [
                    {"name": "camera_id", "label": t("col_camera"), "field": "camera_id"},
                    {"name": "total", "label": t("col_total"), "field": "total_videos"},
                    {"name": "labeled", "label": t("col_labeled"), "field": "labeled"},
                    {"name": "labeled_pct", "label": t("col_labeled_pct"), "field": "labeled_pct"},
                    {"name": "blank", "label": t("col_blank"), "field": "blank"},
                    {"name": "hours", "label": t("col_hours"), "field": "hours"},
                ]
                ui.table(columns=columns, rows=rows).classes("q-pa-sm")
            else:
                ui.label(t("no_camera_data")).classes("text-grey-5 q-pa-sm")

        # Model annotations (only if data exists)
        model_coverage = stats.get("model_coverage", [])
        model_agreement = stats.get("model_human_agreement", [])
        if model_coverage:
            with ui.expansion(t("model_annotations"), icon="smart_toy").classes(
                "full-width q-mb-lg"
            ).props("content-inset-level=0 header-class='q-pa-md q-py-sm'"):
                with ui.column().classes("w-full gap-md q-pa-sm"):
                    with ui.card().classes("full-width"):
                        ui.label(t("col_coverage")).classes(
                            "text-subtitle2 font-weight-medium q-mb-sm"
                        )
                        coverage_cols = [
                            {"name": "model", "label": t("col_model"), "field": "model_name"},
                            {
                                "name": "covered",
                                "label": t("col_coverage"),
                                "field": "videos_covered",
                            },
                            {
                                "name": "avg_conf",
                                "label": t("col_avg_conf"),
                                "field": "avg_probability",
                            },
                        ]
                        coverage_rows = [
                            {
                                **r,
                                "avg_probability": f"{r['avg_probability']:.2f}"
                                if r.get("avg_probability") is not None
                                else "—",
                            }
                            for r in model_coverage
                        ]
                        ui.table(columns=coverage_cols, rows=coverage_rows)

                    if model_agreement:
                        with ui.card().classes("full-width"):
                            ui.label("Model-Human Agreement").classes(
                                "text-subtitle2 font-weight-medium q-mb-xs"
                            )
                            ui.label(
                                "For videos with both model and manual annotations, "
                                "counts how often the model's top species prediction matches "
                                "the manually labeled species."
                            ).classes("text-caption text-grey-6 q-mb-sm")
                            for row in model_agreement:
                                pct = row.get("agreement_pct") or 0
                                color = (
                                    "text-positive"
                                    if pct >= 80
                                    else "text-warning"
                                    if pct >= 50
                                    else "text-negative"
                                )
                                with ui.row().classes("w-full items-center q-mb-sm"):
                                    ui.label(row.get("model_name", "")).classes("col text-body2")
                                    ui.label(
                                        f"{row.get('agreed', 0)}/{row.get('compared', 0)}"
                                    ).classes("text-body2 text-grey-6 q-mr-md")
                                    ui.label(f"{pct:.0f}%").classes(f"text-body2 {color}")
