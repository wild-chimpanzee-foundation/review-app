import hashlib
import mimetypes
import shutil
import subprocess
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components


def render_video_sidebar_settings() -> None:
    if "video_autoplay" not in st.session_state:
        st.session_state.video_autoplay = True
    if "video_muted" not in st.session_state:
        st.session_state.video_muted = True
    if "video_playback_speed" not in st.session_state:
        st.session_state.video_playback_speed = 1.0

    with st.sidebar.expander("Video Playback", expanded=True):
        st.session_state.video_autoplay = st.checkbox(
            "Autoplay",
            value=st.session_state.video_autoplay,
            key="video_autoplay_toggle",
        )
        st.session_state.video_muted = st.checkbox(
            "Mute",
            value=st.session_state.video_muted,
            key="video_muted_toggle",
        )
        st.session_state.video_playback_speed = st.select_slider(
            "Playback Speed",
            options=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
            value=float(st.session_state.video_playback_speed),
            key="video_playback_speed_toggle",
        )


def _infer_video_mime(video_path: str) -> str:
    mime, _ = mimetypes.guess_type(video_path)
    if mime and mime.startswith("video/"):
        return mime

    suffix = Path(video_path).suffix.lower()
    fallback_by_suffix = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".wmv": "video/x-ms-wmv",
        ".flv": "video/x-flv",
        ".mpeg": "video/mpeg",
        ".mpg": "video/mpeg",
    }
    return fallback_by_suffix.get(suffix, "video/mp4")


def _warn_once(key: str, message: str) -> None:
    if "video_player_warnings" not in st.session_state:
        st.session_state.video_player_warnings = set()
    if key in st.session_state.video_player_warnings:
        return
    st.session_state.video_player_warnings.add(key)
    st.warning(message)


def _preview_cache_path(video_path: str) -> Path:
    resolved = Path(video_path).resolve()
    stat = resolved.stat()
    fingerprint = f"{resolved}:{stat.st_size}:{stat.st_mtime_ns}"
    preview_name = f"{hashlib.sha1(fingerprint.encode('utf-8')).hexdigest()}.mp4"
    cache_root = Path("/tmp/video_pipeline_preview_cache")
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root / preview_name


def _get_browser_playable_video(video_path: str) -> str:
    src = Path(video_path)
    if not src.exists():
        _warn_once(
            f"missing::{video_path}",
            f"Video file does not exist on server: `{video_path}`",
        )
        return video_path

    if src.suffix.lower() in {".mp4", ".webm", ".ogv", ".ogg"}:
        return video_path

    ffmpeg_bin = shutil.which("ffmpeg")
    if ffmpeg_bin is None:
        _warn_once(
            f"ffmpeg_missing::{video_path}",
            "ffmpeg not found on server. Cannot create browser-compatible preview for this video.",
        )
        return video_path

    preview_path = _preview_cache_path(video_path)
    if preview_path.exists() and preview_path.stat().st_size > 0:
        return str(preview_path)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-nostdin",
        "-loglevel",
        "error",
        "-i",
        str(src),
        "-an",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-preset",
        "veryfast",
        "-crf",
        "28",
        "-movflags",
        "+faststart",
        str(preview_path),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    except Exception as exc:
        _warn_once(
            f"preview_exception::{video_path}",
            f"Preview conversion failed for `{src.name}`: {exc}",
        )
        return video_path

    if result.returncode != 0 or not preview_path.exists():
        stderr_msg = (result.stderr or "").strip() or "unknown ffmpeg error"
        _warn_once(
            f"preview_failed::{video_path}",
            f"Preview conversion failed for `{src.name}`: {stderr_msg}",
        )
        return video_path

    return str(preview_path)


def render_video_player(
    video_path: str,
    autoplay: bool | None = None,
    muted: bool | None = None,
) -> None:
    if autoplay is None:
        autoplay = bool(st.session_state.get("video_autoplay", True))
    if muted is None:
        muted = bool(st.session_state.get("video_muted", False))

    playable_path = _get_browser_playable_video(video_path)
    st.video(
        playable_path,
        format=_infer_video_mime(playable_path),
        autoplay=autoplay,
        muted=muted,
    )


