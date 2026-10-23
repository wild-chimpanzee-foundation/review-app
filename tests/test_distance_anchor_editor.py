"""Behavioral checks for the anchor editor's browser-side geometry and frame gating."""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from review_app.app.pages.distance_review import player

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="JavaScript checks require Node")


HARNESS = r"""
const listeners = new WeakMap();
class Element {
    constructor(tag) { this.tagName = tag.toUpperCase(); this.style = {}; this.children = [];
        this.parentElement = null; this.isConnected = true; this.textContent = ''; }
    appendChild(child) { child.parentElement = this; this.children.push(child); return child; }
    replaceChildren(...children) { this.children = []; children.forEach(child => this.appendChild(child)); }
    addEventListener(name, callback) {
        if (!listeners.has(this)) listeners.set(this, {});
        (listeners.get(this)[name] ||= []).push(callback);
    }
    dispatch(name, event = {}) {
        for (const callback of listeners.get(this)?.[name] || []) callback(event);
    }
    setPointerCapture() {}
    setAttribute(name, value) { (this.attributes ||= {})[name] = value; }
    getBoundingClientRect() {
        if (this.tagName === 'SPAN') return {width:this.textContent.length * 8 + 10, height:20};
        return this.rect;
    }
}
const container = new Element('div');
container.rect = {left:0, top:0, width:800, height:500};
const video = new Element('video');
video.parentElement = container; video.paused = true; video.currentTime = 1;
video.readyState = 2; video.videoWidth = 640; video.videoHeight = 360;
video.rect = RECT;
video.pause = () => { video.paused = true; video.dispatch('pause'); };
video.addEventListener = Element.prototype.addEventListener;
video.dispatch = Element.prototype.dispatch;
const elements = {overlay: null};
global.window = {};
global.document = {body: new Element('body'), createElement(tag) { return new Element(tag); },
    createElementNS(_namespace, tag) { return new Element(tag); }};
global.getElement = () => ({$el:video, $emit: (...args) => global.emitEvent(...args)});
global.emitEvent = (_name, payload) => { emitted.push(payload); };
global.ResizeObserver = class { observe() {} disconnect() {} };
global.MutationObserver = class { constructor(callback) { this.callback = callback; }
    observe() {} disconnect() {} };
global.setTimeout = callback => { callback(); return 1; };
const emitted = [];
"""


def run_editor(rect, script_body):
    source = Path(player.__file__).with_name("anchor_editor.js").read_text()
    for key, value in {
        "__PLAYER_ID__": "1",
        "__EVENT__": '"anchor"',
        "__INITIAL__": '{"individual":{"id":"selected","individual_number":2,"first_seen_offset_ms":1000},"individuals":[]}',
        "__MODE__": '"box"',
        "__FRAME_RATE__": "25",
    }.items():
        source = source.replace(key, value)
    return subprocess.run(
        [NODE, "-e", HARNESS.replace("RECT", json.dumps(rect)) + source + script_body],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )


@pytest.mark.parametrize(
    "rect,start,end,expected",
    [
        (
            {"left": -80, "top": 40, "width": 800, "height": 450},
            (80, 175),
            (400, 355),
            {"x": 0.2, "y": 0.3, "width": 0.4, "height": 0.4},
        ),
        (
            {"left": 0, "top": 0, "width": 400, "height": 300},
            (100, 82.5),
            (200, 172.5),
            {"x": 0.25, "y": 0.2, "width": 0.25, "height": 0.4},
        ),
    ],
)
def test_drawing_normalizes_zoom_pan_and_letterboxing(rect, start, end, expected):
    result = run_editor(
        rect,
        f"""
        const overlay = container.children[0];
        overlay.dispatch('pointerdown', {{button:0, pointerId:1, clientX:{start[0]}, clientY:{start[1]}, preventDefault(){{}}}});
        overlay.dispatch('pointermove', {{pointerId:1, clientX:{end[0]}, clientY:{end[1]}}});
        overlay.dispatch('pointerup', {{pointerId:1, clientX:{end[0]}, clientY:{end[1]}}});
        console.log(JSON.stringify(emitted[0].anchor));
        """,
    )
    assert json.loads(result.stdout) == pytest.approx(expected)


