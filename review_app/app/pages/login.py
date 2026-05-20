from nicegui import app, ui

from review_app.app.translations import t


def setup_login():
    if app.storage.user.get("annotator_name"):
        ui.navigate.to("/")
        return

    from review_app.app.entry_point import shared_header
    from review_app.app.state import get_data_provider, load_session_defaults

    shared_header()

    with ui.column().classes("w-full items-center justify-center").style("min-height: 80vh"):
        with ui.card().classes("q-pa-xl").style("min-width: 360px; max-width: 480px"):
            ui.label(t("login_title")).classes("text-h5 q-mb-sm")
            ui.label(t("annotator_setup_desc")).classes("text-caption text-grey-6 q-mb-md")
            name_input = (
                ui.input(
                    placeholder=t("annotator_name_placeholder"),
                )
                .props("outlined dense class=w-full")
                .classes("q-mb-md")
            )
            name_input.run_method("focus")

            def _submit():
                name = name_input.value.strip()
                if not name:
                    ui.notify(t("login_name_required"), type="warning")
                    return
                app.storage.user["annotator_name"] = name
                load_session_defaults(get_data_provider())
                ui.navigate.to("/")

            name_input.on("keydown.enter", lambda _: _submit())
            ui.button(t("login_continue"), on_click=_submit, color="primary").props(
                "size=lg"
            ).classes("w-full")
