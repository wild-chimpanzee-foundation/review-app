from nicegui import ui

from review_app.app.state import (
    get_playback_speed,
    is_autoplay,
    is_muted,
    set_muted,
    set_playback_speed,
)
from review_app.app.translations import t

SPEED_OPTIONS = [
    0.25,
    0.5,
    0.75,
    1.0,
    1.25,
    1.5,
    2.0,
    3.0,
    4.0,
    5.0,
    6.0,
    7.0,
    8.0,
    9.0,
    10.0,
    15.0,
    20.0,
    25.0,
]


def render_custom_video_player(video_url, duration, vid_key):
    """Render a video component with mouse-wheel zoom, drag-to-pan, and custom controls."""

    autoplay = is_autoplay()
    muted = is_muted()

    with (
        ui.element("div")
        .props(f'id="vp-wrap-{vid_key}"')
        .classes("full-width")
        .style("display: flex; flex-direction: column")
    ):
        with (
            ui.element("div")
            .classes("vp-video-container relative-position overflow-hidden w-full")
            .style(f"id: vp-container-{vid_key}; border: 1px solid #333; line-height: 0;")
        ):
            v = ui.video(video_url, autoplay=autoplay, muted=muted, controls=False).classes(
                "w-full"
            ).props('preload="auto"')

        def _fmt(s):
            try:
                m, sec = divmod(int(float(s)), 60)
                return f"{m:02d}:{sec:02d}"
            except (ValueError, TypeError):
                return "00:00"

        speed_options_html = "".join(
            f'<option value="{s}" {"selected" if s == float(get_playback_speed().replace("x", "")) else ""}>{s}x</option>'
            for s in SPEED_OPTIONS
        )

        ui.html(f'''
            <style>
                #vp-range-{vid_key} {{ -webkit-appearance: none; background: transparent; }}
                #vp-range-{vid_key}::-webkit-slider-runnable-track {{
                    height: 36px; border-radius: 3px;
                    background: linear-gradient(to right, var(--q-primary) var(--pct, 0%), #555 var(--pct, 0%));
                }}
                #vp-range-{vid_key}::-webkit-slider-thumb {{
                    -webkit-appearance: none; width: 36px; height: 16px; border-radius: 50%;
                    background: var(--q-primary); margin-top: -5px; cursor: pointer;
                }}
            </style>
            <div style="display:flex;align-items:center;gap:8px;padding:4px 8px 0;width:100%;flex-wrap:wrap;">
                <button id="vp-playpause-{vid_key}" style="flex-shrink:0;width:32px;height:32px;border:none;background:none;cursor:pointer;color:var(--q-primary);">
                    <svg id="vp-play-icon-{vid_key}" viewBox="0 0 24 24" width="32" height="32" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                    <svg id="vp-pause-icon-{vid_key}" viewBox="0 0 24 24" width="32" height="32" fill="currentColor" style="display:none"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
                </button>
                <input type="range" id="vp-range-{vid_key}" min="0" max="{duration}" step="0.1" value="0" style="flex:1;min-width:80px;cursor:pointer;height:32px;">
                <div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">
                    <span id="vp-time-{vid_key}" style="font-size:16px;color:#888;font-family:monospace">00:00 / {_fmt(duration)}</span>
                    <select id="vp-speed-{vid_key}" style="font-size:15px;font-weight:600;color:var(--q-primary);background:none;border:1px solid var(--q-primary);border-radius:4px;cursor:pointer;padding:2px 4px;">
                        {speed_options_html}
                    </select>
                    <button id="vp-mute-{vid_key}" title="Toggle mute" style="flex-shrink:0;width:32px;height:32px;border:none;background:none;cursor:pointer;color:var(--q-primary);display:flex;align-items:center;justify-content:center;">
                        <svg id="vp-unmuted-icon-{vid_key}" viewBox="0 0 24 24" width="28" height="28" fill="currentColor" style="{"display:none" if muted else ""}"><path d="M3 9v6h4l5 5V4L7 9H3zm13.5 3c0-1.77-1.02-3.29-2.5-4.03v8.05c1.48-.73 2.5-2.25 2.5-4.02zM14 3.23v2.06c2.89.86 5 3.54 5 6.71s-2.11 5.85-5 6.71v2.06c4.01-.91 7-4.49 7-8.77s-2.99-7.86-7-8.77z"/></svg>
                        <svg id="vp-muted-icon-{vid_key}" viewBox="0 0 24 24" width="28" height="28" fill="currentColor" style="{"display:none" if not muted else ""}"><path d="M16.5 12c0-1.77-1.02-3.29-2.5-4.03v2.21l2.45 2.45c.03-.2.05-.41.05-.63zm2.5 0c0 .94-.2 1.82-.54 2.64l1.51 1.51C20.63 14.91 21 13.5 21 12c0-4.28-2.99-7.86-7-8.77v2.06c2.89.86 5 3.54 5 6.71zM4.27 3L3 4.27 7.73 9H3v6h4l5 5v-6.73l4.25 4.25c-.67.52-1.42.93-2.25 1.18v2.06c1.38-.31 2.63-.95 3.69-1.81L19.73 21 21 19.73l-9-9L4.27 3zM12 4L9.91 6.09 12 8.18V4z"/></svg>
                    </button>
                    <button id="vp-fs-{vid_key}" title="Toggle fullscreen" style="flex-shrink:0;width:32px;height:32px;border:none;background:none;cursor:pointer;color:var(--q-primary);display:flex;align-items:center;justify-content:center;">
                        <svg id="vp-fs-enter-{vid_key}" viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M7 14H5v5h5v-2H7v-3zm-2-4h2V7h3V5H5v5zm12 7h-3v2h5v-5h-2v3zM14 5v2h3v3h2V5h-5z"/></svg>
                        <svg id="vp-fs-exit-{vid_key}" viewBox="0 0 24 24" width="28" height="28" fill="currentColor" style="display:none"><path d="M5 16h3v3h2v-5H5v2zm3-8H5v2h5V5H8v3zm6 11h2v-3h3v-2h-5v5zm2-11V5h-2v5h5V8h-3z"/></svg>
                    </button>
                </div>
            </div>
            <div style="display:flex;align-items:center;gap:8px;padding:4px 8px;width:100%;flex-wrap:wrap;">
                <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:220px;">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="#888" style="flex-shrink:0" title="Brightness"><path d="M12 7c-2.76 0-5 2.24-5 5s2.24 5 5 5 5-2.24 5-5-2.24-5-5-5zM2 13h2c.55 0 1-.45 1-1s-.45-1-1-1H2c-.55 0-1 .45-1 1s.45 1 1 1zm18 0h2c.55 0 1-.45 1-1s-.45-1-1-1h-2c-.55 0-1 .45-1 1s.45 1 1 1zM11 2v2c0 .55.45 1 1 1s1-.45 1-1V2c0-.55-.45-1-1-1s-1 .45-1 1zm0 18v2c0 .55.45 1 1 1s1-.45 1-1v-2c0-.55-.45-1-1-1s-1 .45-1 1zM5.99 4.58c-.39-.39-1.03-.39-1.41 0-.39.39-.39 1.03 0 1.41l1.06 1.06c.39.39 1.03.39 1.41 0s.39-1.03 0-1.41L5.99 4.58zm12.37 12.37c-.39-.39-1.03-.39-1.41 0-.39.39-.39 1.03 0 1.41l1.06 1.06c.39.39 1.03.39 1.41 0 .39-.39.39-1.03 0-1.41l-1.06-1.06zm1.06-10.96c.39-.39.39-1.03 0-1.41-.39-.39-1.03-.39-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06zM7.05 18.36c.39-.39.39-1.03 0-1.41-.39-.39-1.03-.39-1.41 0l-1.06 1.06c-.39.39-.39 1.03 0 1.41s1.03.39 1.41 0l1.06-1.06z"/></svg>
                    <input type="range" id="vp-brightness-{vid_key}" min="0.5" max="2" step="0.01" value="1" style="flex:1;cursor:pointer;height:28px;">
                    <span id="vp-brightness-val-{vid_key}" style="font-size:14px;color:#888;width:30px;text-align:right;flex-shrink:0;">1.00</span>
                </div>
                <div style="display:flex;align-items:center;gap:6px;flex:1;min-width:220px;">
                    <svg viewBox="0 0 24 24" width="20" height="20" fill="#888" style="flex-shrink:0" title="Contrast"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm0 18V4c4.41 0 8 3.59 8 8s-3.59 8-8 8z"/></svg>
                    <input type="range" id="vp-contrast-{vid_key}" min="0.5" max="2" step="0.01" value="1" style="flex:1;cursor:pointer;height:28px;">
                    <span id="vp-contrast-val-{vid_key}" style="font-size:14px;color:#888;width:30px;text-align:right;flex-shrink:0;">1.00</span>
                </div>
                <div style="display:flex;align-items:center;gap:4px;flex-shrink:0;">
                    <button id="vp-reset-filters-{vid_key}" style="font-size:13px;color:#888;background:none;border:1px solid #444;border-radius:3px;padding:4px 8px;cursor:pointer;white-space:nowrap;">{t("reset_filters")}</button>
                    <button id="vp-reset-zoom-{vid_key}" style="font-size:13px;color:#888;background:none;border:1px solid #444;border-radius:3px;padding:4px 8px;cursor:pointer;white-space:nowrap;">{t("reset_zoom")}</button>
                </div>
            </div>
        ''').classes("full-width")

        def _persist_speed(e):
            try:
                rate = float(e.args)
            except (TypeError, ValueError):
                return
            set_playback_speed(f"{round(rate, 2)}x")

        ui.on(f"vp_speed_change_{vid_key}", _persist_speed)

        def _persist_mute(e):
            set_muted(bool(e.args))

        ui.on(f"vp_mute_change_{vid_key}", _persist_mute)

        ui.run_javascript(f"""
            (function setup() {{
                const comp = getElement({v.id});
                if (!comp || !comp.$el) {{ setTimeout(setup, 50); return; }}
                const videoEl = comp.$el.tagName === 'VIDEO' ? comp.$el : comp.$el.querySelector('video');
                const range = document.getElementById('vp-range-{vid_key}');
                const speedSel = document.getElementById('vp-speed-{vid_key}');
                const brightnessSlider = document.getElementById('vp-brightness-{vid_key}');
                const contrastSlider = document.getElementById('vp-contrast-{vid_key}');
                const brightnessVal = document.getElementById('vp-brightness-val-{vid_key}');
                const contrastVal = document.getElementById('vp-contrast-val-{vid_key}');
                const resetBtn = document.getElementById('vp-reset-filters-{vid_key}');
                const resetZoomBtn = document.getElementById('vp-reset-zoom-{vid_key}');
                const fsBtn = document.getElementById('vp-fs-{vid_key}');
                const fsEnterIcon = document.getElementById('vp-fs-enter-{vid_key}');
                const fsExitIcon = document.getElementById('vp-fs-exit-{vid_key}');
                const vpWrapper = document.getElementById('vp-wrap-{vid_key}');
                const muteBtn = document.getElementById('vp-mute-{vid_key}');
                const mutedIcon = document.getElementById('vp-muted-icon-{vid_key}');
                const unmutedIcon = document.getElementById('vp-unmuted-icon-{vid_key}');

                let state = {{
                    scale: 1,
                    x: 0,
                    y: 0,
                    isDragging: false,
                    startX: 0,
                    startY: 0
                }};

                videoEl.playbackRate = parseFloat(speedSel.value);
                // Sync select to what browser actually accepted (browsers may cap max rate)
                setTimeout(() => {{
                    const actual = videoEl.playbackRate;
                    const desired = parseFloat(speedSel.value);
                    if (Math.abs(actual - desired) > 0.05) {{
                        // Find closest available option
                        let best = null;
                        for (const opt of speedSel.options) {{
                            if (best === null || Math.abs(parseFloat(opt.value) - actual) < Math.abs(parseFloat(best.value) - actual)) best = opt;
                        }}
                        if (best) speedSel.value = best.value;
                        emitEvent('vp_speed_change_{vid_key}', actual);
                    }}
                }}, 100);

                function updateTransform() {{
                    const brightness = brightnessSlider ? brightnessSlider.value : 1;
                    const contrast = contrastSlider ? contrastSlider.value : 1;
                    videoEl.style.transform = `translate(${{state.x}}px, ${{state.y}}px) scale(${{state.scale}})`;
                    videoEl.style.filter = `brightness(${{brightness}}) contrast(${{contrast}})`;

                    if (brightnessVal) brightnessVal.textContent = parseFloat(brightness).toFixed(2);
                    if (contrastVal) contrastVal.textContent = parseFloat(contrast).toFixed(2);
                }}

                function resetZoom() {{
                    state.scale = 1;
                    state.x = 0;
                    state.y = 0;
                    updateTransform();
                }}

                // --- FULLSCREEN LOGIC ---
                function enterFs() {{
                    vpWrapper.classList.add('vp-fs-active');
                    if (fsEnterIcon) fsEnterIcon.style.display = 'none';
                    if (fsExitIcon) fsExitIcon.style.display = '';
                }}

                function exitFs() {{
                    vpWrapper.classList.remove('vp-fs-active');
                    if (fsEnterIcon) fsEnterIcon.style.display = '';
                    if (fsExitIcon) fsExitIcon.style.display = 'none';
                }}

                if (fsBtn) {{
                    fsBtn.addEventListener('click', () => {{
                        vpWrapper.classList.contains('vp-fs-active') ? exitFs() : enterFs();
                    }});
                }}

                // --- MUTE LOGIC ---
                if (muteBtn) {{
                    muteBtn.addEventListener('click', () => {{
                        videoEl.muted = !videoEl.muted;
                        if (mutedIcon) mutedIcon.style.display = videoEl.muted ? '' : 'none';
                        if (unmutedIcon) unmutedIcon.style.display = videoEl.muted ? 'none' : '';
                        emitEvent('vp_mute_change_{vid_key}', videoEl.muted);
                    }});
                }}

                // --- ZOOM LOGIC (Mouse Wheel) ---
                videoEl.parentElement.addEventListener('wheel', (e) => {{
                    e.preventDefault();
                    const zoomSpeed = 0.1;
                    const delta = -Math.sign(e.deltaY) * zoomSpeed;
                    const newScale = Math.min(Math.max(1, state.scale + delta), 10);

                    if (newScale !== state.scale) {{
                        if (newScale === 1) {{
                            state.x = 0;
                            state.y = 0;
                        }} else {{
                            const rect = videoEl.getBoundingClientRect();
                            const mouseX = e.clientX - rect.left;
                            const mouseY = e.clientY - rect.top;
                            state.x -= (mouseX / state.scale) * (newScale - state.scale);
                            state.y -= (mouseY / state.scale) * (newScale - state.scale);
                        }}
                        state.scale = newScale;
                        updateTransform();
                    }}
                }}, {{ passive: false }});

                // --- PAN LOGIC (Click & Drag) ---
                videoEl.addEventListener('mousedown', (e) => {{
                    if (state.scale > 1) {{
                        state.isDragging = true;
                        state.startX = e.clientX - state.x;
                        state.startY = e.clientY - state.y;
                        videoEl.style.cursor = 'grabbing';
                    }}
                }});

                window.addEventListener('mousemove', (e) => {{
                    if (!state.isDragging) return;
                    state.x = e.clientX - state.startX;
                    state.y = e.clientY - state.startY;
                    updateTransform();
                }});

                window.addEventListener('mouseup', () => {{
                    state.isDragging = false;
                    videoEl.style.cursor = state.scale > 1 ? 'grab' : 'default';
                }});

                brightnessSlider.addEventListener('input', updateTransform);
                contrastSlider.addEventListener('input', updateTransform);

                if (resetBtn) {{
                    resetBtn.addEventListener('click', () => {{
                        brightnessSlider.value = 1;
                        contrastSlider.value = 1;
                        updateTransform();
                    }});
                }}

                if (resetZoomBtn) {{
                    resetZoomBtn.addEventListener('click', resetZoom);
                }}

                if (window.__vpKeyController) window.__vpKeyController.abort();
                window.__vpKeyController = new AbortController();
                window.addEventListener('keydown', (e) => {{
                    if (document.querySelector('.q-dialog[aria-modal="true"]')) return;
                    const _tag = e.target.tagName.toLowerCase();
                    if (_tag === 'input' || _tag === 'textarea' || _tag === 'select') return;
                    if (e.key === 'z') {{
                        resetZoom();
                    }} else if ((e.key === 'r' || e.key === 'R') && !e.ctrlKey && !e.metaKey) {{
                        if (resetBtn) resetBtn.click();
                    }} else if (e.key === 'Escape' && vpWrapper.classList.contains('vp-fs-active')) {{
                        exitFs();
                    }} else if ((e.key === 'f' || e.key === 'F') && !e.ctrlKey && !e.metaKey) {{
                        vpWrapper.classList.contains('vp-fs-active') ? exitFs() : enterFs();
                    }}
                }}, {{ signal: window.__vpKeyController.signal }});

                // Playback and Range Logic
                const btn = document.getElementById('vp-playpause-{vid_key}');
                const playIcon = document.getElementById('vp-play-icon-{vid_key}');
                const pauseIcon = document.getElementById('vp-pause-icon-{vid_key}');

                btn.addEventListener('click', () => videoEl.paused ? videoEl.play() : videoEl.pause());
                let clickTimer = null;
                videoEl.addEventListener('click', () => {{
                    if (clickTimer) return;
                    clickTimer = setTimeout(() => {{
                        clickTimer = null;
                        videoEl.paused ? videoEl.play() : videoEl.pause();
                    }}, 250);
                }});
                videoEl.addEventListener('dblclick', () => {{
                    clearTimeout(clickTimer);
                    clickTimer = null;
                    vpWrapper.classList.contains('vp-fs-active') ? exitFs() : enterFs();
                }});
                videoEl.addEventListener('play', () => {{ playIcon.style.display = 'none'; pauseIcon.style.display = ''; }});
                videoEl.addEventListener('pause', () => {{ playIcon.style.display = ''; pauseIcon.style.display = 'none'; }});

                speedSel.addEventListener('change', () => {{
                    const rate = parseFloat(speedSel.value);
                    videoEl.playbackRate = rate;
                    setTimeout(() => {{
                        const actual = videoEl.playbackRate;
                        if (Math.abs(actual - rate) > 0.05) {{
                            let best = null;
                            for (const opt of speedSel.options) {{
                                if (best === null || Math.abs(parseFloat(opt.value) - actual) < Math.abs(parseFloat(best.value) - actual)) best = opt;
                            }}
                            if (best) speedSel.value = best.value;
                            emitEvent('vp_speed_change_{vid_key}', actual);
                        }} else {{
                            emitEvent('vp_speed_change_{vid_key}', rate);
                        }}
                    }}, 50);
                }});

                videoEl.addEventListener('timeupdate', () => {{
                    if (range._seeking) return;
                    range.value = videoEl.currentTime;
                    const pct = (range.value / range.max) * 100;
                    range.style.setProperty('--pct', pct + '%');
                    document.getElementById('vp-time-{vid_key}').textContent =
                        Math.floor(videoEl.currentTime/60).toString().padStart(2,'0') + ':' +
                        Math.floor(videoEl.currentTime%60).toString().padStart(2,'0') + ' / ' + '{_fmt(duration)}';
                }});

                range.addEventListener('input', () => {{
                    videoEl.currentTime = range.value;
                    const pct = (range.value / range.max) * 100;
                    range.style.setProperty('--pct', pct + '%');
                    document.getElementById('vp-time-{vid_key}').textContent =
                        Math.floor(range.value/60).toString().padStart(2,'0') + ':' +
                        Math.floor(range.value%60).toString().padStart(2,'0') + ' / ' + '{_fmt(duration)}';
                }});
                range.addEventListener('mousedown', () => range._seeking = true);
                range.addEventListener('mouseup', () => range._seeking = false);

                videoEl.style.transformOrigin = '0 0'; // Critical for zoom math
            }})();
        """)

        def _key(k):
            ui.badge(k).props("outline color=grey-6").classes("text-caption")

        with (
            ui.element("div").classes("tour-target-shortcuts"),
            ui.expansion(t("shortcuts_title"), icon="keyboard", value=False)
            .classes("w-full text-caption")
            .props("dense"),
        ):
            with ui.row().classes("w-full q-mt-xs").style("gap: 4px;"):
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 80px; flex: 1; padding: 2px 4px;")
                ):
                    with ui.row().classes("items-center gap-xs"):
                        _key("Space")
                    ui.label(t("shortcut_play_pause")).classes("text-caption")
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 80px; flex: 1; padding: 2px 4px;")
                ):
                    with ui.row().classes("items-center gap-xs"):
                        _key("←")
                        _key("→")
                    ui.label(f"{t('shortcut_seek_back')} / {t('shortcut_seek_forward')}").classes(
                        "text-caption"
                    )
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 80px; flex: 1; padding: 2px 4px;")
                ):
                    with ui.row().classes("items-center gap-xs"):
                        _key("S")
                        _key("D")
                    ui.label(f"{t('shortcut_speed_down')} / {t('shortcut_speed_up')}").classes(
                        "text-caption"
                    )
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 80px; flex: 1; padding: 2px 4px;")
                    .tooltip(t("zoom_tooltip"))
                ):
                    with ui.row().classes("items-center gap-xs"):
                        ui.icon("mouse").classes("")
                        ui.label("+").classes("")
                        ui.icon("pan_tool").classes("")
                    ui.label(t("zoom")).classes("text-caption")
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 80px; flex: 1; padding: 2px 4px;")
                    .tooltip(t("reset_zoom_tooltip"))
                ):
                    with ui.row().classes("items-center gap-xs"):
                        _key("Z")
                    ui.label(t("reset_zoom")).classes("text-caption")

            with ui.row().classes("w-full q-mt-xs").style("gap: 4px;"):
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 90px; flex: 1; padding: 2px 4px;")
                ):
                    with ui.row().classes("items-center gap-xs"):
                        _key("[")
                        _key("]")
                    ui.label(t("shortcut_brightness")).classes("text-caption")
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 90px; flex: 1; padding: 2px 4px;")
                ):
                    with ui.row().classes("items-center gap-xs"):
                        _key("{")
                        _key("}")
                    ui.label(t("shortcut_contrast")).classes("text-caption")
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 90px; flex: 1; padding: 2px 4px;")
                ):
                    with ui.row().classes("items-center gap-xs"):
                        _key("R")
                    ui.label(t("shortcut_reset_filters")).classes("text-caption")
                with (
                    ui.column()
                    .classes("items-center gap-xs")
                    .style("min-width: 90px; flex: 1; padding: 2px 4px;")
                ):
                    with ui.row().classes("items-center gap-xs"):
                        _key("F")
                    ui.label(t("shortcut_fullscreen")).classes("text-caption")

    return v