def apply_video_playback_rate(rate: float) -> None:
    safe_rate = float(rate)
    components.html(
        f"""
        <script>
        (function() {{
          function getHostDocument() {{
            try {{
              if (window.parent && window.parent.document) return window.parent.document;
            }} catch (e) {{}}
            return document;
          }}

          const hostDocument = getHostDocument();
          const desiredRate = {safe_rate};
          let tries = 0;
          const maxTries = 240;

          function ensurePersistentProgress(video) {{
            if (!video || !video.parentElement) return;

            let wrapper = hostDocument.getElementById("vp-persistent-progress-wrap");
            if (!wrapper) {{
              wrapper = hostDocument.createElement("div");
              wrapper.id = "vp-persistent-progress-wrap";
              wrapper.style.marginTop = "6px";
              wrapper.style.width = "100%";
              wrapper.style.display = "flex";
              wrapper.style.flexDirection = "column";
              wrapper.style.gap = "4px";

              const track = hostDocument.createElement("div");
              track.id = "vp-persistent-progress-track";
              track.style.height = "8px";
              track.style.width = "100%";
              track.style.background = "rgba(148,163,184,0.35)";
              track.style.borderRadius = "999px";
              track.style.overflow = "hidden";

              const fill = hostDocument.createElement("div");
              fill.id = "vp-persistent-progress-fill";
              fill.style.height = "100%";
              fill.style.width = "0%";
              fill.style.background = "#2563EB";
              fill.style.transition = "width 120ms linear";

              const label = hostDocument.createElement("div");
              label.id = "vp-persistent-progress-label";
              label.style.fontSize = "12px";
              label.style.color = "rgba(51,65,85,0.9)";
              label.textContent = "00:00 / 00:00";

              track.appendChild(fill);
              wrapper.appendChild(track);
              wrapper.appendChild(label);
            }}

            if (wrapper.parentElement !== video.parentElement) {{
              video.parentElement.appendChild(wrapper);
            }}

            function fmt(seconds) {{
              if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
              const total = Math.floor(seconds);
              const m = Math.floor(total / 60);
              const s = total % 60;
              return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
            }}

            function updateProgress() {{
              const fill = hostDocument.getElementById("vp-persistent-progress-fill");
              const label = hostDocument.getElementById("vp-persistent-progress-label");
              if (!fill || !label) return;

              const duration = video.duration;
              const current = video.currentTime || 0;
              const pct = duration && Number.isFinite(duration) && duration > 0
                ? (current / duration) * 100
                : 0;
              fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
              label.textContent = fmt(current) + " / " + fmt(duration);
            }}

            video.removeEventListener("timeupdate", video.__vpProgressUpdater || (() => {{}}));
            video.__vpProgressUpdater = updateProgress;
            video.addEventListener("timeupdate", updateProgress);
            video.addEventListener("loadedmetadata", updateProgress);
            updateProgress();
          }}

          function ensurePersistentRate(video) {{
            if (!video) return;

            const applyRate = () => {{
              if (Math.abs((video.playbackRate || 0) - desiredRate) > 0.001) {{
                video.playbackRate = desiredRate;
              }}
            }};

            if (video.__vpRateHandlers) {{
              video.removeEventListener("loadedmetadata", video.__vpRateHandlers.loadedmetadata);
              video.removeEventListener("canplay", video.__vpRateHandlers.canplay);
              video.removeEventListener("play", video.__vpRateHandlers.play);
            }}

            video.__vpRateHandlers = {{
              loadedmetadata: applyRate,
              canplay: applyRate,
              play: applyRate,
            }};

            video.addEventListener("loadedmetadata", video.__vpRateHandlers.loadedmetadata);
            video.addEventListener("canplay", video.__vpRateHandlers.canplay);
            video.addEventListener("play", video.__vpRateHandlers.play);
            applyRate();
          }}

          const timer = setInterval(function() {{ tries += 1; const video = hostDocument.querySelector("video"); if (video) {{
              video.controls = true;
              ensurePersistentRate(video);
              ensurePersistentProgress(video);
            }}
            if (tries >= maxTries) {{
              clearInterval(timer);
            }}
          }}, 100);
        }})();
        </script>
        """,
        height=0,
    )
