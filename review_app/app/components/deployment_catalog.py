"""Deployment cards and details for project footage."""

from collections import defaultdict
from urllib.parse import quote

from nicegui import ui

from review_app.app.components.deployment_thumbnail import render_deployment_thumbnail
from review_app.app.config import get_user_data_dir
from review_app.app.state import reset_filters, update_filters
from review_app.app.translations import t
from review_app.backend.deployments import DEPLOYMENT_FIELDS

_PAGE_SIZE = 24


def deployment_details_url(identifier):
    return f"/deployments?deployment={quote(identifier, safe='')}"


def render_deployment_clip_counts(total, references):
    if not total:
        ui.label(t("deployment_no_assigned_videos")).classes("text-caption text-grey-6")
        return
    with ui.row().classes("w-full gap-x-4 gap-y-1 text-caption"):
        ui.label(t("deployment_ordinary_count", count=total - references))
        ui.label(t("deployment_reference_count", count=references))


def render_deployment_catalog(inventory, videos, folders, *, add, review_pending):
    clips = defaultdict(list)
    for video in videos:
        clips[video["deployment_id"]].append(video)
    unavailable = {
        assignment["deployment"]
        for folder in folders
        if folder["ordinary_unavailable"]
        for assignment in folder["assignments"]
    }
    pending = [folder for folder in folders if folder["needs_attention"]]

    with ui.row().classes("w-full items-center gap-2"):
        search = (
            ui.input(t("deployment_search"))
            .props("outlined dense clearable debounce=200")
            .classes("grow")
        )
        if pending:
            ui.button(
                t("deployment_review_open"),
                icon="playlist_add_check",
                on_click=lambda: review_pending(pending),
            ).props("outline")
        ui.button(t("deployment_add"), icon="add", on_click=add)
        with ui.button(icon="more_horiz").props("flat").tooltip(t("deployment_tools")):
            with ui.menu():
                ui.menu_item(
                    t("deployment_tools"),
                    on_click=lambda: ui.navigate.to("/deployments?view=tools"),
                )

    groups = {
        "no_reference": [],
        "no_ordinary": [],
        "matched": [],
    }
    for row in inventory:
        footage = clips[row["id"]]
        references = sum(clip["reference"] for clip in footage)
        if not references:
            groups["no_reference"].append(row)
        elif references == len(footage):
            groups["no_ordinary"].append(row)
        else:
            groups["matched"].append(row)

    initial = next(
        (group for group in ("matched", "no_reference", "no_ordinary") if groups[group]),
        "matched",
    )
    with ui.tabs().props("align=left no-caps mobile-arrows").classes("w-full") as tabs:
        labels = {group: ui.tab(group).mark(f"deployment-group-{group}") for group in groups}
    tabs.set_value(initial)
    with ui.tab_panels(tabs, value=initial, animated=False).classes("w-full bg-transparent"):
        panels = {group: ui.tab_panel(group).classes("p-0") for group in groups}
    thumbnails_dir = get_user_data_dir() / "thumbnails"
    matches = {}

    def render_selected_group():
        group = tabs.value
        panel = panels[group]
        if panel.default_slot.children:
            return
        with panel:
            if matches[group]:
                _render_deployment_group(matches[group], clips, unavailable, thumbnails_dir)
            else:
                message = "deployment_catalog_empty_group"
                if not inventory:
                    message = "deployment_overview_empty"
                elif (search.value or "").strip():
                    message = "deployment_catalog_no_matches"
                ui.label(t(message)).classes("text-grey-6 p-4")

    def filter_groups():
        term = (search.value or "").strip().casefold()
        for group, deployments in groups.items():
            matches[group] = [
                row
                for row in deployments
                if any(
                    term in (row.get(field) or "").casefold()
                    for field in ("deployment_id", "name", "camera_id", "transect_id")
                )
            ]
            labels[group].set_label(f"{t(f'deployment_group_{group}')} ({len(matches[group])})")
            panels[group].clear()
        render_selected_group()

    search.on_value_change(filter_groups)
    tabs.on_value_change(render_selected_group)
    filter_groups()


