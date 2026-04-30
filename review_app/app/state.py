from typing import Any

from nicegui import app

_data_provider = None

_annotator_name: str = "default"
_blank_threshold: float = 0.75
_species_threshold: float = 0.75
_active_project_id: str | None = None


def get_data_provider():
    return _data_provider


def set_data_provider(dp):
    global _data_provider
    _data_provider = dp


def reset_app_state() -> None:
    """Clear all process-level state after a DB reset. Does not write to DB."""
    global _annotator_name, _blank_threshold, _species_threshold, _active_project_id
    _active_project_id = None
    _annotator_name = "default"
    _blank_threshold = 0.75
    _species_threshold = 0.75
    _data_provider = None


def load_settings_from_db(dp) -> None:
    """Initialize process-level settings from DB at startup."""
    global _annotator_name, _blank_threshold, _species_threshold, _active_project_id
    _annotator_name = dp.get_setting("annotator_name", "default")
    raw_blank = dp.get_setting("blank_threshold")
    _blank_threshold = float(raw_blank) if raw_blank is not None else 0.75
    raw_species = dp.get_setting("species_threshold")
    _species_threshold = float(raw_species) if raw_species is not None else 0.75
    _active_project_id = dp.get_setting("active_project_id")


def set_active_project(project_id: str | None) -> None:
    global _active_project_id
    _active_project_id = project_id
    if dp := get_data_provider():
        dp.set_setting("active_project_id", project_id)


def get_active_project_id() -> str | None:
    return _active_project_id


def _get_user_state() -> dict[str, Any]:
    """Ensure basic session-scoped state structure exists in user storage."""
    if "filters" not in app.storage.user:
        app.storage.user["filters"] = {
            "search_query": "",
            "selected_camera": "All",
            "selected_species": "All",
            "selected_possible_species": "All",
            "selected_manual_blank": "All",
            "selected_model_blank": "All",
            "selected_behavior": "All",
            "selected_model_behavior": "All",
            "selected_annotation_status": "All",
            "selected_sort": "camera",
            "selected_sort_direction": "desc",
            "selected_is_review_later": False,
            "web_safe_only": False,
            "selected_needs_review": "All",
        }
    if "video_queue" not in app.storage.user:
        app.storage.user["video_queue"] = []
    if "current_video_idx" not in app.storage.user:
        app.storage.user["current_video_idx"] = 0
    if "review_selections" not in app.storage.user:
        app.storage.user["review_selections"] = []
    if "playback_speed" not in app.storage.user:
        app.storage.user["playback_speed"] = "1x"
    if "autoplay" not in app.storage.user:
        app.storage.user["autoplay"] = True
    if "muted" not in app.storage.user:
        app.storage.user["muted"] = True
    if "auto_transcode" not in app.storage.user:
        app.storage.user["auto_transcode"] = True
    if "dark_mode" not in app.storage.user:
        app.storage.user["dark_mode"] = True
    if "language" not in app.storage.user:
        app.storage.user["language"] = "en"
    return app.storage.user


def get_queue():
    return _get_user_state().get("video_queue", [])


def set_queue(queue: list):
    _get_user_state()["video_queue"] = queue


def get_current_idx():
    return _get_user_state().get("current_video_idx", 0)


def set_current_idx(idx: int):
    _get_user_state()["current_video_idx"] = idx


def get_selections():
    return list(_get_user_state().get("review_selections", []))


def set_selections(selections: list):
    _get_user_state()["review_selections"] = list(selections)


def get_filters():
    return _get_user_state().get("filters", {}).copy()


def update_filters(**kwargs):
    _get_user_state()["filters"].update(kwargs)


def reset_filters() -> None:
    app.storage.user["filters"] = {
        "search_query": "",
        "selected_camera": "All",
        "selected_species": "All",
        "selected_possible_species": "All",
        "selected_manual_blank": "All",
        "selected_model_blank": "All",
        "selected_behavior": "All",
        "selected_model_behavior": "All",
        "selected_annotation_status": "All",
        "selected_is_review_later": False,
        "selected_sort": "camera",
        "selected_sort_direction": "desc",
        "web_safe_only": False,
        "selected_needs_review": "All",
    }


def get_annotator_name():
    return _annotator_name


def set_annotator_name(name: str) -> None:
    global _annotator_name
    _annotator_name = name
    if dp := get_data_provider():
        dp.set_setting("annotator_name", name)


def get_blank_threshold() -> float:
    return _blank_threshold


def set_blank_threshold(value: float) -> None:
    global _blank_threshold
    _blank_threshold = value
    if dp := get_data_provider():
        dp.set_setting("blank_threshold", value)


def get_species_threshold() -> float:
    return _species_threshold


def set_species_threshold(value: float) -> None:
    global _species_threshold
    _species_threshold = value
    if dp := get_data_provider():
        dp.set_setting("species_threshold", value)


def get_playback_speed():
    return _get_user_state().get("playback_speed", "1x")


def set_playback_speed(speed: str):
    _get_user_state()["playback_speed"] = speed


def is_autoplay():
    return _get_user_state().get("autoplay", True)


def set_autoplay(enabled: bool):
    _get_user_state()["autoplay"] = enabled


def is_muted():
    return _get_user_state().get("muted", True)


def set_muted(enabled: bool):
    _get_user_state()["muted"] = enabled


def is_reset_speed_on_seek():
    return _get_user_state().get("reset_speed_on_seek", True)


def set_reset_speed_on_seek(enabled: bool):
    _get_user_state()["reset_speed_on_seek"] = enabled


def is_auto_transcode():
    return _get_user_state().get("auto_transcode", True)


def set_auto_transcode(enabled: bool):
    _get_user_state()["auto_transcode"] = enabled


def is_dark_mode():
    return _get_user_state().get("dark_mode", True)


def set_dark_mode(enabled: bool):
    _get_user_state()["dark_mode"] = enabled


def get_state_val(key: str, default: Any = None) -> Any:
    return _get_user_state().get(key, default)


def set_state_val(key: str, value: Any):
    _get_user_state()[key] = value
