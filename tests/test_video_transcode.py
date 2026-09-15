from pathlib import Path
from types import SimpleNamespace

from review_app.backend.transcoding import transcoded_cache_filename
from sqlalchemy import text


def test_transcode_forces_browser_compatible_pixel_format(
    provider_with_project, monkeypatch, tmp_path
):
    dp, project, _ = provider_with_project
    with dp.engine.connect() as connection:
        video_id = connection.execute(
            text("SELECT video_id FROM videos WHERE project_id = :project_id LIMIT 1"),
            {"project_id": project.id},
        ).scalar_one()

    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).touch()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(
        "review_app.backend.provider.video.find_video_tool", lambda tool: "/fake/ffmpeg"
    )
    monkeypatch.setattr("review_app.backend.provider.video.subprocess.run", fake_run)
    monkeypatch.setattr("review_app.app.config.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "review_app.backend.provider.video.validate_transcoded_video",
        lambda path: SimpleNamespace(valid=True, error=None),
    )

    result = dp.transcode_video(video_id)

    assert result.success is True
    assert ["-pix_fmt", "yuv420p"] == commands[0][
        commands[0].index("-pix_fmt") : commands[0].index("-pix_fmt") + 2
    ]
    assert commands[0][commands[0].index("-vf") + 1].startswith("pad=ceil(iw/2)*2")
    assert ["-profile:a", "aac_low", "-ac", "2", "-ar", "48000"] == commands[0][
        commands[0].index("-profile:a") : commands[0].index("-profile:a") + 6
    ]
    assert result.new_path is not None
    assert result.new_path.endswith(".v2.mp4")
    assert Path(result.new_path).exists()
    assert not list((tmp_path / "transcoded_cache").glob("*.part.mp4"))


def test_transcode_discards_output_that_fails_validation(
    provider_with_project, monkeypatch, tmp_path
):
    dp, project, _ = provider_with_project
    with dp.engine.connect() as connection:
        video_id = connection.execute(
            text("SELECT video_id FROM videos WHERE project_id = :project_id LIMIT 1"),
            {"project_id": project.id},
        ).scalar_one()

    def fake_run(command, **kwargs):
        Path(command[-1]).touch()
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr(
        "review_app.backend.provider.video.find_video_tool", lambda tool: "/fake/ffmpeg"
    )
    monkeypatch.setattr("review_app.backend.provider.video.subprocess.run", fake_run)
    monkeypatch.setattr("review_app.app.config.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "review_app.backend.provider.video.validate_transcoded_video",
        lambda path: SimpleNamespace(valid=False, error="incompatible output"),
    )

    result = dp.transcode_video(video_id)

    assert result.success is False
    assert result.error == "incompatible output"
    assert result.new_path is None
    assert not list((tmp_path / "transcoded_cache").iterdir())


def test_transcode_preserves_existing_cache_when_tools_are_unavailable(
    provider_with_project, monkeypatch, tmp_path
):
    dp, project, _ = provider_with_project
    with dp.engine.connect() as connection:
        video_id = connection.execute(
            text("SELECT video_id FROM videos WHERE project_id = :project_id LIMIT 1"),
            {"project_id": project.id},
        ).scalar_one()

    cache_dir = tmp_path / "transcoded_cache"
    cache_dir.mkdir()
    cached_video = cache_dir / transcoded_cache_filename(video_id)
    cached_video.write_bytes(b"existing cached video")

    monkeypatch.setattr("review_app.app.config.get_user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "review_app.backend.provider.video.inspect_transcoded_cache",
        lambda path: SimpleNamespace(
            exists=True,
            usable=False,
            validation=SimpleNamespace(error="ffprobe executable not found"),
        ),
    )
    monkeypatch.setattr("review_app.backend.provider.video.find_video_tool", lambda tool: None)

    result = dp.transcode_video(video_id)

    assert result.success is False
    assert result.error == "ffmpeg executable not found"
    assert cached_video.read_bytes() == b"existing cached video"
