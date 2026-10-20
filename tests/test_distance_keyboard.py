"""Browser-side key routing and interval playback contracts."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from review_app.app.pages.distance_review import player as player_module

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="JavaScript checks require Node")


def run_javascript(source):
    result = subprocess.run(
        [NODE, "-e", source], capture_output=True, text=True, check=True, timeout=5
    )
    return json.loads(result.stdout)


def test_enter_opens_clock_setup_until_aligned_then_confirms_and_a_only_adds():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler, settings, clicked, prevented;
        global.window = {addEventListener(type, callback) {handler = callback;}};
        global.document = {querySelector(selector) {
            if (selector.includes('data-distance-clock-dialog')) return null;
            if (selector.startsWith('.q-dialog')) return settings.dialog ? {} : null;
            const action = selector.match(/data-distance-action="([^"]+)"/)[1];
            const exists = action === 'verify-clock' ? !settings.verified && !settings.railOnly
                : action === 'rail-clock' ? !settings.verified && settings.railOnly
                : ['add', 'confirm'].includes(action) && settings.verified;
            return exists ? {disabled:settings.disabled, click() {clicked = action;}} : null;
        }};
        """
        + source
        + """
        const cases = [
            ['Enter', {}], ['Enter', {railOnly:true}], ['Enter', {verified:true}],
            ['A', {verified:true}], ['a', {}], ['A', {}],
            ['Enter', {editable:true}], ['Enter', {dialog:true}], ['Enter', {disabled:true}],
        ];
        const result = cases.map(([key, flags]) => {
            settings = flags; clicked = null; prevented = false;
            handler({key, ...flags, target:{closest() {return flags.editable || null;}},
                preventDefault() {prevented = true;}, stopPropagation() {}});
            return [clicked, prevented];
        });
        console.log(JSON.stringify(result));
        """
    )
    assert result == [
        ["verify-clock", True],
        ["rail-clock", True],
        ["confirm", True],
        ["add", True],
        [None, False],
        [None, False],
        [None, False],
        [None, False],
        [None, True],
    ]


def test_enter_confirms_behavior_prompt_without_routing_background_actions():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler, flags, clicked, prevented;
        global.window = {addEventListener(type, callback) {handler = callback;}};
        global.document = {querySelector(selector) {
            if (selector.includes('data-distance-clock-dialog')) return null;
            if (!selector.startsWith('.q-dialog')) throw Error('Background action');
            return {
                matches() {return true;},
                querySelector() {
                    return {disabled:flags.disabled, click() {clicked = true;}};
                }
            };
        }};
        """
        + source
        + """
        const cases = [
            ['Enter', {}], ['Enter', {disabled:true}], ['Enter', {ctrlKey:true}],
            ['ArrowRight', {}], ['Enter', {editable:true}], ['Enter', {repeat:true}]
        ];
        const result = cases.map(([key, settings]) => {
            flags = settings; clicked = false; prevented = false;
            handler({key, ...settings, target:{closest() {return flags.editable || null;}},
                preventDefault() {prevented = true;}, stopPropagation() {}});
            return [clicked, prevented];
        });
        console.log(JSON.stringify(result));
        """
    )
    assert result == [[True, True], [False, True], *[[False, False]] * 4]


def test_behavior_arrows_move_focus_and_leave_space_escape_and_tab_native():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler, focused = 1, prevented, scrolled;
        global.window = {addEventListener(type, callback) {handler = callback;}};
        const options = [0, 1, 2].map(index => ({
            getAttribute(name) {return name === 'aria-checked' && index === 1 ? 'true' : null;},
            contains(element) {return element === this;},
            focus() {focused = index; document.activeElement = this;},
            scrollIntoView() {scrolled = index;}
        }));
        const dialog = {
            matches() {return true;},
            querySelectorAll() {return options;}
        };
        global.document = {
            activeElement: options[1],
            querySelector(selector) {
                if (selector.includes('data-distance-clock-dialog')) return null;
                if (selector.startsWith('.q-dialog')) return dialog;
                throw Error('Background action');
            }
        };
        """
        + source
        + """
        const result = ['ArrowDown', 'ArrowDown', 'ArrowUp', ' ', 'Escape', 'Tab'].map(key => {
            prevented = false; scrolled = null;
            handler({key, target:{closest() {return null;}},
                preventDefault() {prevented = true;}, stopPropagation() {}});
            return [focused, prevented, scrolled];
        });
        console.log(JSON.stringify(result));
        """
    )
    assert result == [
        [2, True, 2],
        [0, True, 0],
        [2, True, 2],
        *[[2, False, None]] * 3,
    ]


