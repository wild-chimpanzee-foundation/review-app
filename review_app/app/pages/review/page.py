import asyncio
import uuid
from pathlib import Path
from urllib.parse import quote

from nicegui import run, ui

from review_app.app.onboarding import show_info_dialog, show_tour_if_needed
from review_app.app.pages.review.annotations import render_annotation_section
from review_app.app.pages.review.filters import render_filter_drawer  # noqa: F401 (refreshable)
from review_app.app.pages.review.video_player import render_custom_video_player
from review_app.app.state import (
    get_active_project_id,
    get_blank_threshold,
    get_current_idx,
    get_filters,
    get_queue,
    get_species_threshold,
    get_state_val,
    is_auto_transcode,
    set_current_idx,
    set_queue,
    set_selections,
    set_state_val,
)
from review_app.app.translations import get_language, t
from review_app.app.utils import (
    get_or_create_data_provider,
    get_probability_color,
    render_uninitialized_state,
)
from review_app.backend.utils import df_to_records


def navigate(direction: int):
    queue = get_queue()
    new_idx = get_current_idx() + direction
    if 0 <= new_idx < len(queue):
        navigate_to(new_idx)


def navigate_to(idx: int):
    queue = get_queue()
    idx = max(0, min(idx, len(queue) - 1))
    # Debounce to prevent rapid overlap
    token = str(uuid.uuid4())
    set_state_val("nav_token", token)
    set_state_val("is_loading", True)

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
            render_video_section.refresh()

    asyncio.create_task(_do_nav())


