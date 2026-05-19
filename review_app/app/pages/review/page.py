import asyncio
import logging
import uuid
from pathlib import Path
from urllib.parse import quote

from nicegui import run, ui

from review_app.app.onboarding import show_info_dialog, show_tour_if_needed
from review_app.app.pages.review.annotations import render_annotation_section
from review_app.app.pages.review.filters import render_filter_drawer  # noqa: F401 (refreshable)
from review_app.app.pages.review.tags import render_video_tags
from review_app.app.pages.review.video_player import SPEED_OPTIONS, render_custom_video_player
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_blank_threshold,
    get_current_idx,
    get_filters,
    get_language,
    get_obj_detection_threshold,
    get_queue,
    get_species_threshold,
    get_state_val,
    is_auto_transcode,
    is_tour_completed,
    set_current_idx,
    set_queue,
    set_selections,
    set_state_val,
)
from review_app.app.translations import t
from review_app.app.utils import (
    get_or_create_data_provider,
    get_probability_color,
    render_uninitialized_state,
)
from review_app.backend.utils import df_to_records

logger = logging.getLogger(__name__)


def navigate(direction: int):
    queue = get_queue()
    new_idx = get_current_idx() + direction
    if 0 <= new_idx < len(queue):
        navigate_to(new_idx)


def navigate_to(idx: int):
    from nicegui import context as ui_context

    queue = get_queue()
    idx = max(0, min(idx, len(queue) - 1))
    # Debounce to prevent rapid overlap
    token = str(uuid.uuid4())
    set_state_val("nav_token", token)
    set_state_val("is_loading", True)
    client = ui_context.client  # capture before task loses slot context

    async def _do_nav():
        await asyncio.sleep(0.1)
        if get_state_val("nav_token") == token:
            set_current_idx(idx)
            set_state_val("review_active_id", None)
            set_state_val("pending_blank_confirm", False)
            set_state_val("review_state_video_id", None)
            set_state_val("review_is_blank", None)
            set_selections([])
            set_state_val("is_loading", False)
            if queue and idx < len(queue):
                video_id = queue[idx]
                client.run_javascript(f"history.pushState(null, '', '/review?v={video_id}')")
            render_video_section.refresh()

    asyncio.create_task(_do_nav())


def _render_no_videos_match():
    filters = get_filters()
    label_map = {
        "search_query": t("search"),
        "selected_camera": t("camera_filter"),
        "selected_possible_species_group": f"{t('filter_section_model')} · {t('group_label')}",
        "selected_possible_species": t("species_model_filter"),
        "selected_model_blank": t("model_blank_filter"),
        "selected_needs_review": t("needs_review_filter"),
        "selected_species_group": f"{t('filter_section_manual')} · {t('group_label')}",
        "selected_species": t("species_manual_filter"),
        "selected_behavior": t("manual_behavior_filter"),
        "selected_manual_blank": t("manual_blank_filter"),
        "selected_annotation_status": t("annotation_filter"),
        "selected_annotator": t("annotator_filter"),
        "selected_tags": t("tag_filter"),
        "selected_is_review_later": t("review_later_filter"),
        "selected_multiple_annotators": t("multiple_annotators_filter"),
        "web_safe_only": t("web_safe_only"),
    }
    active = []
    for key, label in label_map.items():
        val = filters.get(key)
        if val in ("All", "", None, False, "camera"):
            continue
        if isinstance(val, list) and not val:
            continue
        if isinstance(val, str):
            display = val
        elif isinstance(val, list):
            display = ", ".join(str(v) for v in val)
        else:
            display = str(val)
        active.append((label, display))

    with ui.column().classes("items-center q-pa-xl gap-md"):
        ui.icon("filter_list_off", size="48px").classes("text-grey-5")
        ui.label(t("no_videos_match")).classes("text-h6 text-grey-5")
        if active:
            with ui.card().classes("q-pa-md").style("min-width: 320px; max-width: 520px"):
                ui.label(t("active_filters")).classes("text-caption text-grey-5 q-mb-xs")
                for label, display in active:
                    with ui.row().classes("items-start gap-xs w-full"):
                        ui.label(f"{label}:").classes("text-caption text-grey-5").style(
                            "min-width: 140px; flex-shrink: 0"
                        )
                        ui.label(display).classes("text-caption text-weight-medium")


