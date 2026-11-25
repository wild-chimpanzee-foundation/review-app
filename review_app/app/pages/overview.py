from pathlib import Path

from nicegui import run, ui

from review_app.app.components.deployment_catalog import (
    deployment_details_url,
    render_deployment_clip_counts,
    render_deployment_name,
)
from review_app.app.components.deployment_inventory import open_deployment_editor
from review_app.app.components.deployment_thumbnail import render_deployment_thumbnail
from review_app.app.components.location_map import MapMarker, render_location_map
from review_app.app.state import get_active_project_id, get_language, reset_filters, update_filters
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    render_uninitialized_state,
)
from review_app.backend.path_matching import normalize_path_str

_BLANK_COLOR = "#4caf50"
_NON_BLANK_COLOR = "#2196f3"
_UNLABELED_COLOR = "#e0e0e0"


def _render_annotation_bar(
    blank: int, non_blank: int, unlabeled: int, height: str = "12px"
) -> None:
    total = max(blank + non_blank + unlabeled, 1)
    segments = [
        (_BLANK_COLOR, 100 * blank / total),
        (_NON_BLANK_COLOR, 100 * non_blank / total),
        (_UNLABELED_COLOR, 100 * unlabeled / total),
    ]
    with ui.element("div").style(
        f"display:flex; width:100%; height:{height}; border-radius:6px; overflow:hidden"
    ):
        for color, flex in segments:
            if flex > 0:
                ui.element("div").style(f"flex:{flex:.3f}; background:{color}; height:100%")


async def _render_deployment_overview(dp, pid, footage, go_review) -> None:
    from review_app.app.config import get_user_data_dir

    thumbnails_dir = get_user_data_dir() / "thumbnails"
    with ui.card().classes("w-full q-pa-md q-mb-lg"):
        with ui.row().classes("w-full items-center justify-between"):
            ui.label(t("deployment_overview_title")).classes("text-subtitle1 font-weight-medium")
            ui.link(t("deployment_view_all"), "/deployments")
        host = ui.column().classes("w-full")

    async def refresh(_identifier=None):
        inventory = await run.io_bound(dp.get_project_deployments, pid) if pid and footage else []
        if host.is_deleted:
            return
        by_id = {row["id"]: row for row in inventory}
        host.clear()
        with host:
            if not footage:
                ui.label(t("deployment_no_assigned_videos")).classes("text-grey-5")
                return
            with ui.element("div").classes("w-full overflow-x-auto"):
                with ui.row().classes("no-wrap gap-md q-pb-sm items-stretch"):
                    for group in footage:
                        deployment_id = group["deployment_id"]
                        deployment = by_id.get(deployment_id)
                        total = group["total_videos"]
                        labeled_pct = round(100 * group.get("labeled", 0) / max(total, 1))
                        with ui.card().classes("deployment-card shrink-0").style("width: 300px"):
                            sample_path = group.get("sample_video_path")
                            if deployment:
                                with ui.link(target=deployment_details_url(deployment_id)).classes(
                                    "w-full no-underline flex flex-col gap-3 rounded-lg"
                                ):
                                    render_deployment_thumbnail(thumbnails_dir, sample_path)
                                    render_deployment_name(deployment)
                            else:
                                render_deployment_thumbnail(thumbnails_dir, sample_path)
                                ui.label(t("deployment_unresolved")).classes(
                                    "text-body2 font-medium"
                                )
                            render_deployment_clip_counts(total, group.get("reference_videos", 0))
                            ui.linear_progress(value=labeled_pct / 100, show_value=False).props(
                                "color=primary style=height:4px"
                            )
                            with ui.row().classes(
                                "w-full justify-between text-caption text-grey-7"
                            ):
                                ui.label(f"{labeled_pct}% {t('col_labeled').lower()}")
                                ui.label(f"{group.get('hours', 0):.1f}h")
                            with ui.row().classes("gap-xs"):
                                if total:
                                    ui.button(
                                        t("deployment_open_review"),
                                        icon="rate_review",
                                        on_click=lambda did=deployment_id: go_review(
                                            selected_deployment=did or "unresolved"
                                        ),
                                    ).props("flat dense no-caps")
                                if deployment:
                                    ui.button(
                                        t("deployment_edit"),
                                        icon="edit",
                                        on_click=lambda row=deployment: open_deployment_editor(
                                            dp, pid, row, on_saved=refresh
                                        ),
                                    ).props("flat dense no-caps")

    await refresh()


