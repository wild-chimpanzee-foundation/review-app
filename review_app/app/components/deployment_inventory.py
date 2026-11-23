"""New-project preview of deployment groups inferred from footage folders."""

from __future__ import annotations

from nicegui import run, ui

from review_app.app.translations import t
from review_app.app.utils import user_error_message
from review_app.backend.deployments import DeploymentInputError, preview_deployment_setup


def render_inventory(rows: list[dict]) -> None:
    columns = [
        {"name": name, "label": label, "field": name, "align": "left", "sortable": True}
        for name, label in (
            ("deployment_id", "deployment_id"),
            ("name", t("deployment_name")),
            ("camera_id", "camera_id"),
            ("installation_date", "installation_date"),
            ("retrieval_date", "retrieval_date"),
            ("transect_id", "transect_id"),
            ("habitat", "habitat"),
            ("notes", "notes"),
        )
    ]
    table = ui.table(columns=columns, rows=rows, row_key="deployment_id", pagination=10).classes(
        "w-full"
    )
    ui.input(t("deployment_search")).classes("w-full").bind_value(table, "filter")


class DeploymentSetup:
    def __init__(self, get_root, on_change):
        self.get_root = get_root
        self.on_change = on_change
        self.preview = None
        self._preview_root = None
        self._revision = 0

    @property
    def ready(self) -> bool:
        return self.preview is not None and self._preview_root == self.get_root()

    def enable_creation(self, enabled: bool) -> None:
        self.create.set_enabled(enabled)
        self.create_and_review.set_enabled(enabled)

    async def refresh(self) -> None:
        self._revision += 1
        revision = self._revision
        root = self.get_root()
        self.preview = None
        self._preview_root = None
        self.results.clear()
        self.status.set_text("")
        self.attention.set_visibility(False)
        self.create_and_review.set_visibility(False)
        self.create.set_text(t("deployment_setup_create"))
        self.create.props(remove="outline")
        self.on_change()
        if not root:
            return

        self.status.set_text(t("deployment_validating"))
        try:
            preview = await run.io_bound(preview_deployment_setup, root)
        except Exception as exc:
            if revision == self._revision and root == self.get_root():
                self.status.set_text(
                    t(exc.key, **exc.params)
                    if isinstance(exc, DeploymentInputError)
                    else user_error_message(exc)
                )
            return
        if revision != self._revision or root != self.get_root():
            return
        self.preview = preview
        self._preview_root = root
        self.status.set_text(t("deployment_count", count=len(preview.deployments)))
        flagged = [row for row in preview.folders if row.unmatched_reference]
        if flagged:
            self.attention.set_text(
                t(
                    "deployment_setup_unmatched",
                    count=sum(row.references for row in flagged),
                    folders=len(flagged),
                )
            )
            self.attention.set_visibility(True)
            self.create_and_review.set_visibility(True)
            self.create.set_text(t("deployment_setup_create_later"))
            self.create.props("outline")
        with self.results:
            if flagged:
                ui.table(
                    columns=[
                        {
                            "name": "folder",
                            "label": t("deployment_folder_name"),
                            "field": "folder",
                            "align": "left",
                        },
                        {
                            "name": "references",
                            "label": t("deployment_reference"),
                            "field": "references",
                        },
                    ],
                    rows=[{"folder": row.folder, "references": row.references} for row in flagged],
                    row_key="folder",
                    pagination=10,
                ).classes("w-full").props("wrap-cells")
            with ui.expansion(t("deployment_setup_folders")).classes("w-full"):
                ui.table(
                    columns=[
                        {
                            "name": field,
                            "field": field,
                            "label": t(label),
                            "align": "left",
                            "sortable": True,
                        }
                        for field, label in (
                            ("folder", "deployment_folder_name"),
                            ("deployment", "deployment_target"),
                            ("ordinary", "deployment_setup_ordinary"),
                            ("references", "deployment_reference"),
                            ("status", "deployment_status"),
                        )
                    ],
                    rows=[
                        {
                            "folder": row.folder,
                            "deployment": row.deployment,
                            "ordinary": row.ordinary,
                            "references": row.references,
                            "status": t("deployment_folder_unmatched")
                            if row.unmatched_reference
                            else "",
                        }
                        for row in sorted(
                            preview.folders,
                            key=lambda row: (not row.unmatched_reference, row.folder),
                        )
                    ],
                    row_key="folder",
                    pagination=10,
                ).classes("w-full")
        self.on_change()

    def render(self, on_create, on_create_and_review) -> None:
        with ui.card().classes("w-full q-mb-md"):
            ui.label(t("deployment_title")).classes("text-subtitle1 font-weight-medium")
            ui.label(t("deployment_slice_help")).classes("text-caption text-grey-6")
            self.status = ui.label("").classes("text-caption")
            self.attention = ui.label().classes("text-warning")
            self.attention.set_visibility(False)
            with ui.row().classes("w-full items-center gap-2"):
                self.create_and_review = ui.button(
                    t("deployment_setup_create_review"),
                    on_click=on_create_and_review,
                    icon="playlist_add_check",
                )
                self.create_and_review.set_visibility(False)
                self.create = ui.button(t("deployment_setup_create"), on_click=on_create)
            self.enable_creation(False)
            self.results = ui.column().classes("w-full")
