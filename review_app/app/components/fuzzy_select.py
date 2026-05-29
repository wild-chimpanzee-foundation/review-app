from nicegui.elements.select import Select


class FuzzySelect(Select, component="fuzzy_select.js"):
    """ui.select whose input filter matches subsequences, e.g. 'cr' matches 'car'.

    Drop-in replacement for ui.select with the same constructor signature. The
    matcher requires the typed characters to appear in order (not necessarily
    contiguous) and ranks tighter, earlier matches first. When the filter
    narrows to exactly one option, it is auto-selected (single-select only).

    NOTE: fuzzy_select.js is a near-verbatim copy of NiceGUI's elements/select.js
    with a custom findFilteredOptions. When upgrading NiceGUI, diff the upstream
    select.js and re-sync any changes.
    """
