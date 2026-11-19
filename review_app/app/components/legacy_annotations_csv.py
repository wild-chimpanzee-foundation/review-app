"""Shared project-wide CSV append for the annotations and return screens."""

import io

import pandas as pd
from nicegui import run


def parse_annotations_csv(content: bytes) -> pd.DataFrame:
    return pd.read_csv(io.BytesIO(content), encoding="utf-8-sig")


async def append_annotations_csv(dp, project_id, frame):
    return await run.io_bound(dp.import_annotations_csv, frame, project_id, "append")
