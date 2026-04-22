from typing import Any

from nicegui import app

from review_app.app.config import update_config_key

# Data provider is shared across all sessions as it manages the local database connection
_data_provider = None


def get_data_provider():
    return _data_provider


def set_data_provider(dp):
    global _data_provider
    _data_provider = dp


# Persistent user preferences (backed by config.yaml)
_dark_mode: bool = True
_language: str = "en"
_annotator_name: str = "default"


def init_user_prefs(dark_mode: bool, language: str, annotator_name: str) -> None:
    """Initialize persistent preferences from configuration at startup."""
    global _dark_mode, _language, _annotator_name
    _dark_mode = dark_mode
    _language = language
    _annotator_name = annotator_name


def _get_user_state() -> dict[str, Any]:
    """Ensure basic session-scoped state structure exists in user storage."""
    if "filters" not in app.storage.user:
        app.storage.user["filters"] = {
            "search_query": "",
            "selected_camera": "All",
            "selected_species": "All",
            "selected_possible_species": "All",
            "selected_blank_non_blank": "All",
            "selected_behavior": "All",
            "selected_review_status": "All",
            "selected_sort": "priority",
            "selected_sort_direction": "desc",
            "include_unranked": True,
            "web_safe_only": False,
            "blank_threshold": 0.75,
            "species_threshold": 0.75,
        }
    if "video_queue" not in app.storage.user:
        app.storage.user["video_queue"] = []
    if "current_video_idx" not in app.storage.user:
        app.storage.user["current_video_idx"] = 0
    if "review_selections" not in app.storage.user:
        app.storage.user["review_selections"] = []
    if "video_playback_time" not in app.storage.user:
        app.storage.user["video_playback_time"] = 0.0
    if "playback_speed" not in app.storage.user:
        app.storage.user["playback_speed"] = "1x"
    if "autoplay" not in app.storage.user:
        app.storage.user["autoplay"] = True
    if "muted" not in app.storage.user:
        app.storage.user["muted"] = True
    if "auto_transcode" not in app.storage.user:
        app.storage.user["auto_transcode"] = True
    return app.storage.user


def get_video_playback_time():
    return _get_user_state().get("video_playback_time", 0.0)


def set_video_playback_time(time: float):
    _get_user_state()["video_playback_time"] = time


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


def get_annotator_name():
    return _annotator_name


def set_annotator_name(name: str):
    global _annotator_name
    _annotator_name = name
    update_config_key("annotator_name", name)


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


def is_auto_transcode():
    return _get_user_state().get("auto_transcode", True)


def set_auto_transcode(enabled: bool):
    _get_user_state()["auto_transcode"] = enabled


def is_dark_mode():
    return _dark_mode


def set_dark_mode(enabled: bool):
    global _dark_mode
    _dark_mode = enabled
    update_config_key("dark_mode", enabled)


def get_language():
    return _language


def set_language(lang: str):
    global _language
    _language = lang
    update_config_key("language", lang)


def get_state_val(key: str, default: Any = None) -> Any:
    return _get_user_state().get(key, default)


def set_state_val(key: str, value: Any):
    _get_user_state()[key] = value