@ui.refreshable
async def render_video_section(dp, species_map):
    queue = get_queue()
    if not queue:
        ui.label(t("no_videos_match")).classes("text-h6 text-grey-5")
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
    )
    model_ann_task = run.io_bound(dp.get_model_annotations, selected_video_id)
    video, model_ann = await asyncio.gather(video_task, model_ann_task)

    default_behavior = "does_not_react"
    if model_ann is not None and not model_ann.empty:
        behavior_rows = model_ann[
            (model_ann["annotation_type"] == "behavior")
            & (model_ann["value_text"].notna())
            & (model_ann["value_text"].str.lower() != "dummy")
        ]["value_text"]
        if not behavior_rows.empty:
            default_behavior = behavior_rows.mode().iloc[0]

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
            ui.label(t("loading_video")).classes("text-grey-6 q-mt-md")
        return

    with ui.column().classes("w-full q-mb-xs gap-0 tour-target-queue"):
        with ui.row().classes("items-center gap-xs"):
            queue_label = ui.label().classes("text-caption text-grey-6")
            ui.button(
                icon="info_outline",
                on_click=lambda: show_info_dialog(t("info_queue_title"), t("info_queue_body")),
            ).props("flat round dense size=xs color=grey-6")
        slider = (
            ui.slider(min=0, max=max(len(queue) - 1, 1), step=1, value=current_idx)
            .props("dense color=primary")
            .classes("w-full")
            .on("change", lambda e: navigate_to(int(e.args)))
        )
        queue_label.bind_text_from(
            slider,
            "value",
            backward=lambda v: t("queue_label", current=int(v) + 1, total=len(queue)),
        )

    is_review_later = bool(video.get("review_later"))

    async def toggle_review_later():
        await run.io_bound(dp.set_review_later, selected_video_id, not is_review_later)
        render_video_section.refresh()

    with ui.row().classes("w-full items-center q-mb-md"):
        with ui.row().classes("items-center gap-xs"):
            prev_btn = ui.button(icon="chevron_left", on_click=lambda: navigate(-1)).props("flat")
            prev_btn._props["data-shortcut"] = "prev"
            ui.badge("P").props("color=grey-9").classes("text-caption")
        ui.element("div").classes("col flex justify-center")
        with ui.element("div").classes("col flex justify-center"):
            with ui.column().classes("items-center gap-0"):
                ui.label(Path(video.get("video_path", "")).name).classes(
                    "text-subtitle1 font-weight-medium text-center"
                )
                if video.get("camera_id"):
                    ui.label(video["camera_id"]).classes("text-caption text-grey-5 text-center")
        ui.element("div").classes("col flex justify-center")
        with ui.row().classes("items-center gap-xs"):
            ui.badge("N").props("color=grey-9").classes("text-caption")
            next_btn = ui.button(icon="chevron_right", on_click=lambda: navigate(1)).props("flat")
            next_btn._props["data-shortcut"] = "next"

    with ui.row().classes("w-full gap-xs items-start"):
        with ui.column().style("flex: 3; min-width: 320px; max-width: 1280px"):
            with ui.card().classes("full-width q-mb-md"):
                if video.get("needs_transcode"):
                    attempted = set(get_state_val("transcode_attempted_ids", []))
                    if selected_video_id not in attempted and is_auto_transcode():
                        attempted.add(selected_video_id)
                        set_state_val("transcode_attempted_ids", list(attempted))
                        ui.label(t("transcoding_video")).classes("text-body2 text-grey-7 q-mb-sm")
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
                    ui.label(t("no_video_path")).classes("text-grey-5")

                if not video.get("is_video_valid", True):
                    ui.label(
                        t(
                            "video_validation_failed",
                            error=video.get("video_validation_details", "Unknown error"),
                        )
                    ).classes("text-negative text-caption q-mt-sm")

            if model_ann is not None and not model_ann.empty:
                _species_map = await run.io_bound(dp.get_species_display_map, get_language())

                def _is_number(v: str) -> bool:
                    try:
                        float(v)
                        return True
                    except (TypeError, ValueError):
                        return False

                # Grouping structure: { annotation_type: { prediction_value: [list_of_supporting_models] } }
                groups: dict = {}

                for _row in df_to_records(model_ann, 20):
                    _ann_type = _row.get("annotation_type", "")
                    _value = _row.get("value_text", "") or ""
                    _prob = _row.get("probability")

                    # 1. Logic for Blank/Non-Blank
                    if _ann_type == "blank_non_blank":
                        threshold = get_blank_threshold() or 0.0
                        if _is_number(_value):
                            _value = t("non_blank") if float(_value) < threshold else t("blank")
                        elif _prob is not None:
                            _value = t("non_blank") if _prob < threshold else t("blank")
                        else:
                            _value = t("blank") if str(_value).lower() == "blank" else t("non_blank")

                    # 2. Species mapping
                    elif _ann_type == "species":
                        _value = _species_map.get(_value, _value)

                    _color = get_probability_color(_prob) if _prob is not None else "grey"

                    # 3. Nesting: Type -> Value -> Models
                    if _ann_type not in groups:
                        groups[_ann_type] = {}
                    if _value not in groups[_ann_type]:
                        groups[_ann_type][_value] = []

                    groups[_ann_type][_value].append(
                        {
                            "model": _row.get("model_name", ""),
                            "prob": _prob,
                            "color": _color,
                        }
                    )

                    # move higher confidence predictions to the top of the list for each value
                    groups[_ann_type][_value].sort(
                        key=lambda x: (x["prob"] is not None, x["prob"]), reverse=True
                    )

                    # show species before blank/non-blank if both are present, as species is often more informative and blank/non-blank can be a fallback
                    group_order = ["species", "blank_non_blank"]
                    groups = dict(
                        sorted(
                            groups.items(),
                            key=lambda x: (
                                group_order.index(x[0])
                                if x[0] in group_order
                                else len(group_order)
                            ),
                        )
                    )

                with ui.card().classes(
                    "full-width q-mt-xs q-pa-none bg-transparent no-shadow tour-target-ai-predictions"
                ):
                    rename_map = {
                        "species": t("species_annotations"),
                        "blank_non_blank": t("blank_annotations"),
                    }

                    first_ann_type = True
                    for _ann_type, _predictions in groups.items():
                        ann_type_display = rename_map.get(_ann_type, _ann_type.capitalize())

                        with ui.row().classes("items-center gap-xs q-mt-xs q-mb-none"):
                            ui.label(ann_type_display).classes(
                                "text-micro text-grey-6 text-italic"
                            )
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
                                    # Left side: Value
                                    with ui.row().classes("items-center gap-x-2"):
                                        ui.label(_val).classes("text-caption text-bold")
                                        if len(_models) > 1:
                                            ui.badge(f"{len(_models)}").props(
                                                "color=blue-6 outline size=xs"
                                            )

                                    # Right side: Models
                                    with ui.row().classes("gap-x-1"):
                                        for _m in _models:
                                            with ui.element("div").style(
                                                "display:inline-flex; align-items:center; gap:4px; "
                                                "background: rgba(128,128,128,0.1); padding: 1px 6px; border-radius: 3px;"
                                            ):
                                                ui.label(_m["model"]).classes(
                                                    "text-micro text-grey-5"
                                                )

                                                if _m["prob"] is not None:
                                                    if _ann_type == "blank_non_blank":
                                                        prob_color = None

                                                    else:
                                                        prob_color = _m["color"]
                                                    try:
                                                        _p = float(_m["prob"])
                                                        ui.label(f"{_p:.0%}").classes(
                                                            "text-micro text-bold"
                                                        ).style(f"color: {prob_color};")
                                                    except (ValueError, TypeError):
                                                        pass

        with ui.column().style("flex: 1; min-width: 300px; max-width: 560px;"):
            with ui.card().classes("full-width"):
                with ui.row().classes("items-center w-full q-mb-none"):
                    ui.label(t("manual_review")).classes("text-subtitle1 font-weight-medium")
                    ui.space()
                    ui.button(
                        icon="bookmark" if is_review_later else "bookmark_border",
                        on_click=toggle_review_later,
                    ).props(
                        f"flat round dense {'color=orange' if is_review_later else 'color=grey-6'}"
                    ).tooltip(t("review_later"))
                consensus = video.get("classification_consensus")
                default_species = (
                    (consensus if consensus and consensus != "UNKNOWN" else None)
                    or (list(species_map.keys())[0] if species_map else "unknown")
                )

                render_annotation_section(
                    video,
                    species_map,
                    dp,
                    default_species,
                    default_behavior,
                    render_video_section,
                    render_filter_drawer,
                )


