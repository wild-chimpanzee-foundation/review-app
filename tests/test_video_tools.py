import json
import logging
from types import SimpleNamespace

import pytest
from review_app.backend import transcoding, video_tools


def test_check_video_tools_requires_ffmpeg_and_ffprobe(monkeypatch, caplog):
    paths = {"ffmpeg": "/bin/ffmpeg", "ffprobe": None}
    monkeypatch.setattr(video_tools, "find_video_tool", paths.get)
    monkeypatch.setattr(
        video_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    with caplog.at_level(logging.WARNING, logger=video_tools.__name__):
        status = video_tools.check_video_tools()

    assert status.available is False
    assert status.missing == ("ffprobe",)
    assert status.paths == paths
    assert "Required video tools unavailable: ffprobe" in caplog.text


def test_check_video_tools_rejects_executable_that_does_not_run(monkeypatch):
    monkeypatch.setattr(video_tools, "find_video_tool", lambda tool: f"/bin/{tool}")

    def run(command, **kwargs):
        return SimpleNamespace(returncode=1 if command[0].endswith("ffmpeg") else 0)

    monkeypatch.setattr(video_tools.subprocess, "run", run)

    status = video_tools.check_video_tools()

    assert status.available is False
    assert status.missing == ("ffmpeg",)
    assert status.paths["ffprobe"] == "/bin/ffprobe"


def test_check_video_tools_reports_complete_installation(monkeypatch, caplog):
    monkeypatch.setattr(video_tools, "find_video_tool", lambda tool: f"/bin/{tool}")
    monkeypatch.setattr(
        video_tools.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    with caplog.at_level(logging.INFO, logger=video_tools.__name__):
        status = video_tools.check_video_tools()

    assert status.available is True
    assert status.missing == ()
    assert "Video tools available: ffmpeg=/bin/ffmpeg, ffprobe=/bin/ffprobe" in caplog.text


def test_find_video_tool_rejects_unknown_tool():
    with pytest.raises(ValueError, match="Unsupported video tool"):
        video_tools.find_video_tool("convert")


def test_video_tool_env_restores_linux_library_path_in_frozen_build(monkeypatch):
    monkeypatch.setattr(video_tools.sys, "frozen", True, raising=False)
    monkeypatch.setattr(video_tools.sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/packaged/libraries")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/system/libraries")

    env = video_tools.video_tool_env()

    assert env["LD_LIBRARY_PATH"] == "/system/libraries"


def test_video_tool_env_removes_packaged_linux_library_path_without_original(monkeypatch):
    monkeypatch.setattr(video_tools.sys, "frozen", True, raising=False)
    monkeypatch.setattr(video_tools.sys, "platform", "linux")
    monkeypatch.setenv("LD_LIBRARY_PATH", "/packaged/libraries")
    monkeypatch.delenv("LD_LIBRARY_PATH_ORIG", raising=False)

    env = video_tools.video_tool_env()

    assert "LD_LIBRARY_PATH" not in env


def test_video_tool_env_adds_homebrew_paths_in_frozen_macos_build(monkeypatch, tmp_path):
    arm_bin = tmp_path / "arm-homebrew" / "bin"
    intel_bin = tmp_path / "intel-homebrew" / "bin"
    monkeypatch.setattr(video_tools, "_HOMEBREW_BIN_DIRS", (arm_bin, intel_bin))
    monkeypatch.setattr(video_tools.sys, "frozen", True, raising=False)
    monkeypatch.setattr(video_tools.sys, "platform", "darwin")
    monkeypatch.setenv("PATH", "/usr/bin")

    env = video_tools.video_tool_env()

    assert env["PATH"].split(":") == [str(intel_bin), str(arm_bin), "/usr/bin"]


def test_find_video_tool_uses_homebrew_fallback_on_macos(monkeypatch, tmp_path):
    homebrew_bin = tmp_path / "homebrew" / "bin"
    homebrew_bin.mkdir(parents=True)
    ffmpeg = homebrew_bin / "ffmpeg"
    ffmpeg.touch()
    monkeypatch.setattr(video_tools, "_HOMEBREW_BIN_DIRS", (homebrew_bin,))
    monkeypatch.setattr(video_tools.sys, "platform", "darwin")
    monkeypatch.setattr(video_tools.shutil, "which", lambda tool: None)

    assert video_tools.find_video_tool("ffmpeg") == str(ffmpeg)


@pytest.mark.parametrize("pix_fmt", ["yuv420p", "yuvj420p"])
def test_validate_transcoded_video_accepts_browser_safe_output(monkeypatch, tmp_path, pix_fmt):
    output = tmp_path / "video.v2.mp4"
    output.touch()
    probe_data = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": pix_fmt},
            {"codec_type": "audio", "codec_name": "aac", "profile": "LC"},
        ],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "10.0"},
    }
    monkeypatch.setattr(transcoding, "find_video_tool", lambda tool: "/bin/ffprobe")
    monkeypatch.setattr(
        transcoding.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(probe_data), stderr=""
        ),
    )

    assert transcoding.validate_transcoded_video(output).valid is True


@pytest.mark.parametrize("pix_fmt", ["yuv422p", "yuvj422p", "yuv420p10le"])
def test_validate_transcoded_video_rejects_incompatible_pixel_format(
    monkeypatch, tmp_path, pix_fmt
):
    output = tmp_path / "video.v2.mp4"
    output.touch()
    probe_data = {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "pix_fmt": pix_fmt},
        ],
        "format": {"format_name": "mov,mp4", "duration": "10.0"},
    }
    monkeypatch.setattr(transcoding, "find_video_tool", lambda tool: "/bin/ffprobe")
    monkeypatch.setattr(
        transcoding.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(probe_data), stderr=""
        ),
    )

    validation = transcoding.validate_transcoded_video(output)

    assert validation.valid is False
    assert validation.error == "output video must be 8-bit H.264 4:2:0"


def test_inspect_transcoded_cache_reports_missing_and_outdated_entries(tmp_path):
    missing = transcoding.inspect_transcoded_cache(tmp_path / "missing.v2.mp4")
    assert missing.exists is False
    assert missing.usable is False

    outdated = tmp_path / "video.mp4"
    outdated.touch()
    status = transcoding.inspect_transcoded_cache(outdated)
    assert status.exists is True
    assert status.current is False
    assert status.usable is False


def test_inspect_transcoded_cache_can_skip_content_validation(tmp_path, monkeypatch):
    current = tmp_path / "video.v2.mp4"
    current.touch()
    monkeypatch.setattr(
        transcoding,
        "validate_transcoded_video",
        lambda path: pytest.fail("content validation should be skipped"),
    )

    status = transcoding.inspect_transcoded_cache(current, validate_contents=False)

    assert status.exists is True
    assert status.current is True
    assert status.validation is None
