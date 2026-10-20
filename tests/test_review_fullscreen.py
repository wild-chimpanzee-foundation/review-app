"""Focus requests browser fullscreen inside the user gesture, with graceful fallback."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from review_app.app.components import review_layout


@pytest.mark.parametrize(
    "focused,fullscreen,api,expected",
    [
        (False, None, "supported", ["request", "focus"]),
        (True, "page", "supported", ["exit", "focus"]),
        (False, None, "denied", ["request", "focus"]),
        (False, None, "throws", ["request", "focus"]),
        (False, None, "unsupported", ["focus"]),
        (True, None, "supported", ["focus"]),  # Esc already left fullscreen.
        (False, "video", "supported", ["focus"]),
        (True, "video", "supported", ["focus"]),  # Leave player fullscreen alone.
    ],
)
def test_focus_fullscreen_click(focused, fullscreen, api, expected):
    node = shutil.which("node")
    if node is None:
        pytest.skip("JavaScript checks require Node")
    source = Path(review_layout.__file__).with_suffix(".js").read_text()
    state = {"focused": focused, "fullscreen": fullscreen, "api": api}
    script = "const state = " + json.dumps(state) + ";\n"
    script += """
        const calls = [];
        global.getHtmlElement = id => {
            if (id !== 42) throw Error('Wrong focus button');
            return {getAttribute: name => String(state.focused)};
        };
        global.emit = () => calls.push('focus');
        const page = {};
        function operation(name) {
            calls.push(name);
            if (state.api === 'throws') throw Error('Fullscreen unavailable');
            return state.api === 'denied' ? Promise.reject(Error('Denied')) : Promise.resolve();
        }
        if (state.api !== 'unsupported') page.requestFullscreen = () => operation('request');
        global.document = {
            documentElement: page,
            fullscreenElement: state.fullscreen === 'page' ? page : state.fullscreen,
            exitFullscreen: () => operation('exit'),
        };
        const click =
    """
    script += source.replace("__FOCUS_BUTTON_ID__", "42")
    script += "; click(); console.log(JSON.stringify(calls));"
    result = subprocess.run(
        [node, "--unhandled-rejections=strict", "-e", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    assert json.loads(result.stdout) == expected
