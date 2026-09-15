import platform

from nicegui import ui

from review_app.app.translations import t

VIDEO_TOOLS_DOCS_URL = (
    "https://wild-chimpanzee-foundation.github.io/review-app/getting-started/#ffmpeg"
)

_INSTALL_COMMANDS = {
    "Darwin": "brew install ffmpeg",
    "Windows": "winget install ffmpeg OR download from https://ffmpeg.org/download.html",
    "Linux": "sudo apt install ffmpeg",
}


def get_video_tools_install_command() -> str:
    return _INSTALL_COMMANDS.get(platform.system(), _INSTALL_COMMANDS["Linux"])


class VideoToolsWarning:
    """Reusable warning for an incomplete or broken FFmpeg installation."""

    def __init__(self) -> None:
        self.card = ui.card().classes("full-width q-mb-md bg-negative text-white")
        self.card.visible = False
        with self.card:
            self.title = ui.label("").classes("text-subtitle1 font-weight-bold q-mb-xs")
            ui.label(t("video_tools_install_instructions")).classes("text-caption q-mb-sm")
            ui.code(get_video_tools_install_command()).classes("full-width")
            ui.link(t("ffmpeg_docs_link"), VIDEO_TOOLS_DOCS_URL, new_tab=True).classes(
                "text-white text-caption q-mt-sm"
            )

    def show(self, missing: tuple[str, ...]) -> None:
        self.title.text = t("video_tools_not_found_title", tools=", ".join(missing))
        self.card.visible = True
