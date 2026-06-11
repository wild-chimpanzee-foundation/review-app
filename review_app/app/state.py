"""Application state, split across two scopes:

- Module-level globals (thresholds, auto_transcode, the data provider): one value
  for the whole server process, shared by every connected browser/user, persisted
  in the DB's app_settings table.
- ``app.storage.user`` (filters, queue, prefs, active project): per-browser session,
  persisted in NiceGUI's storage file keyed by the browser cookie.

When adding a setting, decide which scope it belongs to and keep its getter/setter
in the matching section.
"""

from typing import Any

from review_app.backend.utils import DEFAULT_REVIEW_THRESHOLD

_data_provider = None

GLOBAL_DEFAULTS: dict[str, Any] = {
    "blank_threshold": DEFAULT_REVIEW_THRESHOLD,
    "species_threshold": DEFAULT_REVIEW_THRESHOLD,
    "obj_detection_threshold": DEFAULT_REVIEW_THRESHOLD,
    "auto_transcode": True,
    "dark_mode": True,
    "language": "en",
    "autoplay": True,
    "muted": False,
    "tour_completed": False,
    "playback_speed": "1x",
}

_blank_threshold: float = GLOBAL_DEFAULTS["blank_threshold"]
_species_threshold: float = GLOBAL_DEFAULTS["species_threshold"]
_obj_detection_threshold: float = GLOBAL_DEFAULTS["obj_detection_threshold"]
_auto_transcode: bool = GLOBAL_DEFAULTS["auto_transcode"]

_DEFAULT_FILTERS: dict[str, Any] = {
    "search_query": "",
    "selected_camera": "All",
    "selected_species": [],
    "selected_possible_species": [],
    "selected_manual_blank": "All",
    "selected_model_blank": "All",
    "selected_behavior": [],
    "selected_model_behavior": [],
    "selected_annotation_status": "All",
    "selected_sort": "camera",
    "selected_sort_direction": "desc",
    "selected_is_review_later": False,
    "selected_annotator": [],
    "selected_multiple_annotators": False,
    "web_safe_only": False,
    "selected_needs_review": "All",
}


def get_data_provider():
    return _data_provider


def set_data_provider(dp):
    global _data_provider
    _data_provider = dp


def reset_app_state(keep_prefs: bool = True) -> None:
    global _blank_threshold, _species_threshold, _obj_detection_threshold, _auto_transcode
    global _data_provider
    _blank_threshold = GLOBAL_DEFAULTS["blank_threshold"]
    _species_threshold = GLOBAL_DEFAULTS["species_threshold"]
    _obj_detection_threshold = GLOBAL_DEFAULTS["obj_detection_threshold"]
    _auto_transcode = GLOBAL_DEFAULTS["auto_transcode"]
    _data_provider = None
    clear_session(keep_prefs=keep_prefs)


def clear_session(keep_prefs: bool = True) -> None:
    """Clear per-user session storage. Optionally preserve environment preferences."""
    try:
        from nicegui import app

        storage = app.storage.user
        if keep_prefs:
            # Keys to preserve across logout
            PREF_KEYS = {
                "dark_mode",
                "language",
                "autoplay",
                "muted",
                "tour_completed",
                "playback_speed",
            }
            saved = {k: v for k, v in storage.items() if k in PREF_KEYS}
            storage.clear()
            storage.update(saved)
        else:
            storage.clear()
    except Exception:
        pass


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw == "True"


def load_settings_from_db(dp) -> None:
    """Load server-global settings (thresholds). Call once at startup."""
    global _blank_threshold, _species_threshold, _obj_detection_threshold, _auto_transcode
    raw_blank = dp.get_setting("blank_threshold")
    _blank_threshold = (
        float(raw_blank) if raw_blank is not None else GLOBAL_DEFAULTS["blank_threshold"]
    )
    raw_species = dp.get_setting("species_threshold")
    _species_threshold = (
        float(raw_species) if raw_species is not None else GLOBAL_DEFAULTS["species_threshold"]
    )
    raw_obj = dp.get_setting("obj_detection_threshold")
    _obj_detection_threshold = (
        float(raw_obj) if raw_obj is not None else GLOBAL_DEFAULTS["obj_detection_threshold"]
    )
    _auto_transcode = _parse_bool(
        dp.get_setting("auto_transcode"), GLOBAL_DEFAULTS["auto_transcode"]
    )


