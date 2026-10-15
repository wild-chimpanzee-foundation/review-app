from __future__ import annotations

import json
from pathlib import Path

from nicegui import ui

from review_app.backend.distance_timing import SAMPLE_STOP_TOLERANCE_MS


def video_element(player) -> str:
    return f"getElement({player.id})?.$el"


def frame_tolerance_ms(frame_rate: float | None) -> float:
    """Use a half-frame match, or a conservative tolerance when FPS is unknown."""
    return 500 / frame_rate if frame_rate and frame_rate > 0 else 10


async def capture_offset(player, *, exact: bool = False) -> int | float:
    """Pause and read the actual displayed frame, rather than a delayed timeupdate event."""
    result = await ui.run_javascript(
        f"""const video = {video_element(player)};
        if (!video || video.seeking || video.readyState < 2 || !Number.isFinite(video.duration)) return null;
        if (!video.paused) video._distanceSuppressedPauseEvents =
            (video._distanceSuppressedPauseEvents || 0) + 1;
        video.pause();
        return {"video.currentTime * 1000" if exact else "Math.round(video.currentTime * 1000)"};""",
        timeout=5,
    )
    if result is None:
        raise ValueError("distance_player_not_ready")
    return float(result) if exact else int(result)


def seek_paused(player, offset_ms: int | float, *, retain_after_ms: int = 0) -> None:
    ui.run_javascript(f"""
        const video = {video_element(player)};
        if (video) {{
            const seek = () => {{
                video._distancePlayback = (video._distancePlayback || 0) + 1;
                if (!video.paused) video._distanceSuppressedPauseEvents =
                    (video._distanceSuppressedPauseEvents || 0) + 1;
                video.pause();
                const target = {offset_ms / 1000};
                if (!video.seeking && video.currentTime >= target &&
                    video.currentTime <= target + {retain_after_ms / 1000}) return;
                video.currentTime = target;
            }};
            if (video.readyState >= 1) seek();
            else video.addEventListener('loadedmetadata', seek, {{once: true}});
        }}
    """)


async def step_frames(player, delta: int, frame_rate: float) -> None:
    """Pause and move by a whole number of frames, bounded by the video's ends."""
    ready = await ui.run_javascript(
        f"""const video = {video_element(player)};
        if (!video || video.readyState < 2 || !Number.isFinite(video.duration)) return false;
        video._distancePlayback = (video._distancePlayback || 0) + 1;
        if (!video.paused) video._distanceSuppressedPauseEvents =
            (video._distanceSuppressedPauseEvents || 0) + 1;
        video.pause();
        const rate = {frame_rate};
        const current = Math.floor(video.currentTime * rate + 0.0001);
        const last = Math.max(0, Math.ceil(video.duration * rate) - 1);
        const target = Math.max(0, Math.min(last, current + {delta}));
        video.currentTime = Math.min(video.duration, target / rate + 0.000001);
        return true;""",
        timeout=5,
    )
    if not ready:
        raise ValueError("distance_player_not_ready")


async def switch_source(player, source: str, offset_ms: int | float) -> bool:
    """Keep the displayed frame until the replacement source has loaded and sought."""
    if player.source == source:
        seek_paused(player, offset_ms)
        return True
    script = Path(__file__).with_name("source_switch.js").read_text()
    prepared = await ui.run_javascript(
        script.replace("__VIDEO__", video_element(player))
        .replace("__SOURCE__", json.dumps(source))
        .replace("__OFFSET__", str(offset_ms / 1000)),
        timeout=5,
    )
    if not prepared:
        return False
    player.set_source(source)
    return bool(
        await ui.run_javascript(
            f"return await {video_element(player)}?._distanceSourceSwitch?.promise;",
            timeout=20,
        )
    )


async def play_to_sample(player, from_ms: int | float, to_ms: int | float) -> None:
    """Play to the intended scheduled frame, correcting callback delay if needed."""
    script = Path(__file__).with_name("sample_playback.js").read_text()
    completed = await ui.run_javascript(
        script.replace("__VIDEO__", video_element(player))
        .replace("__FROM__", str(from_ms / 1000))
        .replace("__TO__", str(to_ms / 1000))
        .replace("__MAX_DELAY__", str(SAMPLE_STOP_TOLERANCE_MS / 1000))
        .replace("__WATCHDOG__", str(max(10_000, (to_ms - from_ms) * 5 + 5000))),
        timeout=max(15, (to_ms - from_ms) / 1000 * 5 + 10),
    )
    if not completed:
        raise ValueError("distance_playback_interrupted")


def install_manual_pause_snap(player, handler) -> None:
    """Report only user-initiated pauses from this annotation player."""
    event_name = f"distance_manual_pause_{player.id}"
    ui.on(event_name, handler)
    ui.run_javascript(f"""
        (function install() {{
            const video = {video_element(player)};
            if (!video || video._distancePauseSnapInstalled) return;
            video._distancePauseSnapInstalled = true;
            video.addEventListener('pause', () => {{
                if (!video.isConnected) return;
                if (video._distanceSuppressedPauseEvents > 0) {{
                    video._distanceSuppressedPauseEvents--;
                    return;
                }}
                if (!video.seeking)
                    emitEvent({json.dumps(event_name)}, {{offset_ms: video.currentTime * 1000}});
            }});
            // Global dialog events can remount this video. Follow the same
            // replacement lifecycle as the shared controls and anchor editor.
            const lifecycle = new MutationObserver(() => {{
                if (video.isConnected) return;
                lifecycle.disconnect();
                setTimeout(() => {{ if (getElement({player.id})) install(); }}, 50);
            }});
            lifecycle.observe(document.body, {{childList: true, subtree: true}});
        }})();
    """)