async def setup_overview():
    from review_app.app.entry_point import shared_header

    dp = await get_or_create_data_provider()
    if not dp:
        shared_header()
        render_uninitialized_state()
        return

    shared_header()

    pid = get_active_project_id()

    def go_review(**filters):
        reset_filters()
        update_filters(**filters)
        ui.navigate.to("/review")

    if not await run.io_bound(dp.has_videos_in_db, pid):
        with ui.column().classes("w-full q-pa-lg items-center"):
            ui.label(t("no_videos_synced")).classes("text-h6 text-grey-6")
            ui.label(t("no_videos_synced_hint")).classes("text-body2 text-grey-6")
            ui.button(
                t("go_to_settings"), on_click=lambda: ui.navigate.to("/settings"), icon="settings"
            )
        if pid:
            with ui.column().classes("w-full q-pa-lg").style("max-width: 1600px; margin: 0 auto"):
                await _render_deployment_overview(dp, pid, [], go_review)
        return

    deployment_summary = await run.io_bound(dp.get_deployment_summary, pid) if pid else None
    stats, locations = (
        await run.io_bound(dp.get_overview_stats, pid),
        await run.io_bound(dp.get_video_locations, pid),
    )
    if stats is None:
        return

    with ui.column().classes("w-full q-pa-lg").style("max-width: 1600px; margin: 0 auto"):
        bindings = await run.io_bound(dp.list_assignments, pid)
        working = next((row for row in bindings if row["role"] == "working"), None)
        if working:
            from review_app.app.components.assignment_handoff import assignment_identity

            with ui.card().classes("w-full q-pa-md q-mb-md"):
                ui.label(assignment_identity(working)).classes("text-subtitle1 font-medium")
                ui.label(t("handoff_working_help")).classes("text-caption text-grey-6")
                with ui.row():
                    ui.button(
                        t("data_continue_review"),
                        icon="play_arrow",
                        on_click=lambda: ui.navigate.to(
                            "/distance-review" if working["stage"] == "distance" else "/review"
                        ),
                    ).props("unelevated")
                    ui.button(
                        t("handoff_return"),
                        icon="outbox",
                        on_click=lambda: ui.navigate.to("/data-assignments?area=assignments"),
                    ).props("outline")
        v = stats.get("videos", {})
        lb = stats.get("labeling", {})
        total = max(int(lb.get("total_videos", 1)), 1)
        labeled = int(lb.get("labeled", 0))
        review_later = int(lb.get("review_later", 0))
        with ui.row().classes("items-center justify-between q-mb-lg"):
            ui.label(t("overview_title")).classes("text-h5 text-primary font-weight-bold")
            with ui.row().classes("gap-2"):
                ui.button(
                    t("data_results"),
                    icon="ios_share",
                    on_click=lambda: ui.navigate.to("/data-assignments?area=results"),
                ).props("flat")
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

        if deployment_summary and deployment_summary["has_pending_deployment_issues"]:
            with ui.card().classes("w-full q-pa-md q-mb-md"):
                with ui.row().classes("w-full justify-between items-center"):
                    ui.label(
                        t("deployment_count", count=deployment_summary["deployments"])
                    ).classes("text-subtitle1")
                    with ui.row().classes("items-center gap-2"):
                        if deployment_summary["has_pending_reference_reviews"]:
                            ui.button(
                                t("deployment_review_references"),
                                icon="playlist_add_check",
                                on_click=lambda: ui.navigate.to("/deployments?review=references"),
                            )
                        ui.button(
                            t("deployment_view"),
                            icon="list_alt",
                            on_click=lambda: ui.navigate.to("/deployments"),
                        ).props("outline")
                ui.label(t("deployment_summary", **deployment_summary)).classes(
                    "text-caption text-grey-6"
                )

        # Stat cards
        with ui.row().classes("w-full gap-md q-mb-lg"):
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
            with ui.row().classes("w-full gap-md q-mb-lg"):
                with ui.card().classes("col q-pa-md"):
                    with (
                        ui.expansion(
                            t("missing_videos_banner", n=missing_count),
                            icon="warning",
                        )
                        .classes("full-width q-mb-lg text-warning")
                        .props("header-class='text-warning'")
                    ):
                        # Cap the list: an unplugged drive can mean tens of thousands of
                        # missing videos, and one DOM element each hangs the browser.
                        _MAX_MISSING_SHOWN = 200
                        with ui.scroll_area().style("max-height: 300px"):
                            with ui.column().classes("q-pa-sm gap-xs"):
                                for _, mv in missing_videos_df.head(_MAX_MISSING_SHOWN).iterrows():
                                    ui.label(
                                        str(Path(normalize_path_str(mv["video_path"])))
                                    ).classes("text-body2 text-mono text-grey-8")
                                if missing_count > _MAX_MISSING_SHOWN:
                                    ui.label(
                                        t(
                                            "missing_videos_more",
                                            n=missing_count - _MAX_MISSING_SHOWN,
                                        )
                                    ).classes("text-body2 text-grey-6 text-italic")

        # Annotation progress bar
        blank = int(lb.get("blank", 0))
        non_blank = int(lb.get("non_blank", 0))
        unlabeled = max(total - blank - non_blank, 0)

        with ui.row().classes("w-full gap-md q-mb-lg"):
            with ui.card().classes("col q-pa-md"):
                ui.label(t("annotation_progress")).classes(
                    "text-subtitle1 font-weight-medium q-mb-md"
                )
                _render_annotation_bar(blank, non_blank, unlabeled)
                with ui.row().classes("gap-lg q-mt-md"):
                    for color, label, count in [
                        (_BLANK_COLOR, t("progress_blank"), blank),
                        (_NON_BLANK_COLOR, t("progress_non_blank"), non_blank),
                        (_UNLABELED_COLOR, t("progress_unlabeled"), unlabeled),
                    ]:
                        with ui.row().classes("items-center gap-xs"):
                            ui.element("div").style(
                                f"width:10px; height:10px; border-radius:50%; background:{color}; flex-shrink:0"
                            )
                            ui.label(f"{label}: {count}").classes("text-body2")

        # Species and behaviors (side by side)
        species_map = await run.io_bound(dp.get_species_display_map, get_language())

        with ui.row().classes("w-full gap-md q-mb-lg"):
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
                            with (
                                ui.row()
                                .classes(
                                    "w-full items-center q-mb-sm cursor-pointer rounded q-px-xs"
                                )
                                .style("transition: background 0.15s")
                                .on("click", lambda sn=sci_name: go_review(selected_species=[sn]))
                                .on(
                                    "mouseover",
                                    lambda e: e.sender.style("background: rgba(0,0,0,0.06)"),
                                )
                                .on(
                                    "mouseout", lambda e: e.sender.style("background: transparent")
                                )
                            ):
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
                        with (
                            ui.row()
                            .classes("w-full items-center q-mb-sm cursor-pointer rounded q-px-xs")
                            .style("transition: background 0.15s")
                            .on(
                                "click", lambda bv=b["behavior"]: go_review(selected_behavior=[bv])
                            )
                            .on(
                                "mouseover",
                                lambda e: e.sender.style("background: rgba(0,0,0,0.06)"),
                            )
                            .on("mouseout", lambda e: e.sender.style("background: transparent"))
                        ):
                            ui.label(b["behavior"]).classes("col text-body2")
                            ui.label(f"{b['observations']} ({pct:.0f}%)").classes(
                                "text-caption text-grey-6 q-mr-sm"
                            )
                            ui.linear_progress(value=pct / 100, show_value=False).props(
                                "color=primary style=height: 6px"
                            )
                else:
                    ui.label(t("no_behavior_yet")).classes("text-grey-5")

        await _render_deployment_overview(dp, pid, stats["deployment_footage"], go_review)

        # Assignment summary
        with ui.row().classes("w-full gap-md q-mb-lg"):
            with ui.card().classes("col q-pa-md"):
                with ui.row().classes("w-full justify-between items-center q-mb-md"):
                    ui.label(t("assignment_summary_title")).classes(
                        "text-subtitle1 font-weight-medium"
                    )
                    with ui.row().classes("gap-lg"):
                        for color, label in [
                            (_BLANK_COLOR, t("progress_blank")),
                            (_NON_BLANK_COLOR, t("progress_non_blank")),
                            (_UNLABELED_COLOR, t("progress_unlabeled")),
                        ]:
                            with ui.row().classes("items-center gap-xs"):
                                ui.element("div").style(
                                    f"width:8px; height:8px; border-radius:50%; background:{color}; flex-shrink:0"
                                )
                                ui.label(label).classes("text-caption text-grey-6")
                assignment_summary = await run.io_bound(dp.get_assignment_summary, pid)
                if assignment_summary:
                    with ui.column().classes("w-full gap-xs"):
                        for r in assignment_summary:
                            r_blank = int(r["blank"])
                            r_non_blank = int(r["labeled"]) - r_blank
                            r_unlabeled = max(int(r["video_count"]) - int(r["labeled"]), 0)
                            labeled_pct = round(100 * r["labeled"] / max(r["video_count"], 1))
                            with (
                                ui.column()
                                .classes("w-full q-px-sm q-py-xs cursor-pointer rounded")
                                .style("transition: background 0.15s")
                                .on(
                                    "click",
                                    lambda an=r["annotator"]: go_review(selected_annotator=[an]),
                                )
                                .on(
                                    "mouseover",
                                    lambda e: e.sender.style("background: rgba(0,0,0,0.06)"),
                                )
                                .on(
                                    "mouseout", lambda e: e.sender.style("background: transparent")
                                )
                            ):
                                with ui.row().classes(
                                    "w-full justify-between items-baseline q-mb-xs"
                                ):
                                    ui.label(r["annotator"] or "—").classes(
                                        "text-body2 font-weight-medium"
                                    )
                                    ui.label(
                                        f"{labeled_pct}% {t('col_labeled').lower()} · "
                                        f"{r['video_count']} {t('col_videos').lower()} · "
                                        f"{r['cameras']} {t('col_cameras').lower()} · "
                                        f"{r['hours']:.1f}h"
                                    ).classes("text-caption text-grey-6")
                                _render_annotation_bar(
                                    r_blank, r_non_blank, r_unlabeled, height="8px"
                                )
                else:
                    ui.label(t("no_camera_data")).classes("text-grey-5")
        # Location map
        if locations:
            with ui.row().classes("w-full gap-md q-mb-lg"):
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
