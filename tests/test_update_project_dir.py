from pathlib import Path

from review_app.backend.db.models import Video


def _paths(dp, pid):
    with dp.Session() as s:
        return sorted(v.video_path for v in s.query(Video).filter_by(project_id=pid).all())


def test_update_project_dir_rewrites_video_paths(provider_with_project):
    dp, project, d = provider_with_project
    old = d.path
    before = _paths(dp, project.id)
    assert all(p.startswith(old) for p in before)

    new_dir = str(Path(old).parent / "moved_videos")
    updated = dp.update_project_dir(d.id, new_dir)
    assert updated is not None
    assert updated.path == new_dir

    after = _paths(dp, project.id)
    assert all(p.startswith(new_dir + "/") for p in after)
    # filenames/subpaths preserved
    assert [Path(p).name for p in before] == [Path(p).name for p in after]
    # dir record updated
    assert dp.get_project_dirs(project.id)[0].path == new_dir


def test_update_project_dir_unknown_id_returns_none(provider_with_project):
    dp, project, _ = provider_with_project
    assert dp.update_project_dir("nope", "/tmp/x") is None