def test_adjacent_frame_suppresses_saved_marker_and_missing_number_has_no_undefined_label():
    result = run_editor(
        {"left": 0, "top": 0, "width": 640, "height": 360},
        """
        const saved = {id:'saved', individual_number:1, first_seen_offset_ms:1000,
            anchor_x:.1, anchor_y:.2, anchor_width:.3, anchor_height:.4};
        video._distanceAnchor.configure({individual:{id:'selected', first_seen_offset_ms:1000},
            individuals:[saved]});
        const overlay = container.children[0];
        const atAnchor = overlay.children.length;
        video.currentTime = 1.04;
        video.dispatch('seeked');
        const adjacent = overlay.children.length;
        video.currentTime = 1;
        video.dispatch('seeked');
        const labelTexts = overlay.children.flatMap(box => box.children).map(label => label.textContent);
        console.log(JSON.stringify({atAnchor, adjacent, labelTexts}));
        """,
    )
    result = json.loads(result.stdout)
    assert result["atAnchor"] == 1
    assert result["adjacent"] == 0
    assert result["labelTexts"] == ["#1"]


def test_inactive_and_selected_boxes_render_distinct_colors_and_labels():
    result = run_editor(
        {"left": 0, "top": 0, "width": 640, "height": 360},
        """
        const first = {id:'first', individual_number:1, first_seen_offset_ms:1000,
            anchor_x:.1, anchor_y:.2, anchor_width:.2, anchor_height:.3};
        const second = {id:'second', individual_number:2, first_seen_offset_ms:1000,
            anchor_x:.5, anchor_y:.2, anchor_width:.2, anchor_height:.3};
        const snapshot = () => container.children[0].children.map(box => ({
            border: box.style.cssText.match(/border:3px solid ([^;]+)/)[1],
            label: box.children[0].textContent,
            labelBackground: box.children[0].style.cssText.match(/background:([^;]+)/)[1],
        }));
        video._distanceAnchor.configure({individual:first, individuals:[first, second]});
        const firstSelected = snapshot();
        video._distanceAnchor.configure({individual:second, individuals:[first, second]});
        console.log(JSON.stringify({firstSelected, secondSelected:snapshot()}));
        """,
    )
    result = json.loads(result.stdout)
    gray = "#9e9e9e"
    highlight = "#fbbf24"
    assert result["firstSelected"] == [
        {"border": highlight, "label": "#1", "labelBackground": highlight},
        {"border": gray, "label": "#2", "labelBackground": gray},
    ]
    assert result["secondSelected"] == [
        {"border": gray, "label": "#1", "labelBackground": gray},
        {"border": highlight, "label": "#2", "labelBackground": highlight},
    ]


def test_new_identity_preview_omits_undefined_number_label():
    result = run_editor(
        {"left": 0, "top": 0, "width": 640, "height": 360},
        """
        video._distanceAnchor.configure({individual:{id:'draft',first_seen_offset_ms:1000}, individuals:[]});
        const overlay = container.children[0];
        overlay.dispatch('pointerdown', {button:0,pointerId:1,clientX:50,clientY:50,preventDefault(){}});
        overlay.dispatch('pointermove', {pointerId:1,clientX:150,clientY:150});
        console.log(JSON.stringify(overlay.children[0].children.map(label => label.textContent)));
        """,
    )
    assert json.loads(result.stdout) == []


