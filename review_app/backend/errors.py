from __future__ import annotations


class AppError(Exception):
    """Base class for application errors with i18n support via ``user_message_key``."""

    user_message_key: str = "error_generic"

    def __init__(self, detail: str = "", *, user_message_key: str | None = None):
        self.detail = detail
        if user_message_key is not None:
            self.user_message_key = user_message_key
        super().__init__(detail)


class VideoError(AppError):
    user_message_key = "video_error_generic"


class DataImportError(AppError):
    user_message_key = "data_import_error_generic"


class SpeciesError(AppError):
    user_message_key = "species_error_generic"
