"""Selected distance buttons are revealed by scrolling only their horizontal strip."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from review_app.app.pages.distance_review import reference_views


@pytest.mark.parametrize(
    "button,exists,expected",
    [
        ({"left": 330, "right": 370}, True, 102),
        ({"left": 80, "right": 120}, True, 4),
        ({"left": 150, "right": 190}, True, 30),
        (None, True, 30),  # Absent/Not visible is outside the numeric strip.
        ({"left": 330, "right": 370}, False, 30),  # A newer refresh removed this strip.
    ],
)
def test_selected_distance_scrolls_only_when_outside_strip(button, exists, expected):
    node = shutil.which("node")
    if node is None:
        pytest.skip("JavaScript checks require Node")
    source = Path(reference_views.__file__).with_name("distance_choice_scroll.js").read_text()
    script = "const state = " + json.dumps({"button": button, "exists": exists}) + ";\n"
    script += """
        const frames = [];
        global.requestAnimationFrame = callback => frames.push(callback);
        const strip = {
            scrollLeft: 30, clientLeft: 2, clientWidth: 200,
            getBoundingClientRect() {return {left: 100};},
            isConnected: true,
            querySelector(selector) {
                if (selector !== '[aria-pressed="true"]') throw new Error('Wrong selector');
                return state.button ? {getBoundingClientRect: () => state.button} : null;
            },
        };
        let mounted = false, observerCallback, disconnected = false;
        global.getHtmlElement = id => {
            if (!mounted || id !== 42) throw new Error('Read before layout or wrong strip');
            // NiceGUI HTML elements are DOM nodes, NOT Vue objects with a $el.
            return state.exists ? strip : null;
        };
        global.ResizeObserver = class {
            constructor(callback) {observerCallback = callback;}
            observe(target) {if (target !== strip) throw new Error('Wrong resize target');}
            disconnect() {disconnected = true;}
        };
    """
    script += source.replace("__STRIP_ID__", "42")
    script += """
        mounted = true;
        while (frames.length) frames.shift()();
        if (observerCallback) {
            strip.isConnected = false;
            observerCallback();
            if (!disconnected) throw new Error('Leaked resize observer');
        }
        console.log(JSON.stringify(strip.scrollLeft));
    """
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, check=True, timeout=5
    )
    assert json.loads(result.stdout) == expected
