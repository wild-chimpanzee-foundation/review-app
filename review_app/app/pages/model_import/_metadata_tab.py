import io

import pandas as pd
from nicegui import run, ui

from review_app.app.state import get_active_project_id
from review_app.app.translations import t
from review_app.app.utils import user_error_message

_TEMPLATE_CSV = "path,created_at,latitude,longitude\nparent_dir/video.mp4,2024-06-01T08:30:00Z,46.9481,7.4474\n"


def setup_metadata_tab(dp) -> None:
    with ui.card().classes("q-pa-md q-mt-md"):
        ui.label(t("metadata_import_title")).classes("text-subtitle2 font-weight-medium q-mb-xs")
        ui.label(t("metadata_import_desc")).classes("text-caption q-mb-md")

        status_label = ui.label("").classes("text-body2")

        async def handle_upload(e):
            try:
                content = await e.file.read()
                df = pd.read_csv(io.BytesIO(content))
                result = await run.io_bound(
                    dp.import_video_metadata_csv,
                    df,
                    get_active_project_id(),
                )
                msg = t("metadata_imported", count=result["updated"])
                if result["skipped"]:
                    msg += t("metadata_skipped", count=len(result["skipped"]))
                status_label.set_text(msg)
                ui.notify(msg, type="positive")
            except Exception as exc:
                ui.notify(
                    t("import_failed", error=user_error_message(exc)),
                    type="negative",
                )

        ui.upload(
            on_upload=handle_upload,
            multiple=False,
            label=t("metadata_choose_csv"),
            auto_upload=True,
        ).props("accept=.csv")

        ui.button(
            "Download template",
            icon="download",
            on_click=lambda: ui.download(_TEMPLATE_CSV.encode(), "metadata_template.csv"),
        ).props("flat color=primary size=sm").classes("q-mt-sm")
