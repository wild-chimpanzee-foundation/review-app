from nicegui import run, ui

from review_app.app.pages.review.tags import _tag_label, tag_selector
from review_app.app.state import (
    get_active_project_id,
    get_annotator_name,
    get_blank_threshold,
    get_filters,
    get_language,
    get_species_threshold,
    is_auto_transcode,
    is_autoplay,
    set_auto_transcode,
    set_autoplay,
    set_queue,
    update_filters,
)
from review_app.app.translations import t


async def render_filter_drawer_body(page):
    dp = page.dp
    species_map = page.species_map
    species_groups = page.species_groups
    project_id = get_active_project_id()
    language = get_language()
    filter_options, behavior_display_map, all_tags = await run.io_bound(
        lambda: (
            dp.get_queue_filter_options(project_id),
            dp.get_behavior_display_map(lang=language),
            dp.get_all_tags(),
        )
    )
    filters = get_filters()

    with ui.element("div").classes("q-mini-drawer-hide full-width"):
        # ── Filters ──────────────────────────────────────────────────
        with ui.card().classes("full-width q-mb-md tour-target-filters"):
            ui.label(t("filter_section_general")).classes("text-caption q-mt-xs")

            search = ui.input(t("search"), placeholder=t("search_placeholder")).props(
                "outlined dense class=full-width"
            )
            search.value = filters.get("search_query", "")
            search.on("keyup.enter", lambda _: apply_filters())

            camera_values = filter_options.get("camera_values", [])
            selected_camera = filters.get("selected_camera", "All")
            if selected_camera not in ["All"] + camera_values:
                selected_camera = "All"
            camera_select = ui.select(
                label=t("camera_filter"),
                options={v: v if v != "All" else t("all_option") for v in ["All"] + camera_values},
                value=selected_camera,
                with_input=True,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width")

            ui.separator().classes("q-my-xs")
            ui.label(t("filter_section_model")).classes("text-caption")

            all_group_options = {
                "": t("all_groups"),
                **{g: g for g in sorted({grp for grp in species_groups.values() if grp})},
            }

            possible_species_values = filter_options.get("possible_species_values", [])
            selected_possible_species = filters.get("selected_possible_species", [])
            if not isinstance(selected_possible_species, list):
                selected_possible_species = []
            selected_possible_species = [
                v for v in selected_possible_species if v in possible_species_values
            ]
            selected_possible_species_group = filters.get("selected_possible_species_group", "")
            possible_species_group_filter = ui.select(
                label=t("group_label"),
                options=all_group_options,
                value=selected_possible_species_group,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width clearable")
            possible_species_filter = ui.select(
                label=t("species_model_filter"),
                options={
                    v: species_map.get(v, v)
                    for v in sorted(possible_species_values, key=lambda v: species_map.get(v, v))
                },
                value=selected_possible_species,
                with_input=True,
                multiple=True,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width use-chips")

            # model_behavior_values = filter_options.get("model_behavior_values", [])
            # selected_model_behavior = filters.get("selected_model_behavior", [])
            # if not isinstance(selected_model_behavior, list):
            #     selected_model_behavior = []
            # selected_model_behavior = [
            #     v for v in selected_model_behavior if v in model_behavior_values
            # ]
            # model_behavior_filter = ui.select(
            #     label=t("model_behavior_filter"),
            #     options={v: behavior_display_map.get(v, v) for v in model_behavior_values},
            #     value=selected_model_behavior,
            #     with_input=True,
            #     multiple=True,
            #     on_change=lambda _: apply_filters(),
            # ).props("outlined dense class=full-width use-chips")

            selected_model_blank = filters.get("selected_model_blank", "All")
            model_blank_filter = ui.select(
                label=t("model_blank_filter"),
                options={
                    "All": t("all_option"),
                    "Blank": t("blank"),
                    "Non-Blank": t("non_blank"),
                    "Unknown": t("model_blank_not_processed"),
                },
                value=selected_model_blank,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width")

            needs_review_filter = ui.select(
                label=t("needs_review_filter"),
                options={
                    "All": t("all_option"),
                    "Needs Review": t("needs_review"),
                    "No Review": t("no_review_needed"),
                },
                value=filters.get("selected_needs_review", "All"),
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width")

            ui.separator().classes("q-my-xs")
            ui.label(t("filter_section_manual")).classes("text-caption")

            species_values = filter_options.get("species_values", [])
            selected_species = filters.get("selected_species", [])
            if not isinstance(selected_species, list):
                selected_species = []
            selected_species = [v for v in selected_species if v in species_values]
            selected_species_group = filters.get("selected_species_group", "")
            species_group_filter = ui.select(
                label=t("group_label"),
                options=all_group_options,
                value=selected_species_group,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width clearable")
            species_filter = ui.select(
                label=t("species_manual_filter"),
                options={
                    v: species_map.get(v, v)
                    for v in sorted(species_values, key=lambda v: species_map.get(v, v))
                },
                value=selected_species,
                with_input=True,
                multiple=True,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width use-chips")

            behavior_values = filter_options.get("behavior_values", [])
            selected_behavior = filters.get("selected_behavior", [])
            if not isinstance(selected_behavior, list):
                selected_behavior = []
            selected_behavior = [v for v in selected_behavior if v in behavior_values]
            behavior_filter = ui.select(
                label=t("manual_behavior_filter"),
                options={v: behavior_display_map.get(v, v) for v in behavior_values},
                value=selected_behavior,
                with_input=True,
                multiple=True,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width use-chips")

            selected_manual_blank = filters.get("selected_manual_blank", "All")
            manual_blank_filter = ui.select(
                label=t("manual_blank_filter"),
                options={
                    "All": t("all_option"),
                    "Blank": t("blank"),
                    "Non-Blank": t("non_blank"),
                    "Unlabeled": t("unlabeled_option"),
                },
                value=selected_manual_blank,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width")

            annotation_filter = ui.select(
                label=t("annotation_filter"),
                options={
                    "All": t("all_option"),
                    "Annotated": t("annotated"),
                    "Not Annotated": t("not_annotated"),
                },
                value=filters.get("selected_annotation_status", "All"),
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width")

            annotator_values = filter_options.get("annotator_values", [])
            selected_annotator = filters.get("selected_annotator", [])
            if not isinstance(selected_annotator, list):
                selected_annotator = []
            selected_annotator = [v for v in selected_annotator if v in annotator_values]
            annotator_filter = ui.select(
                label=t("annotator_filter"),
                options={v: v for v in annotator_values},
                value=selected_annotator,
                with_input=True,
                multiple=True,
                on_change=lambda _: apply_filters(),
            ).props("outlined dense class=full-width use-chips")

            _init_selected_tags = filters.get("selected_tags", [])
            if not isinstance(_init_selected_tags, list):
                _init_selected_tags = []
            selected_tag_keys: set[str] = set(_init_selected_tags)

            if all_tags:

                async def on_tag_toggle(_tag_key: str):
                    await apply_filters()

                refresh_tags = tag_selector(
                    sorted(all_tags, key=_tag_label), selected_tag_keys, on_tag_toggle
                )
            multiple_annotators_cb = ui.checkbox(
                t("multiple_annotators_filter"),
                value=bool(filters.get("selected_multiple_annotators", False)),
                on_change=lambda _: apply_filters(),
            ).props("class=full-width")

            is_review_later = ui.checkbox(
                t("review_later_filter"),
                value=bool(filters.get("selected_is_review_later", False)),
                on_change=lambda _: apply_filters(),
            ).props(
                "checked-icon=bookmark unchecked-icon=bookmark_border class=full-width color='warning'"
            )

            _current_annotator = get_annotator_name()
            assigned_to_me_cb = ui.checkbox(
                t("assigned_to_me_filter"),
                value=bool(filters.get("assigned_to_me", False)),
                on_change=lambda _: apply_filters(),
            ).props("class=full-width color=primary")
            if not _current_annotator:
                assigned_to_me_cb.props("disable")

            async def reset_filters():
                search.value = ""
                camera_select.value = "All"
                possible_species_group_filter.value = ""
                possible_species_filter.value = []
                model_blank_filter.value = "All"
                needs_review_filter.value = "All"
                species_group_filter.value = ""
                species_filter.value = []
                behavior_filter.value = []
                manual_blank_filter.value = "All"
                annotation_filter.value = "All"
                is_review_later.value = False
                assigned_to_me_cb.value = False
                annotator_filter.value = []
                multiple_annotators_cb.value = False
                selected_tag_keys.clear()
                if all_tags:
                    refresh_tags()
                web_safe_only_cb.value = False
                sort_select.value = "camera"
                sort_dir[0] = "desc"
                dir_btn.props("outlined dense icon=arrow_downward")
                await apply_filters()

            async def apply_filters():
                new_filters = {
                    "search_query": search.value,
                    "selected_camera": camera_select.value,
                    "selected_sort": sort_select.value,
                    "selected_sort_direction": sort_dir[0],
                    "selected_species": species_filter.value,
                    "selected_species_group": species_group_filter.value or "",
                    "selected_possible_species": possible_species_filter.value,
                    "selected_possible_species_group": possible_species_group_filter.value or "",
                    "selected_behavior": behavior_filter.value,
                    "selected_model_behavior": [],
                    "selected_manual_blank": manual_blank_filter.value,
                    "selected_model_blank": model_blank_filter.value,
                    "selected_annotation_status": annotation_filter.value,
                    "selected_is_review_later": is_review_later.value,
                    "assigned_to_me": assigned_to_me_cb.value,
                    "assigned_to": get_annotator_name() if assigned_to_me_cb.value else "",
                    "selected_annotator": annotator_filter.value,
                    "selected_multiple_annotators": multiple_annotators_cb.value,
                    "selected_needs_review": needs_review_filter.value,
                    "web_safe_only": web_safe_only_cb.value,
                    "selected_tags": list(selected_tag_keys),
                }
                update_filters(**new_filters)
                new_queue = await run.io_bound(
                    dp.get_video_queue,
                    {
                        **new_filters,
                        "blank_threshold": get_blank_threshold(),
                        "species_threshold": get_species_threshold(),
                    },
                    get_active_project_id(),
                )
                set_queue(new_queue)
                page.navigate_to(0)
                ui.run_javascript("document.activeElement?.blur()")

            ui.button(t("reset_filters"), on_click=reset_filters, color="negative").classes(
                "full-width"
            )

        # ── Sort ─────────────────────────────────────────────────────────
        with ui.card().classes("full-width q-mb-md"):
            ui.label(t("sort_label")).classes("text-subtitle2 q-mb-xs")
            with ui.row().classes("w-full items-center gap-sm"):
                sort_select = ui.select(
                    options={
                        "camera": t("sort_camera"),
                        "unreviewed_first": t("sort_unreviewed"),
                        "species_prob": t("sort_species_prob"),
                        "random": t("sort_random"),
                    },
                    value=filters.get("selected_sort", "camera"),
                ).props("outlined dense class=col")

                sort_dir = [filters.get("selected_sort_direction", "desc")]

                dir_btn = ui.button(
                    icon="arrow_downward" if sort_dir[0] == "desc" else "arrow_upward"
                ).props("outlined dense")

            async def apply_sort():
                update_filters(
                    selected_sort=sort_select.value,
                    selected_sort_direction=sort_dir[0],
                )
                f = get_filters()
                new_queue = await run.io_bound(
                    dp.get_video_queue,
                    {
                        **f,
                        "blank_threshold": get_blank_threshold(),
                        "species_threshold": get_species_threshold(),
                    },
                    get_active_project_id(),
                )
                set_queue(new_queue)
                page.navigate_to(0)

            async def toggle_dir():
                sort_dir[0] = "asc" if sort_dir[0] == "desc" else "desc"
                dir_btn.props(
                    f"outlined dense icon={'arrow_upward' if sort_dir[0] == 'asc' else 'arrow_downward'}"
                )
                await apply_sort()

            dir_btn.on_click(toggle_dir)
            sort_select.on_value_change(lambda _: apply_sort())

        # ── Playback ──────────────────────────────────────────────────────
        with ui.card().classes("full-width q-mb-md"):
            ui.label(t("playback_settings")).classes("text-subtitle2 q-mb-xs")

            ui.checkbox(
                t("autoplay"),
                value=is_autoplay(),
                on_change=lambda e: (
                    set_autoplay(e.value),
                    page.render_video_section.refresh(),
                ),
            )
            ui.checkbox(
                t("auto_transcode"),
                value=is_auto_transcode(),
                on_change=lambda e: set_auto_transcode(e.value),
            )

            web_safe_only_cb = ui.checkbox(
                t("web_safe_only"),
                value=bool(filters.get("web_safe_only", False)),
                on_change=lambda _: apply_filters(),
            )