def load_session_defaults(dp=None) -> None:
    """Prime per-session storage with DB defaults. Only sets keys not already present."""
    from nicegui import app

    storage = app.storage.user
    for key in [
        "dark_mode",
        "language",
        "autoplay",
        "muted",
        "tour_completed",
        "playback_speed",
    ]:
        if key not in storage:
            raw = dp.get_setting(key) if dp else None
            default = GLOBAL_DEFAULTS[key]
            if isinstance(default, bool):
                storage[key] = _parse_bool(raw, default)
            else:
                storage[key] = raw if raw is not None else default

    if "active_project_id" not in storage:
        storage["active_project_id"] = dp.get_setting("active_project_id") if dp else None


def save_user_prefs_to_db(dp) -> None:
    """No-op: prefs now live in app.storage.user, not the DB."""
    pass


def set_active_project(project_id: str | None) -> None:
    from nicegui import app

    app.storage.user["active_project_id"] = project_id


def get_active_project_id() -> str | None:
    from nicegui import app

    return app.storage.user.get("active_project_id")


def get_queue():
    from nicegui import app

    return app.storage.user.get("video_queue", [])


def set_queue(queue: list):
    from nicegui import app

    app.storage.user["video_queue"] = list(queue)


def get_current_idx():
    from nicegui import app

    return app.storage.user.get("current_video_idx", 0)


def set_current_idx(idx: int):
    from nicegui import app

    app.storage.user["current_video_idx"] = idx


def get_selections():
    from nicegui import app

    return list(app.storage.user.get("review_selections", []))


def set_selections(selections: list):
    from nicegui import app

    app.storage.user["review_selections"] = list(selections)


def get_filters():
    from nicegui import app

    filters = app.storage.user.get("filters", {})
    if not filters:
        return _DEFAULT_FILTERS.copy()
    return dict(filters)


def update_filters(**kwargs):
    from nicegui import app

    filters = dict(app.storage.user.get("filters", {}))
    if not filters:
        filters = _DEFAULT_FILTERS.copy()
    filters.update(kwargs)
    app.storage.user["filters"] = filters


def reset_filters() -> None:
    from nicegui import app

    app.storage.user["filters"] = _DEFAULT_FILTERS.copy()


def get_annotator_name() -> str:
    from nicegui import app

    return app.storage.user.get("annotator_name", "")


def set_annotator_name(name: str) -> None:
    from nicegui import app

    app.storage.user["annotator_name"] = name


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


def get_obj_detection_threshold() -> float:
    return _obj_detection_threshold


def set_obj_detection_threshold(value: float) -> None:
    global _obj_detection_threshold
    _obj_detection_threshold = value
    if dp := get_data_provider():
        dp.set_setting("obj_detection_threshold", value)


def get_playback_speed() -> str:
    from nicegui import app

    return app.storage.user.get("playback_speed", "1x")


def set_playback_speed(speed: str):
    from nicegui import app

    app.storage.user["playback_speed"] = speed


def is_autoplay() -> bool:
    from nicegui import app

    return app.storage.user.get("autoplay", True)


def set_autoplay(enabled: bool):
    from nicegui import app

    app.storage.user["autoplay"] = enabled


def is_muted() -> bool:
    from nicegui import app

    return app.storage.user.get("muted", False)


def set_muted(enabled: bool):
    from nicegui import app

    app.storage.user["muted"] = enabled


def is_auto_transcode() -> bool:
    return _auto_transcode


def set_auto_transcode(enabled: bool):
    global _auto_transcode
    _auto_transcode = enabled
    if dp := get_data_provider():
        dp.set_setting("auto_transcode", enabled)


def is_dark_mode() -> bool:
    try:
        from nicegui import app

        return app.storage.user.get("dark_mode", True)
    except Exception:
        return True


def set_dark_mode(enabled: bool):
    from nicegui import app

    app.storage.user["dark_mode"] = enabled


def get_language() -> str:
    try:
        from nicegui import app

        return app.storage.user.get("language", "en")
    except Exception:
        return "en"


def set_language(lang: str) -> None:
    from nicegui import app

    app.storage.user["language"] = lang


def is_tour_completed() -> bool:
    from nicegui import app

    return app.storage.user.get("tour_completed", False)


def set_tour_completed(value: bool = True) -> None:
    from nicegui import app

    app.storage.user["tour_completed"] = value


def get_state_val(key: str, default: Any = None) -> Any:
    from nicegui import app

    return app.storage.user.get("session", {}).get(key, default)


def set_state_val(key: str, value: Any):
    from nicegui import app

    session = dict(app.storage.user.get("session", {}))
    session[key] = value
    app.storage.user["session"] = session
