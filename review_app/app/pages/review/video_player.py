from pathlib import Path
from urllib.parse import quote

from nicegui import ui

from review_app.app.state import get_playback_speed, set_playback_speed
from review_app.app.translations import t


def render_custom_video_player(video_url, duration, vid_key):
    """Render a video component with custom HTML5 controls and JS synchronization."""
    # Use NiceGUI video component
    from review_app.app.state import is_autoplay, is_muted

    autoplay = is_autoplay()
    muted = is_muted()

    v = ui.video(video_url, autoplay=autoplay, muted=muted, controls=False).classes("w-full")

    def _fmt(s):
        try:
            m, sec = divmod(int(float(s)), 60)
            return f"{m:02d}:{sec:02d}"
        except Exception:
            return "00:00"

    speed_options = [
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
        10.0,
    ]

    speed_options_html = "".join(
        f'<option value="{s}" {"selected" if s == float(get_playback_speed().replace("x", "")) else ""}>{s}x</option>'
        for s in speed_options
    )
    ui.html(f'''
        <style>
            #vp-range-{vid_key} {{
                -webkit-appearance: none;
                appearance: none;
                background: transparent;
            }}
            #vp-range-{vid_key}::-webkit-slider-runnable-track {{
                height: 36px;
                border-radius: 3px;
                background: linear-gradient(to right, var(--q-primary) var(--pct, 0%), #555 var(--pct, 0%));
            }}
            #vp-range-{vid_key}::-moz-range-track {{
                height: 36px;
                border-radius: 3px;
                background: #555;
            }}
            #vp-range-{vid_key}::-moz-range-progress {{
                height: 36px;
                border-radius: 3px;
                background: var(--q-primary);
            }}
            #vp-range-{vid_key}::-webkit-slider-thumb {{
                -webkit-appearance: none;
                width: 36px;
                height: 16px;
                border-radius: 50%;
                background: var(--q-primary);
                margin-top: -5px;
                cursor: pointer;
            }}
            #vp-range-{vid_key}::-moz-range-thumb {{
                width: 36px;
                height: 16px;
                border-radius: 50%;
                background: var(--q-primary);
                border: none;
                cursor: pointer;
            }}
        </style>
        <div style="display:flex;align-items:center;gap:8px;padding:4px 8px 0;width:100%">
            <button id="vp-playpause-{vid_key}"
                    style="flex-shrink:0;width:32px;height:32px;border:none;background:none;cursor:pointer;color:var(--q-primary);display:flex;align-items:center;justify-content:center;padding:0;border-radius:50%">
                <svg id="vp-play-icon-{vid_key}" viewBox="0 0 24 24" width="32" height="32" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>
                <svg id="vp-pause-icon-{vid_key}" viewBox="0 0 24 24" width="32" height="32" fill="currentColor" style="display:none"><path d="M6 19h4V5H6v14zm8-14v14h4V5h-4z"/></svg>
            </button>
            <input type="range" id="vp-range-{vid_key}"
                   min="0" max="{duration}" step="0.1" value="0"
                   style="flex:1;min-width:0;cursor:pointer;height:32px;">
            <span id="vp-time-{vid_key}"
                  style="font-size:16px;color:#888;white-space:nowrap;font-family:monospace">
                00:00 / {_fmt(duration)}
            </span>
            <select id="vp-speed-{vid_key}"
                    style="font-size:15px;font-weight:600;color:var(--q-primary);background:none;border:1px solid var(--q-primary);border-radius:4px;cursor:pointer;font-family:monospace;outline:none;padding:2px 4px;">
                {speed_options_html}
            </select>
        </div>
    ''').classes("full-width")

    current_speed_val = float(get_playback_speed().replace("x", ""))
    _speed_sync = ui.number(value=current_speed_val).style("display:none")
    _speed_sync.on_value_change(lambda e: set_playback_speed(f"{round(e.value, 2)}x"))

    ui.run_javascript(f"""
        (function setup() {{
            const comp = getElement({v.id});
            if (!comp || !comp.$el) {{ setTimeout(setup, 50); return; }}
            const el = comp.$el;
            const videoEl = el.tagName === 'VIDEO' ? el : el.querySelector('video');
            const range = document.getElementById('vp-range-{vid_key}');
            const lbl = document.getElementById('vp-time-{vid_key}');
            const btn = document.getElementById('vp-playpause-{vid_key}');
            const playIcon = document.getElementById('vp-play-icon-{vid_key}');
            const pauseIcon = document.getElementById('vp-pause-icon-{vid_key}');
            const speedSel = document.getElementById('vp-speed-{vid_key}');
            if (!videoEl || !range) return;

            if (speedSel) {{
                speedSel.addEventListener('change', function() {{
                    setSpeed(parseFloat(speedSel.value));
                }});
            }}

            function setSpeed(rate) {{
                videoEl.playbackRate = rate;
                if (speedSel) speedSel.value = Number.isInteger(rate) ? rate.toFixed(1) : String(rate);
                const sync = getElement({_speed_sync.id});
                if (sync) sync.$emit('update:model-value', rate);
            }}

            function syncBtn() {{
                if (videoEl.paused) {{
                    playIcon.style.display = '';
                    pauseIcon.style.display = 'none';
                }} else {{
                    playIcon.style.display = 'none';
                    pauseIcon.style.display = '';
                }}
            }}
            if (btn) {{
                btn.addEventListener('click', function() {{
                    videoEl.paused ? videoEl.play() : videoEl.pause();
                }});
            }}
            videoEl.addEventListener('play', syncBtn);
            videoEl.addEventListener('pause', syncBtn);
            syncBtn();

            const total = '{_fmt(duration)}';
            function fmt(s) {{
                return String(Math.floor(s/60)).padStart(2,'0') + ':' +
                       String(Math.floor(s%60)).padStart(2,'0');
            }}

            function updateTrack() {{
                const pct = range.max > 0 ? (range.value / range.max) * 100 : 0;
                range.style.setProperty('--pct', pct + '%');
            }}
            videoEl.addEventListener('timeupdate', function() {{
                if (!range._seeking) {{
                    range.value = videoEl.currentTime;
                    if (lbl) lbl.textContent = fmt(videoEl.currentTime) + ' / ' + total;
                    updateTrack();
                }}
            }});
            range.addEventListener('input', function() {{ updateTrack(); }});

            range.addEventListener('mousedown', function() {{ range._seeking = true; }});
            range.addEventListener('touchstart', function() {{ range._seeking = true; }}, {{passive:true}});
            range.addEventListener('input', function() {{
                const t = parseFloat(range.value);
                if (lbl) lbl.textContent = fmt(t) + ' / ' + total;
                videoEl.currentTime = t;
                updateTrack();
            }});
            range.addEventListener('mouseup', function() {{
                videoEl.currentTime = parseFloat(range.value);
                range._seeking = false;
            }});
            range.addEventListener('touchend', function() {{
                videoEl.currentTime = parseFloat(range.value);
                range._seeking = false;
            }});

            videoEl.addEventListener('seeked', function() {{
                if (window._resetSpeedOnSeek === false) return;
                videoEl.playbackRate = 1;
                if (speedSel) speedSel.value = '1.0';
                const sync = getElement({_speed_sync.id});
                if (sync) sync.$emit('update:model-value', 1);
            }});

            // Apply and maintain playback speed via event listeners instead of loops
            function applyCurrentSpeed() {{
                const rate = parseFloat(document.getElementById('vp-speed-{vid_key}').value);
                videoEl.playbackRate = rate;
            }}
            videoEl.addEventListener('loadedmetadata', applyCurrentSpeed);
            videoEl.addEventListener('play', applyCurrentSpeed);
            
            // Sync when playbackRate changes (e.g. via keyboard shortcuts or system events)
            videoEl.addEventListener('playbackratechange', () => {{
                if (speedSel) speedSel.value = Number.isInteger(videoEl.playbackRate) ? videoEl.playbackRate.toFixed(1) : String(videoEl.playbackRate);
            }});

            videoEl.addEventListener('speedchange', (e) => {{
                setSpeed(e.detail);
            }});
            applyCurrentSpeed();
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
    return v
