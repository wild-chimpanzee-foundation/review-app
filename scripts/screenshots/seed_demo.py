"""Build a deterministic demo database for documentation screenshots.

Run via the orchestrator (``scripts/screenshots/capture.sh``), which sets
``XDG_DATA_HOME`` so this writes to a throwaway location instead of the real
user data dir. It can also be run standalone:

    XDG_DATA_HOME=/tmp/review-demo/data \
        DEMO_VIDEO_DIR=/tmp/review-demo/videos \
        uv run python scripts/screenshots/seed_demo.py

The dataset is intentionally small but exercises every dashboard panel:
multiple cameras, blank/non-blank/unlabeled videos, model predictions, manual
labels by several annotators, a review-later bookmark, tags, GPS locations and
camera-based work distribution.
"""

from __future__ import annotations

import os
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from review_app.app.config import get_default_db_path
from review_app.backend.db.models import ModelAnnotation
from review_app.backend.provider.local_data_provider import LocalDataProvider

# Real scientific names from the bundled species catalogue so the app's species
# display map resolves them to common names.
SPECIES = [
    "Pan troglodytes verus",  # Western chimpanzee — the species in the sample footage
    "Atilax paludinosus",  # Marsh mongoose
    "Aonyx capensis",  # African clawless otter
    "Atherurus africanus",  # African brush-tailed porcupine
]

# (camera, filename, seconds, lat, lon, source_start_sec)
# `seconds` is the clip length; `source_start_sec` is where to cut it from when a
# real source video is provided (see DEMO_SOURCE_VIDEO), spread across the source
# so each clip looks distinct.
VIDEOS = [
    ("CAM01", "20200511_063000.mp4", 8, 7.6420, -8.3530, 0),
    ("CAM01", "20200511_071500.mp4", 6, 7.6420, -8.3530, 8),
    ("CAM01", "20200514_193000.mp4", 9, 7.6420, -8.3530, 16),
    ("CAM02", "20200513_054500.mp4", 7, 7.6610, -8.3215, 25),
    ("CAM02", "20200513_220000.mp4", 5, 7.6610, -8.3215, 33),
    ("CAM03", "20200515_081200.mp4", 8, 7.6038, -8.3702, 40),
    ("CAM03", "20200515_154500.mp4", 6, 7.6038, -8.3702, 49),
]


def seed_builtin_tags(dp) -> None:
    """Insert the built-in tags. Fresh DBs are stamped current and skip the
    migration that normally seeds them, so do it manually (mirrors tests)."""
    from sqlalchemy import text

    builtin = [
        ("fire", "Fire", "Feu", "deep-orange", "local_fire_department"),
        ("nice_shot", "Nice Shot", "Belle image", "amber", "star"),
        ("broken_metadata", "Broken Metadata", "Métadonnées corrompues", "red", "report_problem"),
    ]
    with dp.engine.begin() as conn:
        for key, name_en, name_fr, color, icon in builtin:
            if not conn.execute(text("SELECT 1 FROM tags WHERE key=:k"), {"k": key}).fetchone():
                conn.execute(
                    text(
                        "INSERT INTO tags (id,key,name_en,name_fr,color,icon,is_custom) "
                        "VALUES (:id,:key,:name_en,:name_fr,:color,:icon,0)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "key": key,
                        "name_en": name_en,
                        "name_fr": name_fr,
                        "color": color,
                        "icon": icon,
                    },
                )


