import io

import pandas as pd
from nicegui import run, ui

from review_app.app.state import get_active_project_id, get_state_val, set_state_val
from review_app.app.translations import t
from review_app.app.utils import user_error_message


def setup_annotations_tab(dp, loading_dialog) -> None:
    with ui.row().classes("w-full gap-md q-mt-md items-start"):
        with ui.card().classes("col q-pa-md"):
            ui.label(t("upload_csv")).classes("text-subtitle2 font-weight-medium q-mb-xs")
            ui.label(t("annotation_import_desc")).classes("text-caption  q-mb-md")

            with ui.row().classes("items-center gap-sm q-mb-md"):
                ui.label(t("csv_mode_label")).classes("text-caption")
                mode_toggle = ui.toggle(
                    {
                        "override": t("mode_override"),
                        "append": t("mode_append"),
                    },
                    value=get_state_val("manual_import_mode") or "override",
                ).props("dense size=sm")
                mode_toggle.on_value_change(
                    lambda: set_state_val("manual_import_mode", mode_toggle.value)
                )

            annotation_import_status = ui.label("").classes("text-body2 ")

            async def handle_annotation_upload(e):
                try:
                    content = await e.file.read()
                    df = pd.read_csv(io.BytesIO(content))
                    result = await run.io_bound(
                        dp.import_annotations_csv,
                        df,
                        get_active_project_id(),
                        mode=get_state_val("manual_import_mode") or "override",
                    )
                    msg = t("imported_annotations", count=result["imported"])
                    if result["skipped"]:
                        msg += t("skipped_annotations", count=len(result["skipped"]))
                    annotation_import_status.set_text(msg)
                    ui.notify(msg, type="positive")
                except Exception as exc:
                    ui.notify(
                        t("import_failed", error=user_error_message(exc)),
                        type="negative",
                    )

            ui.upload(
                on_upload=handle_annotation_upload,
                multiple=False,
                label=t("choose_annotations_csv"),
                auto_upload=True,
            ).props("accept=.csv")

        with ui.card().classes("col q-pa-md"):
            ui.label(t("export_annotations")).classes("text-subtitle2 font-weight-medium q-mb-xs")
            ui.label(t("annotation_export_desc")).classes("text-caption q-mb-md")

            async def do_export():
                try:
                    df = await run.io_bound(
                        dp.export_annotations_csv,
                        get_active_project_id(),
                    )
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    ui.download(csv_bytes, "annotations.csv")
                except Exception as exc:
                    ui.notify(
                        t("export_failed", error=user_error_message(exc)),
                        type="negative",
                    )

            ui.button(t("export_annotations"), icon="download", on_click=do_export).props(
                "flat color=primary"
            )