async def setup_review():
    from review_app.app.entry_point import shared_header

    dp = await get_or_create_data_provider()
    if not dp or not await run.io_bound(
        dp.has_videos_in_db, active_project_id=get_active_project_id()
    ):
        shared_header()
        render_uninitialized_state()
        return

    species_map = await run.io_bound(dp.get_species_display_map, get_language())
    if not species_map:
        species_map = {"unknown": "unknown"}

    filters = get_filters()
    queue = get_queue()

    # Always refresh queue from DB to avoid stale IDs in session state.
    queue_ids = await run.io_bound(
        dp.get_video_queue,
        filters={
            **filters,
            "blank_threshold": get_blank_threshold(),
            "species_threshold": get_species_threshold(),
        },
        active_project_id=get_active_project_id(),
    )
    set_queue(queue_ids)
    if queue != queue_ids:
        set_current_idx(0)
        set_selections([])
        set_state_val("review_state_video_id", None)
        set_state_val("review_is_blank", None)
        set_state_val("review_active_id", None)
        set_state_val("pending_blank_confirm", False)

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

    left_drawer = shared_header(show_drawer=True)
    assert left_drawer is not None
    left_drawer.classes("review-sidebar")

    with left_drawer:
        await render_filter_drawer(dp, species_map, navigate_to, render_video_section)

    ui.add_head_html(
        """
        <style>
            .filter-toggle-small { display: none !important; }
            @media (max-width: 800px) {
                .filter-toggle-small { display: flex !important; }
            }
        </style>
        """,
        shared=True,
    )

    with ui.column().classes("w-full q-pa-xs"):
        with ui.row().classes("filter-toggle-small q-mb-none"):
            ui.button(t("filters_label"), icon="tune", on_click=left_drawer.toggle).props(
                "outline color=primary dense"
            )
        with ui.element("div").style("width: 100%; max-width: 1900px; margin: 0 auto"):
            await render_video_section(dp, species_map)

    ui.run_javascript("document.activeElement?.blur()")

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
                document.addEventListener('keydown', function(e) {
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
                        const speedSteps = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0];
                        let best = 3, bestDiff = Infinity;
                        speedSteps.forEach((s, i) => { const d = Math.abs(s - videoEl.playbackRate); if (d < bestDiff) { bestDiff = d; best = i; } });
                        const newRate = speedSteps[Math.min(best + 1, speedSteps.length - 1)];
                        videoEl.playbackRate = newRate;
                        const speedSel = document.querySelector('[id^="vp-speed-"]');
                        if (speedSel) speedSel.value = Number.isInteger(newRate) ? newRate.toFixed(1) : String(newRate);
                        videoEl.dispatchEvent(new CustomEvent('speedchange', { detail: newRate }));
                    } else if (e.key === 's' || e.key === 'S') {
                        e.preventDefault();
                        const speedSteps = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0];
                        let best = 3, bestDiff = Infinity;
                        speedSteps.forEach((s, i) => { const d = Math.abs(s - videoEl.playbackRate); if (d < bestDiff) { bestDiff = d; best = i; } });
                        const newRate = speedSteps[Math.max(best - 1, 0)];
                        videoEl.playbackRate = newRate;
                        const speedSel = document.querySelector('[id^="vp-speed-"]');
                        if (speedSel) speedSel.value = Number.isInteger(newRate) ? newRate.toFixed(1) : String(newRate);
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