@pytest.mark.parametrize(
    "rect",
    [
        {"left": 0, "top": 0, "width": 640, "height": 360},
        {"left": 0, "top": 0, "width": 400, "height": 300},
        {"left": 0, "top": 0, "width": 160, "height": 90},
        {"left": -80, "top": 40, "width": 960, "height": 540},
    ],
)
@pytest.mark.parametrize("position", ["identical_top", "identical_bottom", "partial"])
def test_overlapping_boxes_keep_distinct_outlines_and_readable_labels(rect, position):
    result = run_editor(
        rect,
        """
        const position = POSITION;
        const anchor = position === 'identical_bottom'
            ? {anchor_x:.9, anchor_y:.7, anchor_width:.1, anchor_height:.3}
            : {anchor_x:0, anchor_y:0, anchor_width:.3, anchor_height:.3};
        const rows = [1, 2, 3].map(number => ({id:`individual-${number}`,
            individual_number:number, first_seen_offset_ms:1000, ...anchor}));
        if (position === 'partial') {
            rows[1].anchor_y = .1;
            rows[2].anchor_x = .15;
        }
        const unrelated = {...rows[0], id:'unrelated', individual_number:99, first_seen_offset_ms:1040};
        const before = JSON.stringify(rows);
        const snapshot = () => container.children[0].children.map(box => {
            const label = box.children[0], size = label.getBoundingClientRect();
            const left = parseFloat(box.style.left), top = parseFloat(box.style.top);
            return {left, top, width:parseFloat(box.style.width), height:parseFloat(box.style.height),
                color:box.style.cssText.match(/border:3px solid ([^;]+)/)[1],
                label:{text:label.textContent,
                    left:left + 3 + parseFloat(label.style.left),
                    top:top + 3 + parseFloat(label.style.top), ...size,
                    background:label.style.cssText.match(/background:([^;]+)/)[1],
                    foreground:label.style.cssText.match(/;color:([^;]+)/)[1]}};
        });
        video._distanceAnchor.configure({individual:rows[0], individuals:[...rows, unrelated]});
        const first = snapshot();
        video._distanceAnchor.configure({individual:rows[2], individuals:[...rows, unrelated]});
        const selectedThird = snapshot();
        video._distanceAnchor.configure({individual:rows[2], individuals:rows});
        const withoutUnrelated = snapshot();
        video.dispatch('seeked');
        const repainted = snapshot();
        video.paused = false;
        video.dispatch('play');
        const playing = container.children[0].children.length;
        video.paused = true;
        video.currentTime = 1.08;
        video.dispatch('seeked');
        console.log(JSON.stringify({first, selectedThird, withoutUnrelated, repainted, playing,
            adjacent:container.children[0].children.length, unchanged:before === JSON.stringify(rows)}));
        """.replace("POSITION", json.dumps(position)),
    )
    actual = json.loads(result.stdout)
    assert actual["unchanged"]
    assert actual["playing"] == actual["adjacent"] == 0
    assert actual["selectedThird"] == actual["withoutUnrelated"] == actual["repainted"]
    assert len(actual["first"]) == 3
    for index, (first, changed) in enumerate(
        zip(actual["first"], actual["selectedThird"], strict=True)
    ):
        assert first["color"] == ("#fbbf24" if index == 0 else "#9e9e9e")
        assert changed["color"] == ("#fbbf24" if index == 2 else "#9e9e9e")
        for box in (first, changed):
            assert box["label"]["text"] == f"#{index + 1}"
            assert box["label"]["background"] == box["color"]
            assert box["label"]["foreground"] == "#111"
        assert [first[key] for key in ("left", "top", "width", "height")] == [
            changed[key] for key in ("left", "top", "width", "height")
        ]
        assert first["label"]["left"] == changed["label"]["left"]
        assert first["label"]["top"] == changed["label"]["top"]

    scale = min(rect["width"] / 640, rect["height"] / 360)
    content_left = rect["left"] + (rect["width"] - 640 * scale) / 2
    content_top = rect["top"] + (rect["height"] - 360 * scale) / 2
    boxes = actual["first"]
    for index, box in enumerate(boxes):
        label = box["label"]
        assert (
            max(0, content_left)
            <= label["left"]
            <= min(800, content_left + 640 * scale) - label["width"]
        )
        assert (
            max(0, content_top)
            <= label["top"]
            <= min(500, content_top + 360 * scale) - label["height"]
        )
        for other in boxes[:index]:
            # Parallel shared edges must have at least a border-width gap.
            if (
                box["top"] < other["top"] + other["height"]
                and other["top"] < box["top"] + box["height"]
            ):
                assert abs(box["left"] - other["left"]) >= 4
                assert abs(box["left"] + box["width"] - other["left"] - other["width"]) >= 4
            if (
                box["left"] < other["left"] + other["width"]
                and other["left"] < box["left"] + box["width"]
            ):
                assert abs(box["top"] - other["top"]) >= 4
                assert abs(box["top"] + box["height"] - other["top"] - other["height"]) >= 4
            other_label = other["label"]
            assert (
                label["left"] + label["width"] <= other_label["left"]
                or other_label["left"] + other_label["width"] <= label["left"]
                or label["top"] + label["height"] <= other_label["top"]
                or other_label["top"] + other_label["height"] <= label["top"]
            )