def test_generic_dialog_arrows_and_enter_respect_focus_and_native_controls():
    source = Path(player_module.__file__).with_name("dialog_keyboard.js").read_text()
    result = run_javascript(
        """
        let focused, clicked, prevented;
        const controls = [0, 1, 2, 3].map(index => ({
            disabled: index === 1, tabIndex: 0,
            getAttribute() {return null;},
            getClientRects() {return index === 3 ? [] : [{}];},
            contains(element) {return element === this;},
            focus() {focused = index; document.activeElement = this;},
            scrollIntoView() {},
            click() {clicked = index;}
        }));
        const panel = {
            querySelectorAll() {return controls;},
            querySelector() {return controls[2];},
            contains(control) {return controls.includes(control);}
        };
        global.document = {};
        const handle = """
        + source
        + """;
        const cases = [
            ['ArrowLeft', 2, {}], ['ArrowRight', 0, {}], ['ArrowRight', 2, {}],
            ['ArrowUp', 0, {}], ['ArrowDown', 0, {}],
            ['Enter', 0, {}], ['Enter', 2, {}], ['Enter', -1, {}],
            ['Enter', 1, {}], ['Enter', 3, {}],
            ['ArrowDown', 2, {editable:true}], ['Enter', 2, {editable:true}],
            ['ArrowRight', 2, {player:true}], ['Enter', 2, {repeat:true}],
            ['ArrowLeft', 2, {shiftKey:true}], ['Enter', 2, {ctrlKey:true}],
            ['Tab', 2, {}], ['Escape', 2, {}], [' ', 2, {}],
        ];
        const results = cases.map(([key, index, flags]) => {
            focused = index; clicked = null; prevented = false;
            document.activeElement = controls[index];
            handle({key, ...flags, currentTarget:panel, target:{closest(selector) {
                if (selector.startsWith('input')) return flags.editable || null;
                if (selector.startsWith('[id^=')) return flags.player || null;
                return controls[index] || null;
            }}, preventDefault() {prevented = true;}, stopPropagation() {}});
            return [focused, clicked, prevented];
        });
        console.log(JSON.stringify(results));
        """
    )
    assert result == [
        [0, None, True],
        [2, None, True],
        [0, None, True],
        [2, None, True],
        [2, None, True],
        [0, 0, True],
        [2, 2, True],
        [-1, 2, True],
        [1, None, True],
        [3, None, True],
        *[[2, None, False]] * 9,
    ]


def test_nested_confirmation_keys_never_reach_background_clock_or_sample_actions():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler;
        global.window = {addEventListener(type, callback) {handler = callback;}};
        global.document = {querySelector() {throw Error('Background dialog or sample action');}};
        """
        + source
        + """
        const results = ['Enter', 'ArrowLeft', 'ArrowRight', 'Escape', 'Tab'].map(key => {
            let prevented = false;
            handler({key, target:{closest(selector) {
                return selector.includes('data-distance-dialog-keyboard') ? {} : null;
            }}, preventDefault() {prevented = true;}, stopPropagation() {}});
            return prevented;
        });
        console.log(JSON.stringify(results));
        """
    )
    assert result == [False] * 5


def test_ctrl_enter_applies_to_end_without_bypassing_shortcut_guards():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler, settings, clicked, prevented, stopped;
        global.window = {addEventListener(type, callback) {handler = callback;}};
        global.document = {querySelector(selector) {
            if (selector.includes('data-distance-clock-dialog')) return settings.clock ? {} : null;
            if (selector.startsWith('.q-dialog')) return settings.dialog ? {} : null;
            const action = selector.match(/data-distance-action="([^"]+)"/)[1];
            if (settings.missing || !['apply-to-end', 'confirm'].includes(action)) return null;
            return {disabled:settings.disabled, click() {clicked = action;}};
        }};
        """
        + source
        + """
        const cases = [
            {}, {disabled:true}, {missing:true}, {editable:true}, {dialog:true}, {clock:true},
            {repeat:true}, {isComposing:true}, {shiftKey:true}, {altKey:true}, {metaKey:true},
            {ctrlKey:false},
        ];
        const results = cases.map(flags => {
            settings = flags; clicked = null; prevented = false; stopped = false;
            handler({key:'Enter', ctrlKey:true, ...flags,
                target:{closest() {return flags.editable || null;}},
                preventDefault() {prevented = true;}, stopPropagation() {stopped = true;}});
            return [clicked, prevented, stopped];
        });
        console.log(JSON.stringify(results));
        """
    )
    assert result == [
        ["apply-to-end", True, True],
        [None, True, True],
        *[[None, False, False]] * 9,
        ["confirm", True, True],
    ]