def _render_ai_annotations(model_ann, global_species_map):
    if model_ann is None or model_ann.empty:
        return

    def _is_number(v: str) -> bool:
        try:
            float(v)
            return True
        except (TypeError, ValueError):
            return False

    groups: dict = {}

    for _row in df_to_records(model_ann, 20):
        _ann_type = _row.get("annotation_type", "")
        _value = _row.get("value_text", "") or ""
        _prob = _row.get("probability")
        _vnum = _row.get("value_num")

        if _ann_type == "blank_non_blank":
            threshold = get_blank_threshold() or 0.0
            if _is_number(_value):
                _value = t("non_blank") if float(_value) < threshold else t("blank")
            elif _prob is not None:
                _value = t("non_blank") if _prob < threshold else t("blank")
            else:
                _value = t("blank") if str(_value).lower() == "blank" else t("non_blank")
        elif _ann_type in {"species", "object_detection"}:
            _value = global_species_map.get(_value, _value)

        _color = get_probability_color(_prob) if _prob is not None else "grey"

        if _ann_type not in groups:
            groups[_ann_type] = {}
        if _value not in groups[_ann_type]:
            groups[_ann_type][_value] = []

        groups[_ann_type][_value].append(
            {
                "model": _row.get("model_name", ""),
                "prob": _prob,
                "color": _color,
                "count": _vnum,
            }
        )
        groups[_ann_type][_value].sort(
            key=lambda x: (x["prob"] is not None, x["prob"]), reverse=True
        )

        group_order = ["species", "blank_non_blank"]
        groups = dict(
            sorted(
                groups.items(),
                key=lambda x: group_order.index(x[0]) if x[0] in group_order else len(group_order),
            )
        )

    with ui.card().classes(
        "full-width q-mt-xs q-pa-none bg-transparent no-shadow tour-target-ai-predictions"
    ):
        rename_map = {
            "species": t("species_annotations"),
            "blank_non_blank": t("blank_annotations"),
            "object_detection": t("object_detection_annotations"),
        }
        first_ann_type = True
        for _ann_type, _predictions in groups.items():
            ann_type_display = rename_map.get(_ann_type, _ann_type.capitalize().replace("_", " "))
            with ui.row().classes("items-center gap-xs q-mt-xs q-mb-none"):
                ui.label(ann_type_display).classes("text-micro  text-italic")
                if first_ann_type:
                    ui.button(
                        icon="info_outline",
                        on_click=lambda: show_info_dialog(
                            t("info_ai_predictions_title"),
                            t("info_ai_predictions_body"),
                        ),
                    ).props("flat round dense size=xs color=grey-6")
                    first_ann_type = False
            with ui.column().classes("w-full gap-y-1"):
                for _val, _models in sorted(
                    _predictions.items(),
                    key=lambda x: (
                        sum(m["prob"] for m in x[1] if m["prob"] is not None)
                        / max(sum(1 for m in x[1] if m["prob"] is not None), 1),
                        len(x[1]),
                    ),
                    reverse=True,
                ):
                    with ui.row().classes(
                        "w-full items-center justify-between q-pa-xs rounded-borders bg-white/5 border border-white/5"
                    ):
                        with ui.row().classes("items-center gap-x-2"):
                            _display_val = _val
                            _count = _models[0].get("count")
                            if _ann_type == "object_detection" and _count is not None and _count > 0:
                                _count_str = (
                                    f"{int(_count)}" if _count == int(_count) else f"{_count:.1f}"
                                )
                                _display_val += f" (x{_count_str})"
                            ui.label(_display_val).classes("text-caption text-bold")
                            if len(_models) > 1:
                                ui.badge(f"{len(_models)}").props("color=blue-6 outline size=xs")
                        with ui.row().classes("gap-x-1"):
                            for _m in _models:
                                with ui.element("div").style(
                                    "display:inline-flex; align-items:center; gap:4px; "
                                    "background: rgba(128,128,128,0.1); padding: 1px 6px; border-radius: 3px;"
                                ):
                                    ui.label(_m["model"]).classes("text-micro ")
                                    if _m["prob"] is not None:
                                        prob_color = (
                                            None if _ann_type == "blank_non_blank" else _m["color"]
                                        )
                                        try:
                                            _p = float(_m["prob"])
                                            ui.label(f"{_p:.0%}").classes(
                                                "text-micro text-bold"
                                            ).style(f"color: {prob_color};")
                                        except (ValueError, TypeError):
                                            pass