def pause_programmatically(player) -> None:
    ui.run_javascript(f"""
        const video = {video_element(player)};
        if (video) {{
            if (!video.paused) video._distanceSuppressedPauseEvents =
                (video._distanceSuppressedPauseEvents || 0) + 1;
            video.pause();
        }}
    """)


def stop_player(player) -> None:
    if player is not None:
        ui.run_javascript(f"""
            const video = {video_element(player)};
            if (video) {{
                if (!video.paused) video._distanceSuppressedPauseEvents =
                    (video._distanceSuppressedPauseEvents || 0) + 1;
                video.pause(); video.removeAttribute('src'); video.load();
            }}
        """)


def playback_shortcuts(container, player) -> None:
    """Use Review's playback keys within this player's focus and dialog scope."""
    container.props('tabindex="0"')
    container.on(
        "keydown",
        js_handler="""(event) => {
            if (event.ctrlKey || event.metaKey || event.altKey ||
                event.target.closest('input, textarea, select, button, [contenteditable=true], [role=combobox]')) return;
            const modal = document.querySelector('.q-dialog[aria-modal="true"]');
            if (modal && !modal.contains(event.target)) return;
            const video = __VIDEO__;
            if (!video) return;
            const wrapper = video.closest('[id^="vp-wrap-"]');
            if (!wrapper) return;
            const control = prefix => wrapper.querySelector(`[id^="${prefix}"]`);
            const key = event.key.toLowerCase();
            const adjust = (prefix, delta) => {
                const slider = control(prefix);
                if (!slider) return;
                slider.value = Math.max(Number(slider.min),
                    Math.min(Number(slider.max), Number(slider.value) + delta));
                slider.dispatchEvent(new Event('input', {bubbles:true}));
            };
            if (event.key === ' ') {
                event.preventDefault();
                video.paused ? video.play() : video.pause();
            } else if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
                event.preventDefault();
                video._distancePlayback = (video._distancePlayback || 0) + 1;
                video.currentTime = Math.max(0, Math.min(video.duration || 0,
                    video.currentTime + (event.key === 'ArrowRight' ? 5 : -5)));
            } else if (key === 's' || key === 'd') {
                event.preventDefault();
                const select = control('vp-speed-');
                if (!select) return;
                const options = Array.from(select.options);
                const closest = options.reduce((best, option, index) =>
                    Math.abs(Number(option.value) - video.playbackRate) <
                    Math.abs(Number(options[best].value) - video.playbackRate) ? index : best, 0);
                const next = Math.max(0, Math.min(options.length - 1,
                    closest + (key === 'd' ? 1 : -1)));
                select.value = options[next].value;
                select.dispatchEvent(new Event('change', {bubbles:true}));
            } else if (event.key === '[' || event.key === ']') {
                event.preventDefault();
                adjust('vp-brightness-', event.key === ']' ? 0.05 : -0.05);
            } else if (event.key === '{' || event.key === '}') {
                event.preventDefault();
                adjust('vp-contrast-', event.key === '}' ? 0.05 : -0.05);
            } else if (key === 'z' || key === 'r' || key === 'f') {
                event.preventDefault();
                control({z:'vp-reset-zoom-', r:'vp-reset-filters-', f:'vp-fs-'}[key])?.click();
            } else if (event.key === 'Escape' && wrapper.classList.contains('vp-fs-active')) {
                event.preventDefault();
                event.stopPropagation();
                control('vp-fs-')?.click();
            }
        }""".replace("__VIDEO__", video_element(player)),
    )


def install_anchor_editor(
    player,
    on_anchor,
    individual: dict | None = None,
    *,
    individuals=None,
    frame_rate: float | None = None,
    initial_mode: str | None = None,
) -> None:
    if initial_mode not in ("box", None):
        raise ValueError("Anchor editor only supports bounding boxes")
    event_name = f"distance_anchor_{player.id}"
    ui.on(event_name, on_anchor)
    source = Path(__file__).with_name("anchor_editor.js").read_text()
    ui.run_javascript(
        source.replace("__PLAYER_ID__", str(player.id))
        .replace("__EVENT__", event_name)
        .replace(
            "__INITIAL__",
            json.dumps(
                {
                    "individual": individual,
                    "individuals": individuals or ([individual] if individual else []),
                },
                default=str,
            ),
        )
        .replace("__MODE__", json.dumps(initial_mode))
        .replace("__FRAME_RATE__", json.dumps(frame_rate))
    )


def configure_anchor(player, individual: dict | None, *, individuals=None) -> None:
    state = {
        "individual": individual,
        "individuals": individuals or ([individual] if individual else []),
    }
    ui.run_javascript(
        f"""const component = getElement({player.id});
        if (component) {{
            const state = {json.dumps(state, default=str)};
            (window.__distanceIndividuals ||= new Map()).set({player.id}, state);
            component.$el?._distanceAnchor?.configure(state);
        }}"""
    )


async def image_filters(player) -> dict[str, float]:
    """Read the current player brightness and contrast for an independent dialog player."""
    result = await ui.run_javascript(
        f"""const video = {video_element(player)};
        const wrapper = video?.closest('[id^="vp-wrap-"]');
        return {{
            brightness: Number(wrapper?.querySelector('[id^="vp-brightness-"]')?.value || 1),
            contrast: Number(wrapper?.querySelector('[id^="vp-contrast-"]')?.value || 1),
        }};""",
        timeout=5,
    )
    return result or {"brightness": 1, "contrast": 1}


def anchor_mode(player, mode: str | None) -> None:
    if mode not in ("box", None):
        raise ValueError("Anchor editor only supports bounding boxes")
    ui.run_javascript(f"{video_element(player)}?._distanceAnchor?.setMode({json.dumps(mode)});")