def test_arrow_keys_capture_navigation_and_preserve_fields_dialogs_and_shift_seeking():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler, options, clicked, prevented, stopped, emitted, settings;
        global.window = {addEventListener(type, callback, config) {handler = callback; options = config;}};
        global.emitEvent = (name, args) => {emitted = [name, args.direction];};
        global.document = {querySelector(selector) {
            if (selector.includes('data-distance-clock-dialog')) {
                return settings.clock ? {querySelector(actionSelector) {
                    const step = actionSelector.match(/data-distance-clock-step="(-?\\d+)"/);
                    const action = actionSelector.match(/data-distance-clock-action="([^"]+)"/);
                    const name = step ? `step:${step[1]}` : action?.[1];
                    return name ? {disabled:settings.disabled, click() {clicked = name;}} : null;
                }} : null;
            }
            if (selector.startsWith('.q-dialog')) return settings.dialog ? {} : null;
            const action = selector.match(/data-distance-action="([^"]+)"/)[1];
            if (action === 'verify-clock' || action === 'rail-clock') return null;
            return {disabled: settings.disabled, click() {clicked = action;}};
        }};
        """
        + source
        + """
        const cases = [
            ['ArrowLeft', {}], ['ArrowRight', {}], ['ArrowUp', {}], ['ArrowDown', {}],
            ['Enter', {}], ['ArrowRight', {editable:true}], ['ArrowUp', {dialog:true}],
            ['ArrowRight', {shiftKey:true}], ['ArrowDown', {repeat:true}],
            ['ArrowUp', {disabled:true}], ['ArrowRight', {ctrlKey:true}], ['7', {}],
            ['Tab', {}], ['Tab', {editable:true}], ['Tab', {dialog:true}], ['Tab', {disabled:true}],
            ['Tab', {shiftKey:true}], [' ', {}],
            ['Tab', {shiftKey:true, editable:true}], ['Tab', {shiftKey:true, dialog:true}],
            ['Tab', {shiftKey:true, disabled:true}],
        ];
        const results = cases.map(([key, flags]) => {
            settings = flags; clicked = null; prevented = false; stopped = false; emitted = null;
            handler({key, ...flags,
                target: {closest() {return flags.editable || null;}},
                preventDefault() {prevented = true;}, stopPropagation() {stopped = true;},
            });
            return [clicked, prevented, stopped, emitted];
        });
        console.log(JSON.stringify({capture:options.capture, results}));
    """
    )
    assert result["capture"] is True
    assert result["results"] == [
        [None, True, True, ["__DISTANCE_SAMPLE_NAV_EVENT__", "previous"]],
        [None, True, True, ["__DISTANCE_SAMPLE_NAV_EVENT__", "next"]],
        ["distance-up", True, True, None],
        ["distance-down", True, True, None],
        ["confirm", True, True, None],
        [None, False, False, None],
        [None, False, False, None],
        [None, False, False, None],
        [None, False, False, None],
        [None, True, True, None],
        [None, False, False, None],
        [None, False, False, None],
        ["next-reference", True, True, None],
        [None, False, False, None],
        [None, False, False, None],
        [None, True, True, None],
        ["previous-reference", True, True, None],
        [None, False, False, None],
        [None, False, False, None],
        [None, False, False, None],
        [None, True, True, None],
    ]


def test_r_replays_current_sample_without_resetting_filters_and_respects_guards():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler, flags, clicked, prevented, stopped;
        global.window = {addEventListener(type, callback) {handler = callback;}};
        global.document = {querySelector(selector) {
            if (selector.includes('data-distance-clock-dialog')) return null;
            if (selector.startsWith('.q-dialog')) return flags.dialog ? {} : null;
            if (selector !== '[data-distance-action="replay"]') throw Error('Wrong action');
            return flags.missing ? null : {
                disabled:flags.disabled, click() {clicked = true;}
            };
        }};
        """
        + source
        + """
        const cases = [
            ['r', {}], ['R', {}], ['r', {disabled:true}], ['R', {missing:true}],
            ['r', {editable:true}], ['R', {dialog:true}], ['r', {ctrlKey:true}],
            ['R', {metaKey:true}], ['r', {altKey:true}], ['r', {repeat:true}],
            ['r', {isComposing:true}],
        ];
        const results = cases.map(([key, settings]) => {
            flags = settings; clicked = false; prevented = false; stopped = false;
            handler({key, ...settings, target:{closest() {return flags.editable || null;}},
                preventDefault() {prevented = true;}, stopPropagation() {stopped = true;}});
            return [clicked, prevented, stopped];
        });
        console.log(JSON.stringify(results));
        """
    )
    assert result == [
        [True, True, True],
        [True, True, True],
        [False, True, True],
        [False, True, True],
        *[[False, False, False]] * 7,
    ]


