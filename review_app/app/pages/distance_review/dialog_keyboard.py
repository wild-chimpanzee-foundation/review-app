"""Shared keyboard controls for Distance forms and confirmation dialogs."""

from pathlib import Path

from nicegui import ui


def keyboard_dialog_card() -> ui.card:
    """Keep dialog actions local, without overriding fields or playback shortcuts."""
    return (
        ui.card()
        .props('tabindex="0" data-distance-dialog-keyboard="true"')
        .on("keydown.capture", js_handler=Path(__file__).with_suffix(".js").read_text())
        # QCheckbox also handles Enter on keyup; confirming must not toggle it.
        .on(
            "keyup.enter.capture",
            js_handler="event => { event.preventDefault(); event.stopPropagation(); }",
        )
    )
