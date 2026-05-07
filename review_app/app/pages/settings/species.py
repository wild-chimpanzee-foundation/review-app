from nicegui import run, ui

from review_app.app.translations import t
from review_app.app.utils import user_error_message


def render_species_section(dp, active_project_id: int, lang: str) -> None:
    ui.label(t("project_species_desc")).classes("text-caption text-grey-6 q-mb-md")

    all_species_map = dp.get_species_display_map(lang=lang)
    project_species = dp.get_project_species(active_project_id)
    behavior_display_map = dp.get_behavior_display_map(lang=lang)

    with (
        ui.column()
        .classes("w-full gap-sm")
        .style("max-height: 400px; overflow-y: auto") as behaviors_container
    ):
        pass

    def _render_behaviors():
        behaviors_container.clear()
        with behaviors_container:
            ui.label(t("configure_behaviors")).classes("text-subtitle2 q-mt-md")
            for spec in sorted(species_select.value or []):
                current_behaviors = dp.get_project_species_behaviors(active_project_id, spec)
                spec_display = all_species_map.get(spec, spec)
                with ui.row().classes("w-full items-center gap-sm no-wrap"):
                    ui.label(spec_display).classes("text-caption").style("width: 150px")

                    def _make_behavior_save(s):
                        def _save(ev):
                            dp.set_project_species_behaviors(active_project_id, s, ev.value or [])

                        return _save

                    ui.select(
                        options=behavior_display_map,
                        value=current_behaviors,
                        multiple=True,
                        label=t("species_behaviors", species=spec_display),
                    ).props("outlined dense use-chips").classes("col").on_value_change(
                        _make_behavior_save(spec)
                    )

    def _save_and_render(ev):
        dp.set_project_species(active_project_id, ev.value or [])
        _render_behaviors()

    with ui.row().classes("w-full items-center gap-sm"):
        species_select = (
            ui.select(
                options=all_species_map,
                value=project_species,
                label=t("enable_species"),
                multiple=True,
                with_input=True,
            )
            .props("outlined use-chips full-width")
            .classes("col")
            .on_value_change(_save_and_render)
        )
        ui.button(
            t("select_all"),
            on_click=lambda: species_select.set_value(list(all_species_map.keys())),
        ).props("flat dense")
        ui.button(
            t("deselect_all"),
            on_click=lambda: species_select.set_value([]),
        ).props("flat dense")

    _render_behaviors()

    async def _add_custom_species_dialog():
        dialog = ui.dialog()
        groups = dp.get_existing_groups()
        iucn_options = dp.get_existing_iucn()
        existing_sci_names = list(all_species_map.keys())
        existing_sci_names_lower = {n.lower() for n in existing_sci_names}
        existing_sp_en = list(dp.get_species_display_map(lang="en").values())
        existing_sp_fr = list(dp.get_species_display_map(lang="fr").values())

        def _fuzzy_matches(val, candidates, threshold=50):
            from thefuzz import process

            if not val or not candidates:
                return []
            return [
                m for m, score in process.extract(val, candidates, limit=4) if score >= threshold
            ]

        with (
            dialog,
            ui.card()
            .classes("q-pa-lg")
            .style("width: 480px; max-height: 90vh; display: flex; flex-direction: column;"),
        ):
            ui.label(t("add_custom_species_title")).classes("text-h6 q-mb-md")
            with ui.column().classes("w-full gap-xs").style("overflow-y: auto; flex: 1;"):
                sci = ui.input(t("scientific_name")).props("outlined dense").classes("w-full")
                sci_hint = (
                    ui.label("")
                    .classes("text-caption q-mb-xs")
                    .style("min-height: 1em; white-space: nowrap; overflow-x: auto;")
                )

                def _update_sci_hint(e):
                    val = (e.value or "").strip()
                    if not val:
                        sci_hint.text = ""
                        return
                    if val.lower() in existing_sci_names_lower:
                        sci_hint.text = t("species_exists")
                        sci_hint.style(
                            "color: var(--q-negative); white-space: nowrap; overflow-x: auto;"
                        )
                    else:
                        matches = _fuzzy_matches(val, existing_sci_names)
                        sci_hint.text = ("Similar: " + " · ".join(matches)) if matches else ""
                        sci_hint.style(
                            "color: var(--q-on-surface, inherit); white-space: nowrap; overflow-x: auto;"
                        )

                sci.on_value_change(_update_sci_hint)
                n_en = ui.input(t("name_en_label")).props("outlined dense").classes("w-full")
                n_en_hint = (
                    ui.label("")
                    .classes("text-caption text-grey-6 q-mb-xs")
                    .style("min-height: 1em; white-space: nowrap; overflow-x: auto;")
                )

                def _update_n_en_hint(e):
                    matches = _fuzzy_matches((e.value or "").strip(), existing_sp_en)
                    n_en_hint.text = ("Similar: " + " · ".join(matches)) if matches else ""

                n_en.on_value_change(_update_n_en_hint)
                n_fr = ui.input(t("name_fr_label")).props("outlined dense").classes("w-full")
                n_fr_hint = (
                    ui.label("")
                    .classes("text-caption text-grey-6 q-mb-xs")
                    .style("min-height: 1em; white-space: nowrap; overflow-x: auto;")
                )

                def _update_n_fr_hint(e):
                    matches = _fuzzy_matches((e.value or "").strip(), existing_sp_fr)
                    n_fr_hint.text = ("Similar: " + " · ".join(matches)) if matches else ""

                n_fr.on_value_change(_update_n_fr_hint)
                g_en = (
                    ui.select(
                        options=groups["en"],
                        label=t("group_en_label"),
                        with_input=True,
                        new_value_mode="add",
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                g_fr = (
                    ui.select(
                        options=groups["fr"],
                        label=t("group_fr_label"),
                        with_input=True,
                        new_value_mode="add",
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )
                iucn = (
                    ui.select(
                        options=iucn_options,
                        label=t("iucn_label"),
                        with_input=True,
                        new_value_mode="add",
                    )
                    .props("outlined dense")
                    .classes("w-full")
                )

            async def _do_add_species():
                nonlocal all_species_map
                if not all([sci.value, n_en.value, n_fr.value, g_en.value, g_fr.value]):
                    ui.notify(t("all_fields_required"), type="warning")
                    return
                success = await run.io_bound(
                    dp.add_custom_species,
                    sci.value,
                    n_en.value,
                    n_fr.value,
                    g_en.value,
                    g_fr.value,
                    iucn.value or None,
                )
                if not success:
                    ui.notify(t("species_exists"), type="warning")
                    return
                ui.notify(t("species_added"), type="positive")
                dialog.close()
                all_species_map = dp.get_species_display_map(lang=lang)
                species_select.options = all_species_map
                species_select.update()
                _render_behaviors()

            with ui.row().classes("w-full justify-end q-mt-md"):
                ui.button(t("cancel"), on_click=dialog.close).props("flat")
                ui.button(t("add_species_btn"), on_click=_do_add_species).props("unelevated")
        dialog.open()

    async def _add_custom_behavior_dialog():
        dialog = ui.dialog()
        existing_behavior_keys = [b["key"] for b in dp.get_all_behaviors()]
        existing_behavior_keys_lower = {k.lower() for k in existing_behavior_keys}
        existing_beh_en = list(dp.get_behavior_display_map(lang="en").values())
        existing_beh_fr = list(dp.get_behavior_display_map(lang="fr").values())

        def _fuzzy_matches(val, candidates, threshold=50):
            from thefuzz import process

            if not val or not candidates:
                return []
            return [
                m for m, score in process.extract(val, candidates, limit=4) if score >= threshold
            ]

        with (
            dialog,
            ui.card()
            .classes("q-pa-lg")
            .style("width: 480px; max-height: 90vh; display: flex; flex-direction: column;"),
        ):
            ui.label(t("add_custom_behavior_title")).classes("text-h6 q-mb-md")
            with ui.column().classes("w-full gap-xs").style("overflow-y: auto; flex: 1;"):
                key = ui.input(t("behavior_key")).props("outlined dense").classes("w-full")
                key_hint = (
                    ui.label("")
                    .classes("text-caption q-mb-xs")
                    .style("min-height: 1em; white-space: nowrap; overflow-x: auto;")
                )

                def _update_key_hint(e):
                    val = (e.value or "").strip()
                    if not val:
                        key_hint.text = ""
                        return
                    if val.lower() in existing_behavior_keys_lower:
                        key_hint.text = t("behavior_exists")
                        key_hint.style(
                            "color: var(--q-negative); white-space: nowrap; overflow-x: auto;"
                        )
                    else:
                        matches = _fuzzy_matches(val, existing_behavior_keys)
                        key_hint.text = ("Similar: " + " · ".join(matches)) if matches else ""
                        key_hint.style(
                            "color: var(--q-on-surface, inherit); white-space: nowrap; overflow-x: auto;"
                        )

                key.on_value_change(_update_key_hint)
                n_en = ui.input(t("name_en_label")).props("outlined dense").classes("w-full")
                n_en_hint = (
                    ui.label("")
                    .classes("text-caption text-grey-6 q-mb-xs")
                    .style("min-height: 1em; white-space: nowrap; overflow-x: auto;")
                )

                def _update_beh_en_hint(e):
                    matches = _fuzzy_matches((e.value or "").strip(), existing_beh_en)
                    n_en_hint.text = ("Similar: " + " · ".join(matches)) if matches else ""

                n_en.on_value_change(_update_beh_en_hint)
                n_fr = ui.input(t("name_fr_label")).props("outlined dense").classes("w-full")
                n_fr_hint = (
                    ui.label("")
                    .classes("text-caption text-grey-6 q-mb-xs")
                    .style("min-height: 1em; white-space: nowrap; overflow-x: auto;")
                )

                def _update_beh_fr_hint(e):
                    matches = _fuzzy_matches((e.value or "").strip(), existing_beh_fr)
                    n_fr_hint.text = ("Similar: " + " · ".join(matches)) if matches else ""

                n_fr.on_value_change(_update_beh_fr_hint)

            async def _do_add_behavior():
                nonlocal behavior_display_map
                if not all([key.value, n_en.value]):
                    ui.notify(t("all_fields_required"), type="warning")
                    return
                success = await run.io_bound(
                    dp.add_custom_behavior,
                    key.value,
                    n_en.value,
                    n_fr.value or None,
                )
                if not success:
                    ui.notify(t("behavior_exists"), type="warning")
                    return
                ui.notify(t("behavior_added"), type="positive")
                dialog.close()
                behavior_display_map = dp.get_behavior_display_map(lang=lang)
                _render_behaviors()

            with ui.row().classes("w-full justify-end q-mt-md"):
                ui.button(t("cancel"), on_click=dialog.close).props("flat")
                ui.button(t("add_behavior_btn"), on_click=_do_add_behavior).props("unelevated")
        dialog.open()

    async def _handle_species_upload(e):
        nonlocal all_species_map
        content = (await e.file.read()).decode("utf-8", errors="replace")
        try:
            count = await run.io_bound(
                dp.import_project_species_from_csv, active_project_id, content
            )
            ui.notify(t("species_import_success", count=count), type="positive")
            all_species_map = dp.get_species_display_map(lang=lang)
            species_select.options = all_species_map
            species_select.update()
            species_select.set_value(dp.get_project_species(active_project_id))
            _render_behaviors()
        except Exception as exc:
            ui.notify(t("csv_import_error", error=user_error_message(exc)), type="negative")

    async def _handle_behaviors_upload(e):
        nonlocal behavior_display_map
        content = (await e.file.read()).decode("utf-8", errors="replace")
        try:
            count = await run.io_bound(
                dp.import_project_behaviors_from_csv, active_project_id, content
            )
            ui.notify(t("behaviors_import_success", count=count), type="positive")
            behavior_display_map = dp.get_behavior_display_map(lang=lang)
            _render_behaviors()
        except Exception as exc:
            ui.notify(t("csv_import_error", error=user_error_message(exc)), type="negative")

    species_uploader = (
        ui.upload(on_upload=_handle_species_upload, auto_upload=True)
        .props("accept=.csv")
        .style("display: none")
    )
    behaviors_uploader = (
        ui.upload(on_upload=_handle_behaviors_upload, auto_upload=True)
        .props("accept=.csv")
        .style("display: none")
    )

    with ui.row().classes("items-center gap-xs"):
        ui.label(t("add_custom_label")).classes("text-caption text-grey-6")
        ui.button(
            t("add_custom_species_title"), icon="add", on_click=_add_custom_species_dialog
        ).props("flat dense")
        ui.button(
            t("add_custom_behavior_title"), icon="add", on_click=_add_custom_behavior_dialog
        ).props("flat dense")
    ui.separator().classes("q-mt-md")
    with ui.row().classes("items-center gap-xs"):
        ui.label(t("import_csv_label")).classes("text-caption text-grey-6")
        ui.button(
            t("upload_species_csv"),
            icon="upload_file",
            on_click=lambda: ui.run_javascript(
                f"document.getElementById('c{species_uploader.id}').querySelector('.q-uploader__input').click()"
            ),
        ).props("flat dense")
        ui.button(
            t("upload_behaviors_csv"),
            icon="upload_file",
            on_click=lambda: ui.run_javascript(
                f"document.getElementById('c{behaviors_uploader.id}').querySelector('.q-uploader__input').click()"
            ),
        ).props("flat dense")
