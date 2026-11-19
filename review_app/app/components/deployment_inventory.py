"""Small inventory creation slice, deliberately separate from footage assignment."""

from __future__ import annotations

from nicegui import run, ui

from review_app.app.translations import t
from review_app.app.utils import user_error_message
from review_app.backend.deployments import (
    CSV_TEMPLATE,
    MAX_CSV_BYTES,
    DeploymentInputError,
    preview_deployment_setup,
)


def render_inventory(rows: list[dict] | tuple[dict, ...]) -> None:
    columns = [
        {"name": name, "label": label, "field": name, "align": "left", "sortable": True}
        for name, label in (
            ("deployment_id", "deployment_id"),
            ("name", t("deployment_name")),
            ("camera_id", "camera_id"),
            ("installation_date", "installation_date"),
            ("retrieval_date", "retrieval_date"),
            ("folder_path", "folder_path"),
            ("transect_id", "transect_id"),
            ("habitat", "habitat"),
            ("notes", "notes"),
            ("source", t("deployment_source")),
        )
    ]
    display = [
        {
            key: value.isoformat() if hasattr(value, "isoformat") else value
            for key, value in row.items()
        }
        | {"source": t("deployment_source_" + row["source"])}
        for row in rows
    ]
    table = ui.table(
        columns=columns, rows=display, row_key="deployment_id", pagination=10
    ).classes("w-full")
    search = ui.input(t("deployment_search")).classes("w-full")
    search.bind_value(table, "filter")


class DeploymentSetup:
    """Own upload preview state; stale previews never enable project creation."""

    def __init__(self, get_root, on_change):
        self.get_root = get_root
        self.on_change = on_change
        self.raw = None
        self.preview = None
        self._preview_key = None
        self._revision = 0
        self._upload_revision = 0

    def _key(self):
        return self.get_root(), bool(self.infer.value)

    @property
    def ready(self) -> bool:
        return self.preview is not None and self._preview_key == self._key()

    def creation_kwargs(self) -> dict:
        if not self.ready:
            raise DeploymentInputError("deployment_error_required")
        return {
            "infer_from_folders": self.infer.value,
            "csv_data": None if self.infer.value else self.raw,
            "expected_preview": self.preview,
        }

    def invalidate(self) -> None:
        self._revision += 1
        self.preview = None
        self._preview_key = None
        self.results.clear()
        self.status.set_text("")
        self.on_change()

    async def refresh(self, _=None) -> None:
        self.invalidate()
        self.csv_controls.set_visibility(not self.infer.value)
        root, infer = self._key()
        if not root.strip() or (self.raw is None and not infer):
            return
        revision = self._revision
        key = self._key()
        self.status.set_text(t("deployment_validating"))
        try:
            preview = await run.io_bound(
                preview_deployment_setup,
                root,
                csv_data=None if infer else self.raw,
                infer_from_folders=infer,
            )
        except Exception as exc:
            if revision == self._revision:
                self.status.set_text(
                    t(exc.key, **exc.params)
                    if isinstance(exc, DeploymentInputError)
                    else user_error_message(exc)
                )
            return
        if revision != self._revision or key != self._key():
            return
        self.preview = preview
        self._preview_key = key
        inventory = preview.inventory
        self.status.set_text(t("deployment_count", count=len(inventory.rows)))
        with self.results:
            if inventory.ignored_columns:
                ui.label(t("deployment_ignored", columns=", ".join(inventory.ignored_columns)))
            if inventory.missing_folders:
                ui.label(
                    t("deployment_missing", folders=", ".join(inventory.missing_folders))
                ).classes("text-warning")
            ui.label(
                t(
                    "deployment_setup_counts",
                    matched=sum(
                        row.ordinary + row.references
                        for row in preview.folders
                        if len(row.candidates) == 1
                    ),
                    unresolved=sum(
                        row.ordinary + row.references
                        for row in preview.folders
                        if len(row.candidates) != 1
                    ),
                    empty=len(preview.empty_deployments),
                )
            )
            with ui.expansion(t("deployment_setup_folders"), value=True).classes("w-full"):
                rows = [
                    {
                        "folder": row.folder,
                        "status": t("deployment_unresolved")
                        if len(row.candidates) != 1
                        else row.candidates[0],
                        "candidates": ", ".join(row.candidates) if len(row.candidates) > 1 else "",
                        "ordinary": row.ordinary,
                        "references": row.references,
                    }
                    for row in sorted(
                        preview.folders, key=lambda row: (len(row.candidates) == 1, row.folder)
                    )
                ]
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
                            ("status", "deployment_target"),
                            ("candidates", "deployment_setup_competing"),
                            ("ordinary", "deployment_setup_ordinary"),
                            ("references", "deployment_reference"),
                        )
                    ],
                    rows=rows,
                    row_key="folder",
                    pagination=10,
                ).classes("w-full")
            if preview.empty_deployments:
                with ui.expansion(t("deployment_setup_empty"), icon="folder_off"):
                    ui.label(", ".join(preview.empty_deployments)).classes("break-words")
            with ui.expansion(t("deployment_title")).classes("w-full"):
                render_inventory(inventory.rows)
        self.on_change()

    async def _upload(self, event) -> None:
        self._upload_revision += 1
        revision = self._upload_revision
        self.invalidate()
        self.raw = None
        if event.file.size() > MAX_CSV_BYTES:
            self.status.set_text(t("deployment_error_size"))
            return
        raw = await event.file.read()
        if revision != self._upload_revision:
            return
        self.raw = raw
        await self.refresh()

    def render(self) -> None:
        with ui.card().classes("w-full q-mb-md"):
            ui.label(t("deployment_title")).classes("text-subtitle1 font-weight-medium")
            ui.label(t("deployment_slice_help")).classes("text-caption text-grey-6")
            with ui.expansion(t("deployment_advanced"), icon="tune").classes("w-full"):
                self.infer = ui.checkbox(
                    t("deployment_infer"), value=False, on_change=self.refresh
                )
                ui.label(t("deployment_infer_help")).classes("text-caption")
            with ui.column().classes("w-full") as self.csv_controls:
                ui.label(t("deployment_csv_help")).classes("text-caption")
                ui.button(
                    t("deployment_template"),
                    icon="download",
                    on_click=lambda: ui.download(CSV_TEMPLATE.encode(), "deployments.csv"),
                ).props("flat")
                ui.upload(
                    on_upload=self._upload, auto_upload=True, max_file_size=MAX_CSV_BYTES
                ).props(f'accept=.csv label="{t("deployment_upload")}"').classes("w-full")
            self.status = ui.label("").classes("text-caption")
            self.results = ui.column().classes("w-full")
