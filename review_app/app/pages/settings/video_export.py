from nicegui import run, ui

from review_app.app.translations import t
from review_app.app.utils import user_error_message


def render_species_video_export(dp, project_id: str, lang: str) -> None:
    """Render the administrator's manual-species video export."""
    species_map = dp.get_species_display_map(lang=lang, project_id=project_id)
    annotated = set(dp.get_queue_filter_options(project_id)["species_values"])
    options = {
        name: species_map.get(name, name)
        for name in sorted(annotated, key=lambda name: species_map.get(name, name))
    }

    def folder_label(name: str) -> str:
        return species_map.get(name, name).removesuffix(f" ({name})")

    ui.label(t("species_video_export_desc")).classes("text-caption text-grey-6 q-mb-md")
    species_select = (
        ui.select(
            label=t("species_video_export_species"),
            options=options,
            multiple=True,
            with_input=True,
        )
        .props("outlined dense use-chips clearable")
        .classes("full-width q-mb-sm")
    )
    with ui.row().classes("w-full justify-end q-mb-sm"):
        ui.button(
            t("select_all"),
            icon="select_all",
            on_click=lambda: species_select.set_value(list(options)),
        ).props("flat dense")
    output_input = (
        ui.input(placeholder=t("species_video_export_placeholder"))
        .props("outlined dense clearable")
        .classes("full-width q-mb-sm")
    )
    ui.label(t("species_video_export_default_hint")).classes("text-caption text-grey-6 q-mb-sm")

    async def export():
        selected = list(species_select.value or [])
        if not selected:
            ui.notify(t("species_video_export_none_selected"), type="warning")
            return

        dialog = ui.dialog().props("persistent")
        with dialog, ui.card().classes("q-pa-lg").style("min-width: 420px"):
            ui.label(t("species_video_export_title")).classes("text-h6 q-mb-md")
            count_label = ui.label("0 / …").classes("text-body2 q-mb-xs")
            progress = ui.linear_progress(value=0).props("color=primary")
        dialog.open()
        await ui.context.client.connected()

        def on_progress(done: int, total: int):
            count_label.set_text(f"{done} / {total}")
            progress.set_value(done / total)

        try:
            result = await run.io_bound(
                dp.export_videos_by_species,
                project_id,
                selected,
                {name: folder_label(name) for name in selected},
                on_progress,
                4,
                (output_input.value or "").strip() or None,
            )
        except Exception as exc:
            dialog.close()
            ui.notify(
                t("species_video_export_error", msg=user_error_message(exc)), type="negative"
            )
            return

        dialog.clear()
        with dialog, ui.card().classes("q-pa-lg items-center gap-md").style("min-width: 420px"):
            ui.icon("check_circle", size="lg").classes("text-positive")
            ui.label(
                t("species_video_export_done", count=result["video_count"], path=result["path"])
            ).classes("text-body1")
            ui.button(t("close"), on_click=dialog.close, color="primary").classes("full-width")

    ui.button(t("species_video_export_btn"), icon="file_download", on_click=export).props(
        "unelevated color=secondary"
    )
