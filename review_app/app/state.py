from typing import Any

state: dict[str, Any] = {
    "data_provider": None,
    "config_loaded": False,
    "video_queue": [],
    "current_video_idx": 0,
    "review_selections": [],
    "annotator_name": "default",
    "video_playback_time": 0.0,
    "filters": {
        "search_query": "",
        "selected_camera": "All",
        "selected_species": "All",
        "selected_possible_species": "All",
        "selected_blank_non_blank": "All",
        "selected_behavior": "All",
        "include_unranked": True,
    },
}


def get_video_playback_time():
    return state.get("video_playback_time", 0.0)


def set_video_playback_time(time: float):
    state["video_playback_time"] = time


def get_data_provider():
    return state.get("data_provider")


def set_data_provider(dp):
    state["data_provider"] = dp
    state["config_loaded"] = True


def get_queue():
    return state.get("video_queue", [])


def set_queue(queue: list):
    state["video_queue"] = queue


def get_current_idx():
    return state.get("current_video_idx", 0)


def set_current_idx(idx: int):
    state["current_video_idx"] = idx


def get_selections():
    return list(state.get("review_selections", []))


def set_selections(selections: list):
    state["review_selections"] = list(selections)


def get_filters():
    return state.get("filters", {}).copy()


def update_filters(**kwargs):
    state["filters"].update(kwargs)
