"""Legacy CSV intake, intentionally separate from assignment returns."""

import io

import pandas as pd
from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import user_error_message


def render_legacy_annotations_import(dp, project_id):
    ui.label(t("data_legacy_upload_warning")).classes("text-warning text-body2")
    # ── Batch Annotation Import ───────────────────────────────────────────
    with ui.card().classes("full-width q-mb-lg"):
        batch_status = ui.label("").classes("text-body2")
        _batch_state: dict = {"total_imported": 0, "total_skipped": 0, "file_count": 0}

        async def handle_batch_upload(e):
            try:
                if get_active_project_id() != project_id:
                    raise ValueError(t("handoff_project_changed"))
                content = (await e.file.read()).decode("utf-8")
                df = pd.read_csv(io.StringIO(content))
                if get_active_project_id() != project_id:
                    raise ValueError(t("handoff_project_changed"))
                result = await run.io_bound(dp.import_annotations_csv, df, project_id, "append")
                _batch_state["total_imported"] += result.get("imported", 0)
                _batch_state["total_skipped"] += len(result.get("skipped", []))
                _batch_state["file_count"] += 1
                batch_status.set_text(
                    t(
                        "batch_import_summary",
                        files=_batch_state["file_count"],
                        imported=_batch_state["total_imported"],
                        skipped=_batch_state["total_skipped"],
                    )
                )
            except Exception as exc:
                ui.notify(f"{e.file.name}: {user_error_message(exc)}", type="negative")

        ui.upload(
            on_upload=handle_batch_upload,
            multiple=True,
            label=t("batch_import_btn"),
            auto_upload=True,
        ).props("accept=.csv")