def test_display_insets_do_not_change_drawing_payload():
    result = run_editor(
        {"left": 0, "top": 0, "width": 640, "height": 360},
        """
        const rows = [1, 2].map(number => ({id:`individual-${number}`, individual_number:number,
            first_seen_offset_ms:1000, anchor_x:.1, anchor_y:.2, anchor_width:.3, anchor_height:.4}));
        const before = JSON.stringify(rows);
        video._distanceAnchor.configure({individual:rows[1], individuals:rows});
        const overlay = container.children[0];
        overlay.dispatch('pointerdown', {button:0,pointerId:1,clientX:64,clientY:72,preventDefault(){}});
        overlay.dispatch('pointermove', {pointerId:1,clientX:256,clientY:216});
        overlay.dispatch('pointerup', {pointerId:1,clientX:256,clientY:216});
        console.log(JSON.stringify({emitted, unchanged:before === JSON.stringify(rows)}));
        """,
    )
    actual = json.loads(result.stdout)
    assert actual["unchanged"]
    assert len(actual["emitted"]) == 1
    payload = actual["emitted"][0]
    assert payload["individual_id"] == "individual-2"
    assert payload["offset_ms"] == 1000
    assert payload["anchor"] == pytest.approx({"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4})


async def test_image_filters_copies_current_player_brightness_and_contrast(monkeypatch):
    evaluate = AsyncMock(return_value={"brightness": 1.35, "contrast": 0.8})
    monkeypatch.setattr(player.ui, "run_javascript", evaluate)

    actual = await player.image_filters(type("Player", (), {"id": 42})())

    assert actual == {"brightness": 1.35, "contrast": 0.8}
    assert "vp-brightness-" in evaluate.await_args.args[0]
    assert "vp-contrast-" in evaluate.await_args.args[0]


def test_animal_mask_column_major_geometry_and_frame_visibility():
    result = run_editor(
        {"left": 0, "top": 0, "width": 400, "height": 300},
        """
        video._distanceAnchor.configure({individual:{id:'one'}, individuals:[],
            masks:[{individual_id:'one', time_s:1.000000001, size:[2,3], counts:[1,3,2]}]});
        video._distanceAnchor.setMode(null);
        const overlay = container.children[0];
        const svg = overlay.children[0];
        const snapshot = {viewBox:svg.attributes.viewBox,
            path:svg.children[0].attributes.d, fill:svg.children[0].attributes.fill,
            left:svg.style.left, top:svg.style.top, width:svg.style.width, height:svg.style.height};
        video.currentTime = 1.04; video.dispatch('seeked');
        snapshot.adjacent = overlay.children.length;
        video.currentTime = 1; video.seeking = true; video.dispatch('seeking');
        snapshot.seeking = overlay.children.length;
        video.seeking = false; video.paused = false; video.dispatch('play');
        snapshot.playing = overlay.children.length;
        video.paused = true; video.dispatch('pause');
        snapshot.restored = overlay.children.length;
        video._distanceAnchor.configure({individual:{id:'one'}, individuals:[], show_masks:false});
        snapshot.hidden = overlay.children.length;
        console.log(JSON.stringify(snapshot));
        """,
    )
    actual = json.loads(result.stdout)
    assert actual == {
        "viewBox": "0 0 3 2",
        "path": "M0 1h1v1h-1zM1 0h1v2h-1z",
        "fill": "#fbbf24",
        "left": "0px",
        "top": "37.5px",
        "width": "400px",
        "height": "225px",
        "adjacent": 0,
        "seeking": 0,
        "playing": 0,
        "restored": 1,
        "hidden": 0,
    }
