"""Persistent layout controls, independent of refreshable review content."""

from pathlib import Path

from nicegui import app, ui

from review_app.app.translations import t


class ReviewLayout:
    def __init__(
        self,
        right_drawer,
        *,
        scope: str = "review",
        compact_details: bool = False,
        details_always_visible: bool = False,
    ):
        self.right_drawer = right_drawer
        self.scope = scope
        self.compact_details = compact_details
        self.details_always_visible = details_always_visible
        self.storage_key = f"{scope}_layout"
        saved = app.storage.user.get(self.storage_key, {})
        panels = saved.get("panels", {})
        self.before_focus = saved.get("before_focus")
        if details_always_visible and self.before_focus is not None:
            self.before_focus = {**self.before_focus, "details": True}
        self.panels = {
            name: panels.get(name, True) for name in ("filters", "navigation", "details")
        }
        if details_always_visible:
            self.panels["details"] = True
        self.buttons = {}

    @property
    def focused(self) -> bool:
        return self.before_focus is not None

    def attach(self, left_drawer, header) -> None:
        self.left_drawer, self.header = left_drawer, header
        ui.add_css(Path(__file__).with_name("review_layout.css").read_text())

        def panel_button(name: str, icon: str, label: str) -> None:
            test_id = "annotations" if name == "details" else name
            with (
                ui.button(icon=icon, on_click=lambda: self.toggle(name))
                .props(
                    f'flat dense no-caps data-testid="{self.scope}-toggle-{test_id}" '
                    f'aria-label="{t(label)}"'
                )
                .classes(f"workspace-layout-toggle workspace-layout-{name}")
            ) as button:
                button.tooltip(t(label))
                ui.label(t(label)).classes("workspace-layout-label")
                if name == "filters":
                    self.filter_badge = ui.badge("0").props("floating rounded")
                    self.filter_badge.set_visibility(False)
            self.buttons[name] = button

        with (
            ui.row()
            .classes("workspace-layout-toolbar items-center no-wrap")
            .props(f'role=group aria-label="{t("workspace_layout")}"')
        ) as self.toolbar:
            panel_button("filters", "view_sidebar", "filters_label")
            with ui.row().classes("workspace-layout-center items-center no-wrap gap-1"):
                panel_button("navigation", "web_asset", "workspace_navigation")
                self.focus_button = (
                    ui.button()
                    .props(f'flat dense no-caps data-testid="{self.scope}-toggle-focus"')
                    .classes("workspace-layout-focus")
                )
                self.focus_button.on(
                    "click",
                    self.toggle_focus,
                    args=[],
                    js_handler=Path(__file__)
                    .with_suffix(".js")
                    .read_text()
                    .replace("__FOCUS_BUTTON_ID__", str(self.focus_button.id)),
                )
            if not self.details_always_visible:
                panel_button("details", "view_sidebar", "workspace_review_details")
        self.apply()

    def toggle(self, name: str) -> None:
        # Panel adjustments keep focus/fullscreen active until Exit focus is pressed.
        self.panels[name] = not self.panels[name]
        self.apply()
        self.remember()

    def toggle_focus(self) -> None:
        if self.focused:
            self.panels = self.before_focus
            self.before_focus = None
        else:
            self.before_focus = self.panels.copy()
            self.panels = dict.fromkeys(self.panels, False)
        self.apply()
        self.remember()

    def apply(self) -> None:
        if self.details_always_visible:
            self.panels["details"] = True
        self.left_drawer.set_value(self.panels["filters"])
        self.header.set_value(self.panels["navigation"])
        # Keep Species' annotation tools and Distance's compact rail visible in focus mode.
        self.right_drawer.set_value(
            self.details_always_visible or self.compact_details or self.panels["details"]
        )
        self.right_drawer.props["mini"] = self.compact_details and not self.panels["details"]
        self.right_drawer.update()
        for name, button in self.buttons.items():
            button.props(f'aria-pressed="{str(self.panels[name]).lower()}"')
        self.focus_button.set_text(
            t("workspace_exit_focus" if self.focused else "workspace_focus")
        )
        self.focus_button.set_icon("fullscreen_exit" if self.focused else "fullscreen")
        self.focus_button.props(f'aria-pressed="{str(self.focused).lower()}"')
        self.toolbar.style(
            "top:var(--q-header-offset, 0px)" if self.panels["navigation"] else "top:0px"
        )

    def remember(self) -> None:
        app.storage.user[self.storage_key] = {
            "panels": self.panels.copy(),
            "before_focus": self.before_focus.copy() if self.before_focus is not None else None,
        }

    def set_filter_count(self, count: int) -> None:
        self.filter_badge.set_text(str(count))
        self.filter_badge.set_visibility(count > 0)
        self.buttons["filters"].props(
            f'aria-label="{t("filters_label")}: {t("workspace_active_filters", count=count)}"'
        )
