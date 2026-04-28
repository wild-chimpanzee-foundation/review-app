from nicegui import ui

from review_app.app.state import get_playback_speed, is_autoplay, is_muted, set_playback_speed
from review_app.app.translations import t


def render_custom_video_player(video_url, duration, vid_key):
    """Render a video component with mouse-wheel zoom, drag-to-pan, and custom controls."""

    autoplay = is_autoplay()
    muted = is_muted()

    # We wrap the video in a container to act as the 'clipping' viewport
    with (
        ui.element("div")
        .classes("relative-position overflow-hidden w-full")
        .style(f"id: vp-container-{vid_key}; border: 1px solid #333; line-height: 0;")
    ):
        v = ui.video(video_url, autoplay=autoplay, muted=muted, controls=False).classes("w-full")

    def _fmt(s):
        try:
            m, sec = divmod(int(float(s)), 60)
            return f"{m:02d}:{sec:02d}"
        except:
            return "00:00"

    speed_options = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0]
    speed_options_html = "".join(
        f'<option value="{s}" {"selected" if s == float(get_playback_speed().replace("x", "")) else ""}>{s}x</option>'
        for s in speed_options
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
        <div style="display:flex;align-items:center;gap:8px;padding:4px 8px 0;width:100%">
            <button id="vp-playpause-{vid_key}" style="flex-shrink:0;width:32px;height:32px;border:none;background:none;cursor:pointer;color:var(--q-primary);">
                <svg id="vp-play-icon-{vid_key}" viewBox="0 0 24 24" width="32" height="32" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                <svg id="vp-pause-icon-{vid_key}" viewBox="0 0 24 24" width="32" height="32" fill="currentColor" style="display:none"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
            </button>
            <input type="range" id="vp-range-{vid_key}" min="0" max="{duration}" step="0.1" value="0" style="flex:1;cursor:pointer;height:32px;">
            <span id="vp-time-{vid_key}" style="font-size:16px;color:#888;font-family:monospace">00:00 / {_fmt(duration)}</span>
            <select id="vp-speed-{vid_key}" style="font-size:15px;font-weight:600;color:var(--q-primary);background:none;border:1px solid var(--q-primary);border-radius:4px;cursor:pointer;padding:2px 4px;">
                {speed_options_html}
            </select>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px;padding:4px 8px;width:100%;">
            <div style="display:flex;align-items:center;gap:8px;width:100%;">
                <span style="font-size:12px;color:#888;width:70px;">Brightness</span>
                <input type="range" id="vp-brightness-{vid_key}" min="0.5" max="2" step="0.01" value="1" style="flex:1;cursor:pointer;">
                <span id="vp-brightness-val-{vid_key}" style="font-size:12px;color:#888;width:30px;text-align:right;">1.00</span>
            </div>
            <div style="display:flex;align-items:center;gap:8px;width:100%;">
                <span style="font-size:12px;color:#888;width:70px;">Contrast</span>
                <input type="range" id="vp-contrast-{vid_key}" min="0.5" max="2" step="0.01" value="1" style="flex:1;cursor:pointer;">
                <span id="vp-contrast-val-{vid_key}" style="font-size:12px;color:#888;width:30px;text-align:right;">1.00</span>
            </div>
            <div style="display:flex;align-items:center;justify-content:space-between;width:100%;margin-top:2px;">
                <div style="display:flex;gap:4px;">
                    <button id="vp-reset-filters-{vid_key}" style="font-size:12px;color:#888;background:none;border:1px solid #444;border-radius:3px;padding:2px 8px;cursor:pointer;">Reset Filters</button>
                    <button id="vp-reset-zoom-{vid_key}" style="font-size:12px;color:#888;background:none;border:1px solid #444;border-radius:3px;padding:2px 8px;cursor:pointer;">Reset Zoom</button>
                </div>
                <span style="font-size:12px;color:#666;">Wheel to Zoom • Drag to Pan</span>
            </div>
        </div>
    ''').classes("full-width")

    _speed_sync = ui.number(value=float(get_playback_speed().replace("x", ""))).style(
        "display:none"
    )
    _speed_sync.on_value_change(lambda e: set_playback_speed(f"{round(e.value, 2)}x"))

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
            
            let state = {{
                scale: 1,
                x: 0,
                y: 0,
                isDragging: false,
                startX: 0,
                startY: 0
            }};

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

            // Existing brightness/contrast listeners
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

            window.addEventListener('keydown', (e) => {{
                if (e.key === 'z') {{
                    resetZoom();
                }}
            }});

            // Playback and Range Logic (Condensed)
            const btn = document.getElementById('vp-playpause-{vid_key}');
            const playIcon = document.getElementById('vp-play-icon-{vid_key}');
            const pauseIcon = document.getElementById('vp-pause-icon-{vid_key}');
            
            btn.addEventListener('click', () => videoEl.paused ? videoEl.play() : videoEl.pause());
            videoEl.addEventListener('play', () => {{ playIcon.style.display = 'none'; pauseIcon.style.display = ''; }});
            videoEl.addEventListener('pause', () => {{ playIcon.style.display = ''; pauseIcon.style.display = 'none'; }});
            
            speedSel.addEventListener('change', () => {{
                const rate = parseFloat(speedSel.value);
                videoEl.playbackRate = rate;
                getElement({_speed_sync.id}).$emit('update:model-value', rate);
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

            range.addEventListener('input', () => videoEl.currentTime = range.value);
            range.addEventListener('mousedown', () => range._seeking = true);
            range.addEventListener('mouseup', () => range._seeking = false);

            videoEl.style.transformOrigin = '0 0'; // Critical for math logic
        }})();
    """)

    def _key(k):
        ui.badge(k).props("outline color=grey-6").classes("text-caption text-grey-6")

    with ui.row().classes("w-full justify-between q-mt-xs"):
        with ui.column().classes("col items-center gap-xs"):
            with ui.row().classes("items-center gap-xs"):
                _key("Space")
            ui.label(t("shortcut_play_pause")).classes("text-caption text-grey-6")
        with ui.column().classes("col items-center gap-xs"):
            with ui.row().classes("items-center gap-xs"):
                _key("←")
                _key("→")
            ui.label(f"{t('shortcut_seek_back')} / {t('shortcut_seek_forward')}").classes(
                "text-caption text-grey-6"
            )
        with ui.column().classes("col items-center gap-xs"):
            with ui.row().classes("items-center gap-xs"):
                _key("S")
                _key("D")
            ui.label(f"{t('shortcut_speed_down')} / {t('shortcut_speed_up')}").classes(
                "text-caption text-grey-6"
            )
        with ui.column().classes("col items-center gap-xs").tooltip(t("zoom_tooltip")):
            with ui.row().classes("items-center gap-xs"):
                ui.icon("mouse").classes("text-grey-6")
                ui.label("+").classes("text-grey-6")
                ui.icon("pan_tool").classes("text-grey-6")
            ui.label(t("zoom")).classes("text-caption text-grey-6")
        with ui.column().classes("col items-center gap-xs").tooltip(t("reset_zoom_tooltip")):
            with ui.row().classes("items-center gap-xs"):
                _key("Z")
            ui.label(t("reset_zoom")).classes("text-caption text-grey-6")
    return v
