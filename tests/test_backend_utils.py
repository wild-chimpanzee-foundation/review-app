from pathlib import Path

import pandas as pd
import pytest

from review_app.backend.utils import (
    df_to_records,
    get_video_mime,
    make_serializable,
    needs_browser_transcode,
)


def test_get_video_mime():
    assert get_video_mime("video.mp4") == "video/mp4"
    assert get_video_mime("movie.AVI") == "video/x-msvideo"
    assert get_video_mime("unknown.xyz") == "video/mp4"  # Default


def test_make_serializable():
    from datetime import datetime

    dt = datetime(2023, 1, 1, 12, 0, 0)
    assert make_serializable(dt) == "2023-01-01T12:00:00"
    assert make_serializable(None) is None
    assert make_serializable(123) == 123
    assert make_serializable("test") == "test"


def test_df_to_records():
    df = pd.DataFrame([{"a": 1, "b": 2}, {"a": 3, "b": 4}])
    records = df_to_records(df)
    assert len(records) == 2
    assert records[0]["a"] == 1
    assert records[1]["b"] == 4

    # Test limit
    records_limit = df_to_records(df, limit=1)
    assert len(records_limit) == 1


def test_needs_browser_transcode():
    # web_safe is True
    assert needs_browser_transcode({"is_web_safe": True}) is False

    # Transcoded path exists
    # Note: we should mock Path.exists if we want to be fully independent
    # but for now we'll just test the logic with placeholders

    # web_safe is False
    assert needs_browser_transcode({"is_web_safe": False, "video_path": "test.mp4"}) is True

    # web_safe is None, extension is safe
    assert (
        needs_browser_transcode({"is_web_safe": None, "video_path": "test.mp4"}) is False
    )

    # web_safe is None, extension is unsafe
    assert (
        needs_browser_transcode({"is_web_safe": None, "video_path": "test.avi"}) is True
    )