def test_individual_keys_dispatch_card_selection_and_respect_navigation_guards():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler, clicked, prevented, stopped, settings, cards;
        global.window = {addEventListener(type, callback) {handler = callback;}};
        global.document = {
            querySelectorAll() {return cards;},
            querySelector(selector) {return settings.dialog ? {} : null;},
        };
        function card(active, disabled=false) {
            const button = {disabled, click() {clicked = cards.indexOf(thisCard);}};
            const thisCard = {dataset:{distanceActive:String(active)},
                querySelector() {return button;}};
            return thisCard;
        }
        """
        + source
        + """
        const cases = [
            ['j', 1, 0, {}], ['K', 1, 0, {}], ['j', -1, 0, {}],
            ['k', -1, 1, {}], ['k', 0, 2, {}], ['j', 2, 0, {}],
            ['j', 1, null, {dialog:true}], ['J', 1, 2, {shiftKey:true}],
            ['j', 1, null, {repeat:true}], ['j', 1, null, {isComposing:true}],
            ['j', 1, null, {ctrlKey:true}], ['j', 1, null, {editable:true}],
            ['j', 1, null, {disabled:true}], ['j', -2, null, {}], ['x', 1, null, {}],
            ['j', 0, 0, {single:true}], ['k', 0, 0, {single:true}],
            ['K', 1, 0, {shiftKey:true}], ['j', 1, null, {altKey:true}],
            ['k', 1, null, {metaKey:true}],
        ];
        const results = cases.map(([key, active, expected, flags]) => {
            settings = flags; clicked = null; prevented = false; stopped = false;
            cards = flags.single ? [card(true)] : active === -2 ? []
                : active === -1 ? [card(false), card(false), card(false)]
                : [card(active === 0), card(active === 1), card(active === 2)];
            if (flags.disabled && expected === null) cards[2].querySelector().disabled = true;
            if (flags.editable) cards[1].querySelector = () => null;
            handler({key, ...flags, target:{closest() {return flags.editable || null;}},
                preventDefault() {prevented = true;}, stopPropagation() {stopped = true;}});
            return [clicked, prevented, stopped];
        });
        console.log(JSON.stringify(results));
    """
    )
    assert result == [
        [2, True, True],
        [0, True, True],
        [0, True, True],
        [1, True, True],
        [2, True, True],
        [0, True, True],
        [None, False, False],
        [2, True, True],
        [None, False, False],
        [None, False, False],
        [None, False, False],
        [None, False, False],
        [None, True, True],
        [None, False, False],
        [None, False, False],
        [0, True, True],
        [0, True, True],
        [0, True, True],
        [None, False, False],
        [None, False, False],
    ]


def test_clock_dialog_shortcuts_are_scoped_and_keep_editable_and_busy_guards():
    source = Path(player_module.__file__).with_name("keyboard.js").read_text()
    result = run_javascript(
        """
        let handler, clicked, prevented, settings;
        global.window = {addEventListener(type, callback) {handler = callback;}};
        global.document = {querySelector(selector) {
            if (selector.includes('data-distance-clock-dialog')) {
                return settings.clock ? {querySelector(actionSelector) {
                    const step = actionSelector.match(/data-distance-clock-step="(-?\\d+)"/);
                    const action = actionSelector.match(/data-distance-clock-action="([^"]+)"/);
                    const name = step ? `step:${step[1]}` : action?.[1];
                    return name ? {disabled:settings.disabled, click() {clicked = name;}} : null;
                }} : null;
            }
            if (selector.startsWith('.q-dialog')) return settings.dialog ? {} : null;
            return null;
        }};
        """
        + source
        + """
        const cases = [
            ['ArrowLeft', {clock:true}], ['ArrowRight', {clock:true}], ['Enter', {clock:true}],
            ['Enter', {clock:true, editable:true}], ['ArrowLeft', {clock:true, disabled:true}],
            ['s', {clock:true}], ['v', {clock:true}], ['Enter', {dialog:true}],
        ];
        const results = cases.map(([key, flags]) => {
            settings = flags; clicked = null; prevented = false;
            handler({key, ...flags, target:{closest() {return flags.editable || null;}},
                preventDefault() {prevented = true;}, stopPropagation() {}});
            return [clicked, prevented];
        });
        console.log(JSON.stringify(results));
        """
    )
    assert result == [
        ["step:-1", True],
        ["step:1", True],
        ["confirm", True],
        [None, False],
        [None, True],
        [None, False],
        [None, False],
        [None, False],
    ]


@pytest.mark.parametrize(
    "scenario",
    ["played", "resume", "blocked", "paused", "wrong_seek", "cancelled_seek", "ended", "late"],
)
@pytest.mark.parametrize("from_ms,to_ms,rate", [(0, 2000, 1), (750, 2750, 2), (1000, 5000, 0.5)])
async def test_interval_playback_targets_the_scheduled_frame_and_handles_interruption(
    monkeypatch, scenario, from_ms, to_ms, rate
):
    observed = {}

    async def execute(source, **_):
        observed.update(
            run_javascript(
                """
            const source = """
                + json.dumps(source)
                + "; const scenario = "
                + json.dumps(scenario)
                + ";"
                + f"const from = {from_ms / 1000}, end = {to_ms / 1000}, rate = {rate};"
                + """
            let time = from, playCalls = 0, decoded = false;
            if (scenario === 'resume') time += 0.04;
            if (scenario === 'wrong_seek' || scenario === 'cancelled_seek') time -= 0.5;
            const seeks = [], watched = [];
            const listeners = new Map();
            const emit = event => {for (const handler of listeners.get(event) || []) handler();};
            const video = {
                isConnected:true, paused:true, seeking:false, readyState:2, duration:10,
                playbackRate:rate,
                addEventListener(event, handler) {
                    if (!listeners.has(event)) listeners.set(event, new Set());
                    listeners.get(event).add(handler);
                },
                removeEventListener(event, handler) {listeners.get(event)?.delete(handler);},
                get currentTime() {return time;},
                set currentTime(value) {
                    seeks.push(value); time = value; this.seeking = true;
                    setImmediate(() => {
                        if (scenario === 'wrong_seek') time -= 0.01;
                        if (scenario === 'cancelled_seek') this._distancePlayback++;
                        this.seeking = false; decoded = true; emit('seeked');
                    });
                },
                pause() {this.paused = true;},
                play() {
                    playCalls++;
                    if (scenario === 'blocked') return Promise.reject(new Error('Autoplay blocked'));
                    this.paused = false; return Promise.resolve();
                },
            };
            const animation = callback => setImmediate(() => {
                if (!video.paused) {
                    time += 0.07 * video.playbackRate; watched.push(time);
                    if (scenario === 'late') time = end + 0.5;
                    if (scenario === 'paused' && time >= from + 0.5) video.paused = true;
                    if (scenario === 'ended' && time >= end) {video.paused = true; emit('ended');}
                }
                callback();
            });
            new Function('getElement', 'requestAnimationFrame', 'cancelAnimationFrame',
                'return (async () => {' + source + '})();')(
                    () => ({$el:video}), animation, clearImmediate
                ).then(completed => console.log(JSON.stringify({
                    completed, seeks, watched, time, playCalls, decoded, seeking:video.seeking,
                    listeners:Array.from(listeners.values()).reduce((n, handlers) => n + handlers.size, 0),
                })));
        """
            )
        )
        return observed["completed"]

    monkeypatch.setattr(player_module.ui, "run_javascript", execute)
    if scenario in ("played", "resume", "ended", "late"):
        await player_module.play_to_sample(SimpleNamespace(id=1), from_ms, to_ms)
        assert observed["time"] == pytest.approx(to_ms / 1000)
        if scenario != "late":
            assert observed["watched"][-1] >= to_ms / 1000
        expected_seeks = [] if scenario == "resume" and to_ms < 5000 else [to_ms / 1000]
        assert observed["seeks"] == expected_seeks
        assert not observed["seeking"]
    else:
        with pytest.raises(ValueError, match="distance_playback_interrupted"):
            await player_module.play_to_sample(SimpleNamespace(id=1), from_ms, to_ms)
        if scenario in ("blocked", "paused"):
            assert observed["time"] < to_ms / 1000 and observed["seeks"] == []
    assert observed["playCalls"] == (0 if scenario in ("wrong_seek", "cancelled_seek") else 1)
    assert observed["listeners"] == 0


@pytest.mark.parametrize(
    "operation",
    ["seek", "step", "capture", "already_paused_seek", "already_paused_capture"],
)
async def test_programmatic_pause_suppression_waits_for_delayed_event_and_expires(
    monkeypatch, operation
):
    listener_source = {}
    monkeypatch.setattr(player_module.ui, "on", lambda *_: None)
    monkeypatch.setattr(
        player_module.ui,
        "run_javascript",
        lambda source, **_: listener_source.setdefault("source", source),
    )
    player_module.install_manual_pause_snap(SimpleNamespace(id=12), lambda *_: None)
    operation_source = {}
    if operation.endswith("seek"):
        monkeypatch.setattr(
            player_module.ui,
            "run_javascript",
            lambda source, **_: operation_source.setdefault("source", source),
        )
        player_module.seek_paused(SimpleNamespace(id=12), 1000)
    else:

        async def capture_source(source, **_):
            operation_source["source"] = source
            return 1000 if operation.endswith("capture") else True

        monkeypatch.setattr(player_module.ui, "run_javascript", capture_source)
        if operation.endswith("capture"):
            await player_module.capture_offset(SimpleNamespace(id=12), exact=True)
        else:
            await player_module.step_frames(SimpleNamespace(id=12), 1, 25)

    start_paused = operation.startswith("already_paused")
    result = run_javascript(
        "const handlers = new Map(), emitted = []; let time = 0;"
        + "global.document={body:{}}; global.MutationObserver=class {observe() {} disconnect() {}};"
        + f"const video = {{isConnected:true, paused:{str(start_paused).lower()}, seeking:false, readyState:2, duration:10,"
        + "addEventListener(name, handler) {if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(handler);},"
        + "removeEventListener(name, handler) {handlers.get(name)?.delete(handler);},"
        + "pause() {if (this.paused) return; this.paused = true; setImmediate(() => "
        + "{for (const handler of handlers.get('pause') || []) handler();});},"
        + "play() {this.paused = false; return Promise.resolve();},"
        + "get currentTime() {return time;}, set currentTime(value) {time=value;}};"
        + "global.emitEvent = (name, args) => emitted.push([name, args.offset_ms]);"
        + "new Function('getElement', 'emitEvent', "
        + json.dumps(listener_source["source"])
        + ")( () => ({$el:video}), global.emitEvent);"
        + "new Function('getElement', "
        + json.dumps(operation_source["source"])
        + ")( () => ({$el:video}));"
        + "setImmediate(() => {video.play(); video.pause(); setTimeout(() => "
        + "console.log(JSON.stringify({emitted, paused:video.paused})), 10);});"
    )
    expected_offset = 40.001 if operation == "step" else 1000 if operation.endswith("seek") else 0
    assert len(result["emitted"]) == 1
    assert result["emitted"][0][0] == "distance_manual_pause_12"
    assert result["emitted"][0][1] == pytest.approx(expected_offset)
    assert result["paused"]


@pytest.mark.parametrize(
    "time,target,retention,seeks",
    [
        (1.08, 1000.01, 250, []),
        (0.98, 1000.01, 250, [1.00001]),
        (3, 1000.01, 250, [1.00001]),
        (0.04, 0, 0, [0]),
    ],
)
def test_sample_return_keeps_a_forward_stop_but_allows_manual_previous(
    monkeypatch, time, target, retention, seeks
):
    observed = {}

    def execute(source, **_):
        observed.update(
            run_javascript(
                f"let time = {time}; const seeks = [];"
                + "const video = {readyState:2, seeking:false, pause() {},"
                + "get currentTime() {return time;}, set currentTime(value) {seeks.push(value); time=value;}};"
                + "new Function('getElement', "
                + json.dumps(source)
                + ")(() => ({$el:video}));"
                + "console.log(JSON.stringify({seeks, time}));"
            )
        )

    monkeypatch.setattr(player_module.ui, "run_javascript", execute)
    player_module.seek_paused(SimpleNamespace(id=1), target, retain_after_ms=retention)
    assert observed["seeks"] == seeks


async def test_automatic_stop_suppresses_delayed_pause_then_reports_user_pause(monkeypatch):
    listener_source = {}
    monkeypatch.setattr(player_module.ui, "on", lambda *_: None)
    monkeypatch.setattr(
        player_module.ui,
        "run_javascript",
        lambda source, **_: listener_source.setdefault("source", source),
    )
    player_module.install_manual_pause_snap(SimpleNamespace(id=12), lambda *_: None)
    script = (
        Path(player_module.__file__)
        .with_name("sample_playback.js")
        .read_text()
        .replace("__VIDEO__", "getElement(12)?.$el")
        .replace("__FROM__", "0")
        .replace("__TO__", "0.1")
        .replace("__MAX_DELAY__", "0.25")
        .replace("__WATCHDOG__", "1000")
    )
    result = run_javascript(
        "const handlers = new Map(), emitted = []; let time = 0;"
        + "global.document={body:{}}; global.MutationObserver=class {observe() {} disconnect() {}};"
        + "const video = {isConnected:true, paused:true, seeking:false, readyState:2, duration:2, playbackRate:1,"
        + "addEventListener(name, handler) {if (!handlers.has(name)) handlers.set(name, new Set()); handlers.get(name).add(handler);},"
        + "removeEventListener(name, handler) {handlers.get(name)?.delete(handler);},"
        + "pause() {if (this.paused) return; this.paused = true; setImmediate(() => "
        + "{for (const handler of handlers.get('pause') || []) handler();});},"
        + "play() {this.paused = false; return Promise.resolve();},"
        + "get currentTime() {return time;}, set currentTime(value) {time=value; this.seeking=true;"
        + "setImmediate(() => {this.seeking=false; for (const handler of handlers.get('seeked') || []) handler();});}};"
        + "global.emitEvent = (name, args) => emitted.push([name, args.offset_ms]);"
        + "new Function('getElement', 'emitEvent', "
        + json.dumps(listener_source["source"])
        + ")( () => ({$el:video}), global.emitEvent);"
        + "new Function('getElement', 'requestAnimationFrame', 'cancelAnimationFrame',"
        + json.dumps("return (async()=>{" + script + "})();")
        + ")( () => ({$el:video}), callback => setImmediate(() => {if (!video.paused) time += .2; callback();}), clearImmediate)"
        + ".then(completed => setImmediate(() => {const afterAuto=emitted.length; video.play(); video.pause();"
        + "setImmediate(() => console.log(JSON.stringify({completed, time, afterAuto, emitted})));}));"
    )
    assert result["completed"]
    assert result["time"] == pytest.approx(0.1)
    assert result["afterAuto"] == 0
    assert result["emitted"] == [["distance_manual_pause_12", 100]]


def test_manual_pause_listener_follows_remounted_video_and_ignores_detached_events(monkeypatch):
    scripts = []
    monkeypatch.setattr(player_module.ui, "on", lambda *_: None)
    monkeypatch.setattr(player_module.ui, "run_javascript", lambda source: scripts.append(source))
    player_module.install_manual_pause_snap(SimpleNamespace(id=12), lambda *_: None)
    result = run_javascript(
        "const observers=[], emitted=[]; global.document={body:{}};"
        + "global.MutationObserver=class {constructor(callback) {this.callback=callback; observers.push(this);}"
        + "observe() {} disconnect() {this.disconnected=true;}};"
        + "function makeVideo() {return {isConnected:true, seeking:false, currentTime:3.2, handlers:[],"
        + "addEventListener(name, callback) {this.handlers.push(callback);},"
        + "pause() {this.handlers.forEach(callback=>callback());}};}"
        + "const first=makeVideo(), second=makeVideo(); let current=first;"
        + "global.emitEvent=(name,args)=>emitted.push([name,args.offset_ms]);"
        + "const install=new Function('getElement','emitEvent',"
        + json.dumps(scripts[0])
        + "); const get=()=>current ? {$el:current} : null;"
        + "install(get,emitEvent); install(get,emitEvent);"
        + "first.isConnected=false; current=second; observers[0].callback(); first.pause();"
        + "setTimeout(()=>{second._distanceSuppressedPauseEvents=1; second.pause(); second.pause();"
        + "second.isConnected=false; current=null; observers[1].callback();"
        + "setTimeout(()=>console.log(JSON.stringify({emitted,counts:[first.handlers.length,second.handlers.length],"
        + "disconnected:observers.every(observer=>observer.disconnected)})),75);},75);"
    )
    assert result == {
        "emitted": [["distance_manual_pause_12", 3200]],
        "counts": [1, 1],
        "disconnected": True,
    }


async def test_frame_callback_waits_for_a_forward_frame_without_seeking_back(monkeypatch):
    observed = {}

    async def execute(source, **_):
        observed.update(
            run_javascript(
                "let time=1.00001, displayedTime; const seeks=[], listeners=new Map();"
                + "const video={isConnected:true, readyState:2, duration:10, paused:true, seeking:false,"
                + "playbackRate:1, pause(){this.paused=true;}, play(){this.paused=false; return Promise.resolve();},"
                + "get currentTime(){return time;}, set currentTime(value){seeks.push(value); time=value;},"
                + "addEventListener(e,h){listeners.set(e,h);}, removeEventListener(e,h){listeners.delete(e);},"
                + "requestVideoFrameCallback(callback){return setImmediate(()=>{"
                + "displayedTime=Math.floor(time*25)/25; callback(0,{mediaTime:displayedTime});});},"
                + "cancelVideoFrameCallback:clearImmediate};"
                + "const animation=callback=>setImmediate(()=>{if(!video.paused)time+=.02; callback();});"
                + "new Function('getElement','requestAnimationFrame','cancelAnimationFrame',"
                + json.dumps("return (async()=>{" + source + "})();")
                + ")(()=>({$el:video}),animation,clearImmediate).then(completed=>"
                + "console.log(JSON.stringify({completed,time,displayedTime,seeks})));"
            )
        )
        return observed["completed"]

    monkeypatch.setattr(player_module.ui, "run_javascript", execute)
    await player_module.play_to_sample(SimpleNamespace(id=1), 1000.01, 2000.01)
    assert observed["time"] >= 2.00001
    assert observed["displayedTime"] >= 2
    assert observed["seeks"] == []


@pytest.mark.parametrize("seeking,ready_state", [(False, 2), (True, 2), (False, 1)])
@pytest.mark.parametrize("exact", [False, True])
async def test_capture_requires_a_decoded_still_after_seeking(
    monkeypatch, seeking, ready_state, exact
):
    observed = {}

    async def execute(source, **_):
        observed.update(
            run_javascript(
                f"const video = {{currentTime:0.749999, duration:10, readyState:{ready_state},"
                f"seeking:{json.dumps(seeking)}, paused:false, pause() {{this.paused = true;}}}};"
                + "const result = new Function('getElement', "
                + json.dumps(source)
                + ")(() => ({$el:video}));"
                + "console.log(JSON.stringify({result, paused:video.paused}));"
            )
        )
        return observed["result"]

    monkeypatch.setattr(player_module.ui, "run_javascript", execute)
    if seeking or ready_state < 2:
        with pytest.raises(ValueError, match="distance_player_not_ready"):
            await player_module.capture_offset(SimpleNamespace(id=1), exact=exact)
        assert not observed["paused"]
    else:
        assert await player_module.capture_offset(
            SimpleNamespace(id=1), exact=exact
        ) == pytest.approx(749.999 if exact else 750)
        assert observed["paused"]


@pytest.mark.parametrize(
    "start,delta,rate,expected",
    [
        (1, 1, 25, 1.04),
        (1, 5, 25, 1.2),
        (1, -1, 25, 0.96),
        (1, -5, 25, 0.8),
        (0, -5, 25, 0),
        (9.96, 5, 25, 9.96),
        (1.001, 1, 30000 / 1001, 31 * 1001 / 30000),
    ],
)
async def test_frame_steps_pause_and_seek_whole_frames_within_video_bounds(
    monkeypatch, start, delta, rate, expected
):
    observed = {}

    async def execute(source, **_):
        observed.update(
            run_javascript(
                "const source = "
                + json.dumps(source)
                + ";"
                + f"const video = {{currentTime:{start}, duration:10, readyState:2, paused:false,"
                + "pause() {this.paused = true;}};"
                + "new Function('getElement', source)(() => ({$el:video}));"
                + "console.log(JSON.stringify({time:video.currentTime, paused:video.paused}));"
            )
        )
        return True

    monkeypatch.setattr(player_module.ui, "run_javascript", execute)
    await player_module.step_frames(SimpleNamespace(id=1), delta, rate)
    assert observed["paused"]
    assert observed["time"] == pytest.approx(expected, abs=0.00001)
