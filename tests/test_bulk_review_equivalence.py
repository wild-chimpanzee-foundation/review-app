"""apply_manual_reviews must match a per-video update_manual_review sequence exactly.

The bulk method is the import path's replacement for calling update_manual_review /
set_review_later / set_video_tags / assignment restore once per video. The single-video
methods stay untouched (the review UI uses them), so they serve as the reference
implementation here: two identical providers, one driven per video, one via the bulk
call, must end up with byte-identical tables.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


class _FakeDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz else FIXED_NOW.replace(tzinfo=None)


@pytest.fixture
def frozen_clock(monkeypatch):
    """Pin every timestamp source the review-write paths use, so dumps are comparable."""
    from review_app.backend.provider import assignment_service, base, tag_repository
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    monkeypatch.setattr(base.ProviderBase, "_utcnow_dt", staticmethod(lambda: FIXED_NOW))
    monkeypatch.setattr(LocalDataProvider, "_utcnow_dt", staticmethod(lambda: FIXED_NOW))
    monkeypatch.setattr(assignment_service, "datetime", _FakeDatetime)
    monkeypatch.setattr(tag_repository, "datetime", _FakeDatetime)


def _make_provider(tmp_path, monkeypatch, name):
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    db_dir = tmp_path / f"db_{name}"
    db_dir.mkdir()
    monkeypatch.setattr(
        "review_app.backend.provider.local_data_provider.get_user_data_dir", lambda: db_dir
    )
    video_dir = tmp_path / f"videos_{name}"
    for cam in ("cam_a", "cam_b"):
        (video_dir / cam).mkdir(parents=True)
        for stem in ("v1", "v2", "v3", "v4"):
            (video_dir / cam / f"{stem}.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    ids = {
        f"{Path(dp.get_video_detail(v)['video_path']).parent.name}/"
        f"{Path(dp.get_video_detail(v)['video_path']).stem}": v
        for v in dp.get_video_queue({}, active_project_id=None)
    }
    return dp, ids


def _dump(dp) -> dict:
    """All review-write tables with uuid keys replaced by stable semantic keys."""
    with dp.engine.connect() as conn:

        def q(sql):
            return [tuple(r) for r in conn.execute(text(sql)).fetchall()]

        vid_path = {
            v: Path(p).parent.name + "/" + Path(p).stem
            for v, p in q("SELECT video_id, video_path FROM videos")
        }
        sp_name = dict(q("SELECT id, scientific_name FROM species"))
        beh_key = dict(q("SELECT id, key FROM behaviors"))
        tag_key = dict(q("SELECT id, key FROM tags"))

        return {
            "video_labels": sorted(
                [vid_path[r[0]], r[1], r[2], str(r[3]), r[4]]
                for r in q(
                    "SELECT video_id, is_blank, labeled_by, labeled_at, review_later"
                    " FROM video_labels"
                )
            ),
            "individual_observations": sorted(
                [
                    vid_path[r[0]],
                    r[1],
                    sp_name.get(r[2]),
                    r[3],
                    r[4],
                    r[5],
                    r[6],
                    str(r[7]),
                    str(r[8]),
                ]
                for r in q(
                    "SELECT video_id, id, species_id, count, start_sec, end_sec,"
                    " labeled_by, labeled_at, updated_at FROM individual_observations"
                )
            ),
            "observation_tags": sorted(
                [vid_path[r[0]], r[1], beh_key.get(r[2])]
                for r in q("SELECT video_id, observation_id, behavior_id FROM observation_tags")
            ),
            "video_tags": sorted(
                [vid_path[r[0]], tag_key.get(r[1]), r[2], str(r[3])]
                for r in q("SELECT video_id, tag_id, tagged_by, tagged_at FROM video_tags")
            ),
            "video_assignments": sorted(
                [vid_path[r[0]], r[1], str(r[2])]
                for r in q("SELECT video_id, assigned_to, assigned_at FROM video_assignments")
            ),
            "annotators": sorted(r[0] for r in q("SELECT name FROM annotators")),
        }


def _seed(dp, ids):
    """Pre-existing state both providers share before the operation under test."""
    dp.update_manual_review(
        ids["cam_a/v1"],
        [
            {
                "id": 1,
                "species": "deer",
                "tags": ["grazing"],
                "count": 2,
                "start_sec": 1.0,
                "end_sec": 5.0,
                "labeled_by": "alice",
            },
            {"id": 2, "species": "fox", "tags": [], "start_sec": 3.0, "labeled_by": "bob"},
        ],
        is_blank=False,
    )
    dp.update_manual_review(ids["cam_a/v2"], [], is_blank=True, labeled_by="carol")
    dp.update_manual_review(
        ids["cam_b/v1"],
        [{"species": "deer", "tags": ["running"], "start_sec": 0.0, "labeled_by": "dave"}],
        is_blank=False,
    )
    dp.set_review_later(ids["cam_b/v1"], True)
    dp.create_custom_tag(name_en="Night Time")
    dp.set_video_tags(ids["cam_a/v1"], ["night_time"])


REVIEWS = [
    # v1: obs 1 changed (species + tags), obs 2 dropped (override) / kept (append),
    # plus a new observation without an id
    {
        "video_id_key": "cam_a/v1",
        "selections": [
            {
                "id": 1,
                "species": "fox",
                "tags": ["running"],
                "count": 2,
                "start_sec": 1.0,
                "end_sec": 5.0,
                "labeled_by": "alice",
            },
            {"species": "deer", "tags": [], "start_sec": 7.0, "labeled_by": "erin"},
        ],
        "is_blank": None,
        "labeled_by": None,
    },
    # v2: blank again (labeled_at must NOT move: blank state unchanged)
    {"video_id_key": "cam_a/v2", "selections": [], "is_blank": True, "labeled_by": "frank"},
    # cam_b/v1: declared blank while it has observations — append keeps them,
    # override deletes them; is_blank is forced False whenever observations remain
    {"video_id_key": "cam_b/v1", "selections": [], "is_blank": True, "labeled_by": "gina"},
    # cam_b/v2: first-ever label
    {
        "video_id_key": "cam_b/v2",
        "selections": [
            {"species": "deer", "tags": ["grazing"], "start_sec": 0.0, "labeled_by": "hugo"}
        ],
        "is_blank": None,
        "labeled_by": None,
    },
]
REVIEW_LATER = {"cam_b/v1": False, "cam_b/v3": True}
ASSIGNMENTS = {"cam_a/v1": "erin", "cam_b/v2": "hugo"}
VIDEO_TAGS = {"cam_a/v1": ["night_time"], "cam_b/v2": ["night_time", "unknown_key"]}


def _reference_apply(dp, ids, append):
    """The exact per-video call sequence the imports used before the bulk method."""
    for review in REVIEWS:
        dp.update_manual_review(
            ids[review["video_id_key"]],
            review["selections"],
            is_blank=review["is_blank"],
            labeled_by=review["labeled_by"],
            append=append,
        )
    for key, value in REVIEW_LATER.items():
        dp.set_review_later(ids[key], value)
    for key, annotator in ASSIGNMENTS.items():
        dp.add_annotator(annotator)
        with dp.engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT OR REPLACE INTO video_assignments"
                    " (video_id, assigned_to, assigned_at) VALUES (:vid, :a, :now)"
                ),
                {"vid": ids[key], "a": annotator, "now": dp._utcnow_dt().isoformat()},
            )
    for key, tag_keys in VIDEO_TAGS.items():
        dp.set_video_tags(ids[key], tag_keys, append=append)


def _bulk_apply(dp, ids, append):
    dp.apply_manual_reviews(
        [
            {
                "video_id": ids[r["video_id_key"]],
                "selections": r["selections"],
                "is_blank": r["is_blank"],
                "labeled_by": r["labeled_by"],
            }
            for r in REVIEWS
        ],
        append=append,
        review_later={ids[k]: v for k, v in REVIEW_LATER.items()},
        assignments={ids[k]: a for k, a in ASSIGNMENTS.items()},
        video_tags={ids[k]: t for k, t in VIDEO_TAGS.items()},
    )


@pytest.mark.parametrize("append", [False, True], ids=["override", "append"])
def test_bulk_apply_matches_per_video_sequence(
    tmp_db, mock_probe, monkeypatch, frozen_clock, append
):
    ref, ref_ids = _make_provider(tmp_db["root"], monkeypatch, f"ref_{append}")
    _seed(ref, ref_ids)
    _reference_apply(ref, ref_ids, append)

    bulk, bulk_ids = _make_provider(tmp_db["root"], monkeypatch, f"bulk_{append}")
    _seed(bulk, bulk_ids)
    _bulk_apply(bulk, bulk_ids, append)

    assert _dump(bulk) == _dump(ref)


def test_bulk_apply_empty_is_a_noop(tmp_db, mock_probe, monkeypatch, frozen_clock):
    dp, ids = _make_provider(tmp_db["root"], monkeypatch, "noop")
    _seed(dp, ids)
    before = _dump(dp)
    dp.apply_manual_reviews([])
    assert _dump(dp) == before