@ui.refreshable
async def render_video_section(dp, species_map, species_groups, global_species_map):
    queue = get_queue()
    if not queue:
        _render_no_videos_match()
        return

    current_idx = get_current_idx()
    current_idx = max(0, min(current_idx, len(queue) - 1))
    selected_video_id = queue[current_idx]

    # Parallelize data fetching to improve performance
    video_task = run.io_bound(
        dp.get_video_detail,
        selected_video_id,
        get_blank_threshold(),
        get_species_threshold(),
        get_obj_detection_threshold(),
    )
    model_ann_task = run.io_bound(dp.get_model_annotations, selected_video_id)
    video, model_ann = await asyncio.gather(video_task, model_ann_task)

    default_behavior = "does_not_react"
    try:
        if model_ann is not None and not model_ann.empty:
            behavior_rows = model_ann[
                (model_ann["annotation_type"] == "behavior")
                & (model_ann["value_text"].notna())
                & (model_ann["value_text"].fillna("").str.lower() != "dummy")
            ]["value_text"]
            if not behavior_rows.empty:
                default_behavior = behavior_rows.mode().iloc[0]
    except Exception:
        logger.exception("Failed to compute default behavior from model annotations")

    if not video:
        # Queue can become stale after DB reset/re-sync or ID format changes.
        filters = get_filters()
        fresh_queue = await run.io_bound(
            dp.get_video_queue,
            {
                **filters,
                "blank_threshold": get_blank_threshold(),
                "species_threshold": get_species_threshold(),
            },
            get_active_project_id(),
        )
        if fresh_queue and fresh_queue != queue:
            set_queue(fresh_queue)
            set_current_idx(max(0, min(current_idx, len(fresh_queue) - 1)))
            render_video_section.refresh()
            return
        ui.label(t("video_load_error")).classes("text-h6 text-negative")
        return

    if get_state_val("is_loading"):
        with ui.column().classes("w-full h-64 items-center justify-center"):
            ui.spinner(size="lg")
            ui.label(t("loading_video")).classes(" q-mt-md")
        return

    is_review_later = bool(video.get("review_later"))

    async def toggle_review_later():
        nonlocal is_review_later
        new_val = not is_review_later
        await run.io_bound(dp.set_review_later, selected_video_id, new_val)
        is_review_later = new_val
        bookmark_btn._props["icon"] = "bookmark" if new_val else "bookmark_border"
        bookmark_btn._props["color"] = "orange" if new_val else "grey-6"
        bookmark_btn.update()

    with ui.column().classes("w-full q-mb-xs gap-0 tour-target-queue"):
        with ui.row().classes("w-full items-center"):
            with ui.element("div").classes("col"):
                if video.get("camera_id"):
                    ui.label(video["camera_id"]).classes("text-caption")
            ui.label(Path(video.get("video_path", "")).name).classes(
                "col text-caption text-center"
            ).style("white-space: nowrap; overflow: hidden; text-overflow: ellipsis;")
            with ui.row().classes("col justify-end items-center gap-xs no-wrap"):
                queue_label = ui.label().classes("text-caption no-wrap")
                ui.button(
                    icon="info_outline",
                    on_click=lambda: show_info_dialog(t("info_queue_title"), t("info_queue_body")),
                ).props("flat round dense size=xs color=grey-6")
        with ui.row().classes("w-full items-center gap-xs q-mt-none"):
            with ui.button(on_click=lambda: navigate(-1)).props("flat dense") as prev_btn:
                with ui.row().classes("items-center gap-xs no-wrap"):
                    ui.icon("chevron_left")
                    ui.badge("P").props("color=grey-9").classes("text-caption")
            prev_btn._props["data-shortcut"] = "prev"
            slider = (
                ui.slider(min=0, max=max(len(queue) - 1, 1), step=1, value=current_idx)
                .props("dense color=primary")
                .classes("col")
                .on("change", lambda e: navigate_to(int(e.args)))
            )
            queue_label.bind_text_from(
                slider,
                "value",
                backward=lambda v: t("queue_label", current=int(v) + 1, total=len(queue)),
            )
            with ui.button(on_click=lambda: navigate(1)).props("flat dense") as next_btn:
                with ui.row().classes("items-center gap-xs no-wrap"):
                    ui.badge("N").props("color=grey-9").classes("text-caption")
                    ui.icon("chevron_right")
            next_btn._props["data-shortcut"] = "next"

    with ui.row().classes("w-full gap-xs items-start"):
        with ui.column().style("flex: 3; min-width: 320px; max-width: 1280px"):
            with ui.card().classes("full-width q-mb-md"):
                if video.get("needs_transcode"):
                    attempted = set(get_state_val("transcode_attempted_ids", []))
                    if selected_video_id not in attempted and is_auto_transcode():
                        attempted.add(selected_video_id)
                        set_state_val("transcode_attempted_ids", list(attempted))
                        ui.label(t("transcoding_video")).classes("text-body2 q-mb-sm")
                        result = await run.io_bound(dp.transcode_video, selected_video_id)
                        if result.get("success"):
                            ui.notify(t("video_transcoded"), type="positive")
                            render_video_section.refresh()
                            return
                        ui.label(
                            t("transcode_failed", error=result.get("error", "unknown error"))
                        ).classes("text-negative text-caption q-mb-sm")

                    async def retry_transcode():
                        result = await run.io_bound(dp.transcode_video, selected_video_id)
                        if result.get("success"):
                            ui.notify(t("video_transcoded"), type="positive")
                            render_video_section.refresh()
                        else:
                            ui.notify(
                                t("transcode_failed", error=result.get("error", "unknown error")),
                                type="negative",
                            )

                    ui.button(
                        t("transcode_for_playback"),
                        icon="movie",
                        color="primary",
                        on_click=retry_transcode,
                    )
                elif video.get("video_path"):
                    # Transcoded sidecars live in the system temp dir, served via /transcoded
                    transcoded_str = video.get("transcoded_path")
                    transcoded_path = Path(transcoded_str) if transcoded_str else None

                    if transcoded_path and transcoded_path.exists():
                        video_url = f"/transcoded/{quote(transcoded_path.name)}"
                    else:
                        serve_path = Path(video["video_path"])
                        if serve_path.exists():
                            video_url = None
                            for _base in [
                                Path(d.path) for d in dp.get_project_dirs(get_active_project_id())
                            ]:
                                try:
                                    rel_path = serve_path.relative_to(_base)
                                    video_url = f"/media/{quote(str(rel_path), safe='/')}"
                                    break
                                except ValueError:
                                    continue
                            if video_url is None:
                                ui.label(t("video_outside_media")).classes("text-negative")
                        else:
                            ui.label(t("video_not_found", path=video["video_path"])).classes(
                                "text-negative"
                            )
                            video_url = None

                    if video_url:
                        render_custom_video_player(
                            video_url, video.get("duration_sec") or 0, str(uuid.uuid4())
                        )
                else:
                    ui.label(t("no_video_path"))

                if not video.get("is_video_valid", True):
                    _err = video.get("video_validation_details", "Unknown error")
                    logger.warning(
                        "Displaying invalid video %s: %s",
                        video.get("video_path", "<unknown>"),
                        _err,
                    )
                    ui.label(t("video_validation_failed", error=_err)).classes(
                        "text-negative text-caption q-mt-sm"
                    )

                _meta_parts = []
                _missing_parts = []
                _created = video.get("created_at")
                if _created:
                    try:
                        _created_str = str(_created)[:16].replace("T", " ")
                        _meta_parts.append(f"{t('video_created_at')}: {_created_str}")
                    except Exception:
                        _missing_parts.append(t("video_created_at"))
                else:
                    _missing_parts.append(t("video_created_at"))
                _lat = video.get("latitude")
                _lon = video.get("longitude")
                _has_location = _lat is not None and _lon is not None
                if not _has_location:
                    _missing_parts.append(t("video_location"))
                with ui.row().classes("items-center gap-xs q-mt-xs"):
                    if _meta_parts:
                        ui.label("  ·  ".join(_meta_parts)).classes("text-caption")
                    if _has_location:

                        def _open_map(lat=_lat, lon=_lon):
                            from review_app.app.components.location_map import (
                                MapMarker,
                                render_location_map,
                            )

                            with (
                                ui.dialog().props("maximized=false") as dlg,
                                ui.card()
                                .classes("q-pa-md")
                                .style("min-width:480px; min-height:360px"),
                            ):
                                with ui.row().classes(
                                    "items-center justify-between w-full q-mb-sm"
                                ):
                                    ui.label(t("video_location")).classes(
                                        "text-subtitle2 font-weight-medium"
                                    )
                                    ui.button(icon="close", on_click=dlg.close).props(
                                        "flat round dense"
                                    )
                                render_location_map([MapMarker(lat=lat, lon=lon)], height="320px")
                            dlg.open()

                        ui.label(f"{t('video_location')}: {_lat:.5f}, {_lon:.5f}").classes(
                            "text-caption text-primary cursor-pointer"
                        ).on("click", _open_map).tooltip(t("click_to_view_map"))
                    if _missing_parts:
                        ui.label(
                            t("video_metadata_missing", fields=", ".join(_missing_parts))
                        ).classes("text-caption text-grey-6").tooltip(
                            t("video_metadata_missing_tooltip")
                        )
                ui.separator().classes("q-my-xs")
                await render_video_tags(selected_video_id, dp, get_annotator_name())

        with ui.column().style("flex: 1; min-width: 300px; max-width: 560px;"):
            _render_ai_annotations(model_ann, global_species_map)
            with ui.card().classes("full-width"):
                with ui.row().classes("items-center w-full q-mb-none"):
                    ui.label(t("manual_review")).classes("text-subtitle1 font-weight-medium")
                    ui.space()
                    bookmark_btn = (
                        ui.button(
                            icon="bookmark" if is_review_later else "bookmark_border",
                            on_click=toggle_review_later,
                        )
                        .props(
                            f"flat round dense {'color=orange' if is_review_later else 'color=grey-6'}"
                        )
                        .tooltip(t("review_later"))
                    )
                    ui.button(
                        icon="info_outline",
                        on_click=lambda: show_info_dialog(
                            t("info_review_later_title"), t("info_review_later_body")
                        ),
                    ).props("flat round dense size=xs color=grey-6")
                consensus = video.get("classification_consensus")
                default_species = consensus or None

                render_annotation_section(
                    video,
                    species_map,
                    species_groups,
                    dp,
                    default_species,
                    default_behavior,
                    render_video_section,
                    render_filter_drawer,
                )


