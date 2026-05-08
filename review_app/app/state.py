from typing import Any

_data_provider = None

_annotator_name: str = "default"
_blank_threshold: float = 0.75
_species_threshold: float = 0.75
_active_project_id: str | None = None
_dark_mode: bool = True
_language: str = "en"
_playback_speed: str = "1x"
_autoplay: bool = True
_muted: bool = False
_auto_transcode: bool = True
_tour_completed: bool = False

_filters: dict[str, Any] = {}
_video_queue: list = []
_current_video_idx: int = 0
_review_selections: list = []
_session: dict[str, Any] = {}

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


def reset_app_state() -> None:
    global _annotator_name, _blank_threshold, _species_threshold, _active_project_id
    global _dark_mode, _language, _playback_speed, _autoplay, _muted, _auto_transcode
    global _tour_completed, _data_provider
    global _filters, _video_queue, _current_video_idx, _review_selections, _session
    _active_project_id = None
    _annotator_name = "default"
    _blank_threshold = 0.75
    _species_threshold = 0.75
    _dark_mode = True
    _language = "en"
    _playback_speed = "1x"
    _autoplay = True
    _muted = False
    _auto_transcode = True
    _tour_completed = False
    _data_provider = None
    _filters = _DEFAULT_FILTERS.copy()
    _video_queue = []
    _current_video_idx = 0
    _review_selections = []
    _session = {}


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw == "True"


def load_settings_from_db(dp) -> None:
    global _annotator_name, _blank_threshold, _species_threshold, _active_project_id
    global _dark_mode, _language, _autoplay, _muted, _auto_transcode
    global _tour_completed
    _annotator_name = dp.get_setting("annotator_name", "default")
    raw_blank = dp.get_setting("blank_threshold")
    _blank_threshold = float(raw_blank) if raw_blank is not None else 0.75
    raw_species = dp.get_setting("species_threshold")
    _species_threshold = float(raw_species) if raw_species is not None else 0.75
    _active_project_id = dp.get_setting("active_project_id")
    _dark_mode = _parse_bool(dp.get_setting("dark_mode"), True)
    _language = dp.get_setting("language", "en")
    _autoplay = _parse_bool(dp.get_setting("autoplay"), True)
    _muted = _parse_bool(dp.get_setting("muted"), True)
    _auto_transcode = _parse_bool(dp.get_setting("auto_transcode"), True)
    _tour_completed = _parse_bool(dp.get_setting("tour_completed"), False)


def save_user_prefs_to_db(dp) -> None:
    """Flush current session preferences into a freshly created DB."""
    for key, val in [
        ("dark_mode", _dark_mode),
        ("language", _language),
        ("autoplay", _autoplay),
        ("muted", _muted),
        ("auto_transcode", _auto_transcode),
        ("tour_completed", _tour_completed),
    ]:
        dp.set_setting(key, val)


def set_active_project(project_id: str | None) -> None:
    global _active_project_id
    _active_project_id = project_id
    if dp := get_data_provider():
        dp.set_setting("active_project_id", project_id)


def get_active_project_id() -> str | None:
    return _active_project_id


def get_queue():
    return _video_queue


def set_queue(queue: list):
    global _video_queue
    _video_queue = queue


def get_current_idx():
    return _current_video_idx


def set_current_idx(idx: int):
    global _current_video_idx
    _current_video_idx = idx


def get_selections():
    return list(_review_selections)


def set_selections(selections: list):
    global _review_selections
    _review_selections = list(selections)


def get_filters():
    if not _filters:
        return _DEFAULT_FILTERS.copy()
    return _filters.copy()


def update_filters(**kwargs):
    global _filters
    if not _filters:
        _filters.update(_DEFAULT_FILTERS)
    _filters.update(kwargs)


def reset_filters() -> None:
    global _filters
    _filters = _DEFAULT_FILTERS.copy()


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
    return _playback_speed


def set_playback_speed(speed: str):
    global _playback_speed
    _playback_speed = speed


def is_autoplay():
    return _autoplay


def set_autoplay(enabled: bool):
    global _autoplay
    _autoplay = enabled
    if dp := get_data_provider():
        dp.set_setting("autoplay", enabled)


def is_muted():
    return _muted


def set_muted(enabled: bool):
    global _muted
    _muted = enabled
    if dp := get_data_provider():
        dp.set_setting("muted", enabled)


def is_auto_transcode():
    return _auto_transcode


def set_auto_transcode(enabled: bool):
    global _auto_transcode
    _auto_transcode = enabled
    if dp := get_data_provider():
        dp.set_setting("auto_transcode", enabled)


def is_dark_mode():
    return _dark_mode


def set_dark_mode(enabled: bool):
    global _dark_mode
    _dark_mode = enabled
    if dp := get_data_provider():
        dp.set_setting("dark_mode", enabled)


def get_language() -> str:
    return _language


def set_language(lang: str) -> None:
    global _language
    _language = lang
    if dp := get_data_provider():
        dp.set_setting("language", lang)


def is_tour_completed() -> bool:
    return _tour_completed


def set_tour_completed(value: bool = True) -> None:
    global _tour_completed
    _tour_completed = value
    if dp := get_data_provider():
        dp.set_setting("tour_completed", value)


def get_state_val(key: str, default: Any = None) -> Any:
    return _session.get(key, default)


def set_state_val(key: str, value: Any):
    _session[key] = value
