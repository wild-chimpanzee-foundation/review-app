from nicegui import run, ui

from review_app.app.translations import t
from review_app.app.utils import user_error_message


def render_species_section(dp, active_project_id: int, lang: str) -> None:
    ui.label(t("project_species_desc")).classes("text-caption text-grey-6 q-mb-md")

    all_species_map = dp.get_species_display_map(lang=lang)
    project_species = dp.get_project_species(active_project_id)

    def _save(ev):
        dp.set_project_species(active_project_id, ev.value or [])

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
            .on_value_change(_save)
        )
        ui.button(
            t("select_all"),
            on_click=lambda: species_select.set_value(list(all_species_map.keys())),
        ).props("flat dense")
        ui.button(
            t("deselect_all"),
            on_click=lambda: species_select.set_value([]),
        ).props("flat dense")

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

            with ui.row().classes("w-full justify-end q-mt-md"):
                ui.button(t("cancel"), on_click=dialog.close).props("flat")
                ui.button(t("add_species_btn"), on_click=_do_add_species).props("unelevated")
        dialog.open()

    with ui.row().classes("items-center gap-xs"):
        ui.label(t("add_custom_label")).classes("text-caption text-grey-6")
        ui.button(
            t("add_custom_species_title"), icon="add", on_click=_add_custom_species_dialog
        ).props("flat dense")
    ui.separator().classes("q-mt-md")

    _pending_collection_content: list[str] = []

    async def _handle_collection_upload(e):
        content = (await e.file.read()).decode("utf-8", errors="replace")
        _pending_collection_content.clear()
        _pending_collection_content.append(content)

        dialog = ui.dialog()
        with dialog, ui.card().classes("q-pa-lg").style("width: 360px"):
            ui.label(t("import_collection_csv")).classes("text-h6 q-mb-md")
            name_input = (
                ui.input(t("collection_name_label")).props("outlined dense").classes("w-full")
            )

            async def _do_import():
                coll_name = (name_input.value or "").strip()
                if not coll_name:
                    ui.notify(t("all_fields_required"), type="warning")
                    return
                try:
                    count = await run.io_bound(
                        dp.import_collection_from_csv, coll_name, _pending_collection_content[0]
                    )
                    ui.notify(
                        t("collection_import_success", name=coll_name, count=count),
                        type="positive",
                    )
                    dialog.close()
                except Exception as exc:
                    ui.notify(
                        t("csv_import_error", error=user_error_message(exc)), type="negative"
                    )

            with ui.row().classes("w-full justify-end q-mt-md"):
                ui.button(t("cancel"), on_click=dialog.close).props("flat")
                ui.button(t("import_collection_csv"), on_click=_do_import).props("unelevated")
        dialog.open()

    collection_uploader = (
        ui.upload(on_upload=_handle_collection_upload, auto_upload=True)
        .props("accept=.csv")
        .style("display: none")
    )

    _COLLECTION_CSV_TEMPLATE = "scientific_name;english_name;french_name;group_en;group_fr;IUCN\n"

    with ui.row().classes("items-center gap-xs"):
        ui.label(t("import_csv_label")).classes("text-caption text-grey-6")
        ui.button(
            t("import_collection_csv"),
            icon="playlist_add",
            on_click=lambda: ui.run_javascript(
                f"document.getElementById('c{collection_uploader.id}').querySelector('.q-uploader__input').click()"
            ),
        ).props("flat dense")
        ui.button(
            t("download_csv_template"),
            icon="download",
            on_click=lambda: ui.download(
                _COLLECTION_CSV_TEMPLATE.encode(), filename="collection_template.csv"
            ),
        ).props("flat dense")