def make_clip(path: Path, seconds: int, source: Path | None, start: int) -> None:
    """Write a tiny, web-safe H.264 clip.

    With a real ``source`` video, cut a ``seconds``-long segment starting at
    ``start`` and re-encode it web-safe (yuv420p, faststart, no audio). Without
    one, fall back to a generated test pattern so the pipeline still runs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if source is not None:
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-i",
            str(source),
            "-t",
            str(seconds),
            "-vf",
            "scale=1280:720",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-an",
            "-movflags",
            "+faststart",
            str(path),
        ]
    else:
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={seconds}:size=640x480:rate=12",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-movflags",
            "+faststart",
            str(path),
        ]
    subprocess.run(cmd, check=True)


def main() -> None:
    video_root = Path(os.environ.get("DEMO_VIDEO_DIR", "/tmp/review-demo/videos"))
    video_root.mkdir(parents=True, exist_ok=True)

    source_env = os.environ.get("DEMO_SOURCE_VIDEO", "").strip()
    source = Path(source_env) if source_env else None
    if source is not None and not source.exists():
        print(f"  ! DEMO_SOURCE_VIDEO not found ({source}); using a test pattern instead")
        source = None

    kind = "segments of the sample video" if source else "test-pattern clips"
    print(f"Rendering {len(VIDEOS)} {kind} into {video_root} ...")
    for cam, name, secs, _lat, _lon, start in VIDEOS:
        make_clip(video_root / cam / name, secs, source, start)

    db_path = get_default_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    print(f"Building demo database at {db_path} ...")

    dp = LocalDataProvider()
    seed_builtin_tags(dp)

    project = dp.create_project("Taï Forest — Survey 2024", str(video_root))
    pid = project.id
    dp.sync_videos(progress_callback=None, video_dir=video_root, active_project_id=pid)

    dp.set_project_species(pid, SPECIES)

    # Index synced videos by their on-disk name.
    ids = dp.get_video_queue({}, active_project_id=pid)
    by_name = {Path(dp.get_video_detail(v)["video_path"]).name: v for v in ids}

    # GPS + recorded-at metadata straight onto the rows.
    base = datetime(2020, 5, 11, tzinfo=timezone.utc)
    from review_app.backend.db.models import Video

    with dp.Session() as s:
        for i, (_cam, name, _secs, lat, lon, _start) in enumerate(VIDEOS):
            v = s.get(Video, by_name[name])
            v.latitude = lat
            v.longitude = lon
            v.created_at = base + timedelta(days=i, hours=i)
        s.commit()

    # Annotators + camera-based distribution.
    for who in ("alice", "bob", "demo"):
        dp.add_annotator(who)
    dp.apply_distribution(
        pid,
        {"alice": ["CAM01"], "bob": ["CAM02"], "demo": ["CAM03"]},
    )

    # Manual labels (species names must be project-valid; tags are behaviour keys).
    def label(name, species, *, blank=False, by="demo", behaviours=None, later=False):
        sel = (
            []
            if blank
            else [
                {
                    "species": species,
                    "tags": behaviours or [],
                    "count": 1,
                    "start_sec": 0.0,
                    "end_sec": 4.0,
                    "labeled_by": by,
                }
            ]
        )
        dp.update_manual_review(by_name[name], sel, is_blank=blank, active_project_id=pid)
        if later:
            dp.set_review_later(by_name[name], True)

    label(
        "20200511_063000.mp4", "Pan troglodytes verus", by="alice", behaviours=["reacts_to_camera"]
    )
    label("20200511_071500.mp4", None, blank=True, by="alice")
    label("20200514_193000.mp4", "Pan troglodytes verus", by="alice")
    label(
        "20200513_054500.mp4",
        "Atilax paludinosus",
        by="bob",
        behaviours=["reacts_to_camera"],
        later=True,
    )
    label("20200515_081200.mp4", "Pan troglodytes verus", by="demo")
    # CAM02/2200 and CAM03/1545 intentionally left unlabeled.

    # A couple of tags for colour.
    dp.set_video_tags(by_name["20200514_193000.mp4"], ["nice_shot"])
    dp.set_video_tags(by_name["20200513_220000.mp4"], ["broken_metadata"])

    # Model predictions so the AI-prediction panels and confidence chips populate.
    ts = base
    preds = [
        ("20200511_063000.mp4", "species", "Pan troglodytes verus", 0.96),
        ("20200511_063000.mp4", "blank_non_blank", "non_blank", 0.91),
        ("20200511_071500.mp4", "blank_non_blank", "blank", 0.94),
        ("20200514_193000.mp4", "species", "Pan troglodytes verus", 0.89),
        ("20200513_054500.mp4", "species", "Atilax paludinosus", 0.79),
        ("20200515_081200.mp4", "species", "Pan troglodytes verus", 0.93),
    ]
    with dp.Session() as s:
        s.add_all(
            ModelAnnotation(
                id=str(uuid.uuid4()),
                video_id=by_name[name],
                project_id=pid,
                annotation_type=atype,
                model_name="demo_model",
                value_text=value,
                probability=prob,
                updated_at=ts,
            )
            for name, atype, value, prob in preds
        )
        s.commit()

    print("Demo database ready.")


if __name__ == "__main__":
    main()
