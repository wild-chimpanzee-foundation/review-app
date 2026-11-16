"""Searchable, sortable video details with selection independent of inspection filters."""

from nicegui import ui

from review_app.app.translations import t


class AssignmentVideoTable:
    def __init__(self, columns, *, filters=(), selectable=False, on_selection=None, marker=None):
        self.rows = []
        self.selected_ids = set()
        self.on_selection = on_selection
        self.updating = False
        self.filter_inputs = {}
        with ui.row().classes("w-full items-center gap-2"):
            self.search = (
                ui.input(t("assign_video_search"), on_change=self.apply_filters)
                .props("dense outlined clearable debounce=200")
                .classes("grow min-w-40")
            )
            for key, label in filters:
                self.filter_inputs[key] = (
                    ui.select(
                        {},
                        label=label,
                        on_change=self.apply_filters,
                    )
                    .props("dense outlined clearable")
                    .classes("w-44")
                )
        ui.label(t("assign_sort_help")).classes("text-caption text-grey-7")
        self.table = (
            ui.table(
                columns=[
                    {"name": key, "field": key, "label": label, "align": "left", "sortable": True}
                    for key, label in columns
                ],
                rows=[],
                row_key="id",
                pagination={"rowsPerPage": 10, "sortBy": columns[0][0], "descending": False},
                selection="multiple" if selectable else None,
                on_select=self.selection_changed if selectable else None,
            )
            .props("flat dense bordered wrap-cells")
            .classes("w-full")
        )
        if marker:
            self.table.mark(marker)
        if selectable:
            with ui.row().classes("gap-2"):
                ui.button(
                    t("assign_select_visible"), on_click=lambda: self.select_visible(True)
                ).props("flat dense")
                ui.button(
                    t("assign_deselect_visible"), on_click=lambda: self.select_visible(False)
                ).props("flat dense")
            ui.label(t("assign_filter_selection_help")).classes("text-caption text-grey-7")

    @property
    def selection(self):
        return tuple(row["id"] for row in self.rows if row["id"] in self.selected_ids)

    def set_rows(self, rows, *, select_all=False):
        self.rows = rows
        self.selected_ids = {row["id"] for row in rows} if select_all else set()
        self.updating = True
        try:
            for key, field in self.filter_inputs.items():
                values = sorted({row[key] for row in rows if row.get(key)})
                if field.value not in values:
                    field.set_value(None)
                field.set_options(values)
        finally:
            self.updating = False
        self.apply_filters()
        self.notify_selection()

    def apply_filters(self):
        if self.updating:
            return
        query = (self.search.value or "").strip().casefold()
        rows = [
            row
            for row in self.rows
            if (not query or query in " ".join(str(value) for value in row.values()).casefold())
            and all(
                not field.value or row.get(key) == field.value
                for key, field in self.filter_inputs.items()
            )
        ]
        self.table.update_rows(rows)
        self.table.selected = [row for row in rows if row["id"] in self.selected_ids]
        self.table.update()

    def selection_changed(self, event):
        visible = {row["id"] for row in self.table.rows}
        self.selected_ids.difference_update(visible)
        self.selected_ids.update(row["id"] for row in event.selection)
        self.notify_selection()

    def select_visible(self, selected):
        visible = {row["id"] for row in self.table.rows}
        if selected:
            self.selected_ids.update(visible)
        else:
            self.selected_ids.difference_update(visible)
        self.apply_filters()
        self.notify_selection()

    def notify_selection(self):
        if self.on_selection:
            self.on_selection(self.selection)