async def setup_review():
    from nicegui import context as ui_context

    from review_app.app.entry_point import shared_header

    dp = await get_or_create_data_provider()
    if not dp or not await run.io_bound(
        dp.has_videos_in_db, active_project_id=get_active_project_id()
    ):
        shared_header()
        render_uninitialized_state()
        return

    species_map = await run.io_bound(
        dp.get_species_display_map, get_language(), project_id=get_active_project_id()
    )
    if not species_map:
        species_map = {"unknown": "unknown"}

    species_groups = await run.io_bound(
        dp.get_species_group_map, get_language(), project_id=get_active_project_id()
    )

    global_species_map = await run.io_bound(dp.get_species_display_map, get_language())

    set_selections([])
    set_state_val("review_state_video_id", None)
    set_state_val("review_is_blank", None)
    set_state_val("review_active_id", None)
    set_state_val("pending_blank_confirm", False)

    # Rebuild queue from DB with current filters.
    queue_ids = await run.io_bound(
        dp.get_video_queue,
        filters={
            **get_filters(),
            "blank_threshold": get_blank_threshold(),
            "species_threshold": get_species_threshold(),
        },
        active_project_id=get_active_project_id(),
    )
    set_queue(queue_ids)

    video_id_param = ui_context.client.request.query_params.get("v")
    initial_idx = 0
    if video_id_param and queue_ids:
        try:
            initial_idx = queue_ids.index(video_id_param)
        except ValueError:
            ui.notify(t("video_not_in_project"), type="warning")
    set_current_idx(initial_idx)

    # Move CSS to a single injection
    ui.add_head_html(
        """
        <style>
            .review-sidebar { background: transparent; }
            .vp-fs-active {
                position: fixed !important;
                top: 0 !important; left: 0 !important;
                width: 100vw !important; height: 100vh !important;
                z-index: 9999 !important;
                background: #f8fafc !important;
                display: flex !important; flex-direction: column !important;
                overflow-y: auto !important;
                padding: 8px !important;
                box-sizing: border-box !important;
            }
            body.body--dark .vp-fs-active {
                background: #0f172a !important;
            }
            .vp-fs-active .vp-video-container {
                flex: 1 !important;
                min-height: 0 !important;
            }
            .vp-fs-active .vp-video-container video {
                height: 100% !important;
                object-fit: contain !important;
            }
        </style>
    """,
        shared=True,
    )

    left_drawer, toggle_mini = shared_header(show_drawer=True)
    assert left_drawer is not None
    left_drawer.classes("review-sidebar")

    with left_drawer:
        await render_filter_drawer(
            dp, species_map, species_groups, navigate_to, render_video_section
        )

    with ui.column().classes("w-full q-pa-xs"):
        with ui.element("div").style("width: 100%; max-width: 1900px; margin: 0 auto"):
            await render_video_section(dp, species_map, species_groups, global_species_map)

    ui.run_javascript("document.activeElement?.blur()")

    if queue_ids:
        ui.run_javascript(f"history.pushState(null, '', '/review?v={queue_ids[initial_idx]}')")

    if not is_tour_completed():
        has_ai = await run.io_bound(lambda: not dp._get_model_annotations_df().empty)
        logger.info("Tour AI annotations check: has_ai=%s", has_ai)
        set_state_val("has_ai_annotations", has_ai)
    show_tour_if_needed(t)

    ui.add_body_html(
        """
        <script>
            if (!window.__videoManagerInitialized) {
                window.__videoManagerInitialized = true;

                // MutationObserver to clean up video elements immediately when removed from DOM
                const observer = new MutationObserver((mutations) => {
                    for (const mutation of mutations) {
                        for (const node of mutation.removedNodes) {
                            if (node.tagName === 'VIDEO') {
                                node.pause();
                                node.src = "";
                                node.load();
                            } else if (node.querySelectorAll) {
                                node.querySelectorAll('video').forEach(v => {
                                    v.pause();
                                    v.src = "";
                                    v.load();
                                });
                            }
                        }
                    }
                });
                observer.observe(document.body, { childList: true, subtree: true });

                // Global keyboard listener that targets the active video or UI elements
                const SPEED_STEPS = """
        + str(SPEED_OPTIONS)
        + """;

                document.addEventListener('keydown', function(e) {
                    if (e.key === 'Escape') {
                        document.activeElement?.blur();
                        return;
                    }
                    if (document.querySelector('.q-dialog')) return;
                    const tag = e.target.tagName.toLowerCase();
                    if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
                    if (e.target.isContentEditable) return;
                    if (e.ctrlKey || e.metaKey || e.altKey) return;

                    // Priority shortcuts: Submit, Next, Prev, Blank
                    if (e.key === 'Enter') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="submit-next"]')?.click();
                    } else if (e.key === 'n' || e.key === 'N') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="next"]')?.click();
                    } else if (e.key === 'p' || e.key === 'P') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="prev"]')?.click();
                    } else if (e.key === 'b' || e.key === 'B') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="mark-blank"]')?.click();
                    } else if (e.key === 'm' || e.key === 'M') {
                        e.preventDefault();
                        document.querySelector('[data-shortcut="mark-unknown"]')?.click();
                    }

                    // Video playback shortcuts - delegated to the first visible video element
                    const videoEl = document.querySelector('video');
                    if (!videoEl) return;

                    if (e.key === ' ') {
                        e.preventDefault();
                        videoEl.paused ? videoEl.play() : videoEl.pause();
                    } else if (e.key === 'ArrowLeft') {
                        e.preventDefault();
                        videoEl.currentTime = Math.max(0, videoEl.currentTime - 5);
                    } else if (e.key === 'ArrowRight') {
                        e.preventDefault();
                        videoEl.currentTime = Math.min(videoEl.duration || 0, videoEl.currentTime + 5);
                    } else if (e.key === 'd' || e.key === 'D') {
                        e.preventDefault();
                        const speedSteps = SPEED_STEPS;
                        let best = 3, bestDiff = Infinity;
                        speedSteps.forEach((s, i) => { const d = Math.abs(s - videoEl.playbackRate); if (d < bestDiff) { bestDiff = d; best = i; } });
                        const newRate = speedSteps[Math.min(best + 1, speedSteps.length - 1)];
                        videoEl.playbackRate = newRate;
                        const speedSel = document.querySelector('[id^="vp-speed-"]');
                        if (speedSel) {
                            speedSel.value = Number.isInteger(newRate) ? newRate.toFixed(1) : String(newRate);
                            speedSel.dispatchEvent(new Event('change'));
                        }
                        videoEl.dispatchEvent(new CustomEvent('speedchange', { detail: newRate }));
                    } else if (e.key === 's' || e.key === 'S') {
                        e.preventDefault();
                        const speedSteps = SPEED_STEPS;
                        let best = 3, bestDiff = Infinity;
                        speedSteps.forEach((s, i) => { const d = Math.abs(s - videoEl.playbackRate); if (d < bestDiff) { bestDiff = d; best = i; } });
                        const newRate = speedSteps[Math.max(best - 1, 0)];
                        videoEl.playbackRate = newRate;
                        const speedSel = document.querySelector('[id^="vp-speed-"]');
                        if (speedSel) {
                            speedSel.value = Number.isInteger(newRate) ? newRate.toFixed(1) : String(newRate);
                            speedSel.dispatchEvent(new Event('change'));
                        }
                        videoEl.dispatchEvent(new CustomEvent('speedchange', { detail: newRate }));
                    } else if (e.key === ']') {
                        e.preventDefault();
                        const brightSlider = document.querySelector('[id^="vp-brightness-"]');
                        if (brightSlider) { brightSlider.value = Math.min(2, parseFloat(brightSlider.value) + 0.05); brightSlider.dispatchEvent(new Event('input')); }
                    } else if (e.key === '[') {
                        e.preventDefault();
                        const brightSlider = document.querySelector('[id^="vp-brightness-"]');
                        if (brightSlider) { brightSlider.value = Math.max(0.5, parseFloat(brightSlider.value) - 0.05); brightSlider.dispatchEvent(new Event('input')); }
                    } else if (e.key === '}') {
                        e.preventDefault();
                        const contrastSlider = document.querySelector('[id^="vp-contrast-"]');
                        if (contrastSlider) { contrastSlider.value = Math.min(2, parseFloat(contrastSlider.value) + 0.05); contrastSlider.dispatchEvent(new Event('input')); }
                    } else if (e.key === '{') {
                        e.preventDefault();
                        const contrastSlider = document.querySelector('[id^="vp-contrast-"]');
                        if (contrastSlider) { contrastSlider.value = Math.max(0.5, parseFloat(contrastSlider.value) - 0.05); contrastSlider.dispatchEvent(new Event('input')); }
                    }
                });
            }
        </script>
    """,
        shared=True,
    )
