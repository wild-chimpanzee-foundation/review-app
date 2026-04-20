from typing import Any

from nicegui import app

# Data provider is shared across all sessions as it manages the local database connection
_data_provider = None


def get_data_provider():
    return _data_provider


def set_data_provider(dp):
    global _data_provider
    _data_provider = dp


def _get_user_state() -> dict[str, Any]:
    """Ensure basic state structure exists in user storage."""
    if "filters" not in app.storage.user:
        app.storage.user["filters"] = {
            "search_query": "",
            "selected_camera": "All",
            "selected_species": "All",
            "selected_possible_species": "All",
            "selected_blank_non_blank": "All",
            "selected_behavior": "All",
            "include_unranked": True,
            "web_safe_only": False,
        }
    if "video_queue" not in app.storage.user:
        app.storage.user["video_queue"] = []
    if "current_video_idx" not in app.storage.user:
        app.storage.user["current_video_idx"] = 0
    if "review_selections" not in app.storage.user:
        app.storage.user["review_selections"] = []
    if "annotator_name" not in app.storage.user:
        app.storage.user["annotator_name"] = "default"
    if "video_playback_time" not in app.storage.user:
        app.storage.user["video_playback_time"] = 0.0
    if "playback_speed" not in app.storage.user:
        app.storage.user["playback_speed"] = "1x"
    if "autoplay" not in app.storage.user:
        app.storage.user["autoplay"] = True
    if "muted" not in app.storage.user:
        app.storage.user["muted"] = True
    if "dark_mode" not in app.storage.user:
        app.storage.user["dark_mode"] = True
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
    return _get_user_state().get("annotator_name", "default")


def set_annotator_name(name: str):
    _get_user_state()["annotator_name"] = name


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


def is_dark_mode():
    return _get_user_state().get("dark_mode", True)


def set_dark_mode(enabled: bool):
    _get_user_state()["dark_mode"] = enabled


def get_state_val(key: str, default: Any = None) -> Any:
    return _get_user_state().get(key, default)


def set_state_val(key: str, value: Any):
    _get_user_state()[key] = value