def _render_deployment_group(deployments, clips, unavailable, thumbnails_dir):
    cards = ui.element("div").classes(
        "w-full p-4 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4"
    )
    pages = (len(deployments) + _PAGE_SIZE - 1) // _PAGE_SIZE
    pagination = ui.pagination(1, pages, direction_links=True).classes("p-4")
    pagination.set_visibility(pages > 1)

    def render_page():
        cards.clear()
        start = ((pagination.value or 1) - 1) * _PAGE_SIZE
        with cards:
            for row in deployments[start : start + _PAGE_SIZE]:
                footage = clips[row["id"]]
                sample = next(
                    (
                        item["video_path"]
                        for item in footage
                        if not item["reference"] and not item["missing"]
                    ),
                    None,
                ) or next((item["video_path"] for item in footage if not item["missing"]), None)
                target = deployment_details_url(row["id"])
                with (
                    ui.link(target=target)
                    .classes("w-full min-w-0 no-underline")
                    .mark(f"deployment-card-{row['id']}")
                ):
                    with ui.card().classes("w-full h-full gap-2 hover:ring-2 hover:ring-primary"):
                        render_deployment_thumbnail(thumbnails_dir, sample)
                        render_deployment_name(row)
                        render_deployment_clip_counts(
                            len(footage), sum(clip["reference"] for clip in footage)
                        )
                        if row["deployment_id"] in unavailable and all(
                            item["reference"] for item in footage
                        ):
                            ui.label(t("deployment_review_ordinary_unavailable")).classes(
                                "text-caption text-grey-6"
                            )

    pagination.on_value_change(render_page)
    render_page()


def render_deployment_name(row, *, title=False):
    name = row["name"] or row["deployment_id"]
    ui.label(name).classes(f"{'text-h5' if title else 'text-subtitle1'} break-all")
    if name != row["deployment_id"]:
        ui.label(row["deployment_id"]).classes("text-caption text-grey-6 break-all")


def render_deployment_details(deployment, videos, folders, links, roots, *, edit, review_folder):
    footage = [row for row in videos if row["deployment_id"] == deployment["id"]]

    def open_review():
        reset_filters()
        update_filters(selected_deployment=deployment["id"])
        ui.navigate.to("/review")

    with ui.row().classes("w-full items-start justify-between gap-2"):
        with ui.column().classes("min-w-0 gap-1"):
            render_deployment_name(deployment, title=True)
            render_deployment_clip_counts(len(footage), sum(clip["reference"] for clip in footage))
        with ui.row().classes("gap-2"):
            ui.button(t("deployment_edit"), icon="edit", on_click=edit).props("outline")
            ui.button(t("deployment_open_review"), icon="rate_review", on_click=open_review).props(
                "unelevated"
            ).set_enabled(bool(footage))

    fields = [(field, deployment[field]) for field in DEPLOYMENT_FIELDS[2:] if deployment[field]]
    if fields:
        with ui.element("div").classes(
            "w-full grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4"
        ):
            for field, value in fields:
                with ui.column().classes("min-w-0 gap-0"):
                    ui.label(t(f"deployment_field_{field}")).classes("text-caption text-grey-6")
                    ui.label(value).classes("whitespace-pre-wrap break-words")

    ui.label(t("deployment_catalog_folders")).classes("text-h6")
    members = {
        (link["root_id"], link["folder"]): (0, None)
        for link in links
        if link["deployment_id"] == deployment["id"]
    }
    for folder in folders:
        key = (folder["root_id"], folder["folder"])
        count = sum(
            assignment["count"]
            for assignment in folder["assignments"]
            if assignment["deployment"] == deployment["deployment_id"]
        )
        if count or key in members:
            members[key] = count, folder
    if not members:
        ui.label(t("deployment_catalog_no_folders")).classes("text-grey-6")
    for root_id, path in sorted(members, key=lambda key: (key[1].casefold(), key[0] or "")):
        count, folder = members[root_id, path]
        with ui.card().classes("w-full"):
            with ui.row().classes("w-full items-center justify-between gap-2"):
                with ui.column().classes("min-w-0 gap-0"):
                    ui.label(path).classes("font-medium break-all")
                    if len(roots) > 1 and root_id in roots:
                        ui.label(roots[root_id]).classes("text-caption text-grey-6 break-all")
                    ui.label(t("deployment_catalog_folder_clips", count=count)).classes(
                        "text-caption"
                    )
                    if folder and folder["review_status"]:
                        ui.label(folder["review_status"]).classes("text-caption")
                if folder:
                    ui.button(
                        t("deployment_catalog_review_folder"),
                        on_click=lambda row=folder: review_folder([row]),
                    ).props("flat")
