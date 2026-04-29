import asyncio
import uuid
from pathlib import Path
from urllib.parse import quote

from nicegui import run, ui

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
from review_app.app.utils import get_or_create_data_provider, render_uninitialized_state
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

    with ui.column().classes("w-full q-mb-xs gap-0"):
        queue_label = ui.label().classes("text-caption text-grey-6")
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

    with ui.row().classes("w-full flex-nowrap gap-md items-start"):
        with ui.column().style("flex: 3; min-width: 500px; max-width: 1500px"):
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

        with ui.column().style("flex:1;min-width:500px;"):
            with ui.card().classes("full-width"):
                with ui.row().classes("items-center w-full q-mb-sm"):
                    ui.label(t("manual_review")).classes("text-subtitle1 font-weight-medium")
                    ui.space()
                    ui.button(
                        icon="bookmark" if is_review_later else "bookmark_border",
                        on_click=toggle_review_later,
                    ).props(
                        f"flat round dense {'color=orange' if is_review_later else 'color=grey-6'}"
                    ).tooltip(t("review_later"))
                default_species = video.get("default_species")
                if not default_species:
                    fallback_species = video.get("classification_consensus", "unknown")
                    if not fallback_species or fallback_species == "UNKNOWN":
                        fallback_species = list(species_map.keys())[0]
                    default_species = fallback_species

                render_annotation_section(
                    video,
                    species_map,
                    dp,
                    default_species,
                    default_behavior,
                    render_video_section,
                    render_filter_drawer,
                )

            # ── Model annotations ─────────────────────────────────────────────
            with ui.card().classes("full-width"):
                ui.label(t("model_annotations")).classes(
                    "text-subtitle1 font-weight-medium q-mb-sm"
                )

                def is_number(value: str) -> bool:
                    try:
                        float(value)
                        return True
                    except (TypeError, ValueError):
                        return False

                if model_ann is not None and not model_ann.empty:
                    species_map = await run.io_bound(dp.get_species_display_map, get_language())
                    for row in df_to_records(model_ann, 10):
                        model_name = row.get("model_name", "")
                        ann_type = row.get("annotation_type", "")
                        value_text = row.get("value_text", "") or ""
                        if ann_type == "blank_non_blank" and is_number(value_text):
                            if (
                                get_blank_threshold() is not None
                                and get_blank_threshold() >= 0
                                and float(value_text) < get_blank_threshold()
                            ):
                                value_text = t("non_blank")
                            else:
                                value_text = t("blank")
                        prob_raw = row.get("probability", 0.0)

                        if ann_type == "species":
                            value_text = species_map.get(value_text, value_text)
                            element_color = (
                                "positive"
                                if prob_raw > 0.85
                                else "warning"
                                if prob_raw > 0.5
                                else "negative"
                            )

                        else:
                            element_color = "primary"
                        with ui.row().classes("w-full items-center q-py-sm "):
                            # 1. Identity Block (Left-aligned, fixed size)
                            with ui.column().classes("gap-0").style("width: 140px"):
                                ui.label(model_name).classes("text-bold text-primary").style(
                                    "font-size: 0.75rem"
                                )
                                ui.label(ann_type).classes("text-caption text-grey-6 italic")

                            # 2. Value & Bar Block (Flex-grow to fill space)
                            with ui.column().classes("col gap-1 px-4"):
                                # The text value sits right above its bar
                                ui.label(value_text).classes(
                                    "text-body2 text-weight-medium"
                                ).style("line-height: 1")

                                with ui.row().classes("w-full items-center gap-2"):
                                    if prob_raw is not None:
                                        try:
                                            p = float(prob_raw)
                                            # round to 2 decimals for display
                                            p_display = round(p, 2)
                                            ui.linear_progress(
                                                value=p_display, color=element_color
                                            ).props("rounded")
                                        except:
                                            ui.label(str(prob_raw))

                        ui.separator().style("opacity: 0.2")

                else:
                    ui.label(t("no_model_annotations")).classes("text-grey-5")


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
            .review-sidebar {
                background: transparent;
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

    with ui.column().classes("w-full q-pa-md"):
        await render_video_section(dp, species_map)

    ui.run_javascript("document.activeElement?.blur()")

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
                    } else if (e.key === 'u' || e.key === 'U') {
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
