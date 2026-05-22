from nicegui import app, run, ui

from review_app.app.translations import t


async def setup_login():
    if app.storage.user.get("annotator_name"):
        ui.navigate.to("/")
        return

    from review_app.app.entry_point import shared_header
    from review_app.app.state import get_data_provider, load_session_defaults
    from review_app.app.utils import get_or_create_data_provider

    shared_header()

    dp = await get_or_create_data_provider()
    existing_annotators = await run.io_bound(dp.get_all_annotators) if dp else []

    with ui.column().classes("w-full items-center justify-center").style("min-height: 80vh"):
        with ui.card().classes("q-pa-xl").style("min-width: 360px; max-width: 480px"):
            ui.label(t("login_title")).classes("text-h5 q-mb-sm")
            ui.label(t("annotator_setup_desc")).classes("text-caption text-grey-6 q-mb-md")

            name_select: ui.select | None = None
            name_input: list[ui.input] = []
            create_new_check: list[ui.checkbox] = []

            if existing_annotators:
                options = {a: a for a in existing_annotators}
                name_select = (
                    ui.select(
                        options=options,
                        label=t("login_select_label"),
                    )
                    .props("outlined dense class=w-full")
                    .classes("q-mb-md")
                )

                def _on_create_new(e):
                    if name_select:
                        name_select.visible = not e.value
                    if name_input:
                        name_input[0].visible = e.value
                    if e.value and name_input:
                        name_input[0].run_method("focus")

                cb = ui.checkbox(t("login_create_new"), value=False).classes("q-mb-md")
                cb.on_value_change(_on_create_new)
                create_new_check.append(cb)

            inp = (
                ui.input(
                    placeholder=t("annotator_name_placeholder"),
                )
                .props("outlined dense class=w-full")
                .classes("q-mb-md")
            )
            name_input.append(inp)

            if existing_annotators:
                name_input[0].visible = False
            else:
                name_input[0].run_method("focus")

            async def _submit():
                if name_select and name_select.visible and name_select.value:
                    name = name_select.value.strip()
                elif name_input and name_input[0].visible:
                    name = name_input[0].value.strip()
                else:
                    name = name_input[0].value.strip()

                if not name:
                    ui.notify(t("login_name_required"), type="warning")
                    return

                if dp:
                    await run.io_bound(dp.add_annotator, name)

                app.storage.user["annotator_name"] = name
                load_session_defaults(get_data_provider())
                ui.navigate.to("/")

            name_input[0].on("keydown.enter", lambda _: _submit())
            ui.button(t("login_continue"), on_click=_submit, color="primary").props(
                "size=lg"
            ).classes("w-full")
