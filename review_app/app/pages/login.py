from nicegui import app, run, ui

from review_app.app.state import get_language, set_language
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
            current_lang = get_language()

            def _set_lang(code):
                set_language(code)
                ui.run_javascript("window.location.reload()")

            with ui.row().classes("w-full justify-end q-mb-sm"):
                with ui.button_group().props("outline rounded"):
                    for _code, _key in (("en", "lang_en"), ("fr", "lang_fr")):
                        _btn = ui.button(
                            t(_key),
                            on_click=lambda _e, c=_code: _set_lang(c),
                        ).props("size=sm no-caps")
                        if _code == current_lang:
                            _btn.props("color=primary")
                        else:
                            _btn.props("flat color=grey-6")

            ui.label(t("login_title")).classes("text-h5 q-mb-sm")
            ui.label(t("annotator_setup_desc")).classes("text-caption text-grey-6 q-mb-md")

            # Single field: pick an existing annotator from the dropdown, or
            # type a brand-new name. `new_value_mode` lets typed names become
            # valid values without a separate "I'm new" toggle.
            name_select = (
                ui.select(
                    options=list(existing_annotators),
                    label=t("login_select_label"),
                    with_input=True,
                    new_value_mode="add-unique",
                )
                .props("outlined dense autofocus clearable fill-input hide-selected class=w-full")
                .classes("w-full")
            )
            ui.label(t("login_select_hint")).classes("text-caption text-grey-6 q-mb-md")

            # Track the text the user is typing. Ignore empty events: blurring
            # the field fires an empty "input-value" that would otherwise wipe
            # the typed name before we can commit it.
            typed_text = {"value": ""}
            name_select.on(
                "input-value",
                lambda e: typed_text.update(value=e.args) if e.args else None,
            )

            # Commit typed-but-uncommitted text when focus leaves the field, so
            # clicking outside (or on Continue) keeps the name instead of
            # clearing it back to the last committed value.
            def _commit_typed():
                val = (typed_text["value"] or "").strip()
                if val and val != name_select.value:
                    if val not in name_select.options:
                        name_select.options.append(val)
                        name_select.update()
                    name_select.set_value(val)

            name_select.on("blur", lambda _: _commit_typed())

            async def _submit():
                name = (name_select.value or typed_text["value"] or "").strip()

                if not name:
                    ui.notify(t("login_name_required"), type="warning")
                    return

                if dp:
                    await run.io_bound(dp.add_annotator, name)

                app.storage.user["annotator_name"] = name
                load_session_defaults(get_data_provider())
                ui.navigate.to("/")

            name_select.on("keydown.enter", lambda _: _submit())
            ui.button(t("login_continue"), on_click=_submit, color="primary").props(
                "size=lg"
            ).classes("w-full q-mt-md")
