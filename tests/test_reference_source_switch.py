"""Reference source switching preserves its boolean result and snapshot lifecycle."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from review_app.app.pages.distance_review import player as player_module

NODE = shutil.which("node")


@pytest.mark.parametrize("ready", [True, False])
async def test_switch_preserves_result_without_timing_logs(monkeypatch, caplog, ready):
    player = SimpleNamespace(id=7, source="/old.mp4", set_source=Mock())
    javascript = AsyncMock(side_effect=[True, ready])
    monkeypatch.setattr(player_module.ui, "run_javascript", javascript)
    with caplog.at_level("INFO", logger=player_module.__name__):
        assert await player_module.switch_source(player, "/next.mp4", 2500) is ready
    player.set_source.assert_called_once_with("/next.mp4")
    assert javascript.await_count == 2
    assert "Distance reference" not in caplog.text


async def test_failed_preparation_does_not_switch_source(monkeypatch):
    player = SimpleNamespace(id=7, source="/old.mp4", set_source=Mock())
    javascript = AsyncMock(return_value=False)
    monkeypatch.setattr(player_module.ui, "run_javascript", javascript)
    assert not await player_module.switch_source(player, "/next.mp4", 2500)
    player.set_source.assert_not_called()
    assert javascript.await_count == 1


async def test_same_source_still_uses_the_existing_seek_fast_path(monkeypatch):
    player = SimpleNamespace(id=7, source="/same.mp4", set_source=Mock())
    seek, javascript = Mock(), AsyncMock()
    monkeypatch.setattr(player_module, "seek_paused", seek)
    monkeypatch.setattr(player_module.ui, "run_javascript", javascript)
    assert await player_module.switch_source(player, "/same.mp4", 2500)
    seek.assert_called_once_with(player, 2500)
    javascript.assert_not_awaited()


@pytest.mark.skipif(NODE is None, reason="Browser-side checks require Node")
@pytest.mark.parametrize("outcome", ["success", "error", "cancel", "timeout"])
def test_browser_keeps_snapshot_until_ready_and_cleans_up(outcome):
    source = Path(player_module.__file__).with_name("source_switch.js").read_text()
    source = (
        source.replace("__VIDEO__", "media")
        .replace("__SOURCE__", '"/next.mp4"')
        .replace("__OFFSET__", "2.5")
    )
    harness = """
        let watchdog, removed = 0, target = null;
        const handlers = new Map(), frames = [];
        global.window = {location:{href:'http://localhost/'}};
        global.setTimeout = callback => {watchdog = callback; return 1;};
        global.clearTimeout = () => {};
        global.requestAnimationFrame = callback => frames.push(callback);
        global.document = {createElement() {return {
            style:{}, getContext() {return {drawImage() {}};},
            remove() {removed++;},
        };}};
        const video = {
            style:{visibility:'visible'}, videoWidth:1280, videoHeight:720,
            readyState:2, seeking:false, duration:10, currentSrc:'http://localhost/old.mp4',
            parentElement:{appendChild() {}}, pause() {},
            addEventListener(name, callback, options) {
                if (!handlers.has(name)) handlers.set(name, []);
                handlers.get(name).push({callback, signal:options.signal});
            },
            set currentTime(value) {target = value; this.seeking = true;},
        };
        function emit(name) {
            for (const {callback, signal} of handlers.get(name) || []) {
                if (!signal.aborted) callback();
            }
        }
    """
    exercise = """
        video.currentSrc = 'http://localhost/next.mp4';
        emit('loadedmetadata');
        const stillCovered = video.style.visibility === 'hidden' && removed === 0;
        if (OUTCOME === 'success') {
            video.seeking = false; emit('seeked'); emit('loadeddata');
            if (frames.length !== 1) throw Error('Duplicate reveal scheduled');
            frames.shift()(); frames.shift()();
        } else {
            if (OUTCOME === 'error') emit('error');
            if (OUTCOME === 'cancel') video._distanceSourceSwitch.cancel();
            if (OUTCOME === 'timeout') watchdog();
        }
        video._distanceSourceSwitch.promise.then(ready => {
            emit('seeked'); emit('loadeddata');
            console.log(JSON.stringify({ready, target, stillCovered,
                visibility:video.style.visibility, removed, pendingFrames:frames.length}));
        });
    """.replace("OUTCOME", json.dumps(outcome))
    result = subprocess.run(
        [
            NODE,
            "-e",
            harness + "\nnew Function('media', " + json.dumps(source) + ")(video);\n" + exercise,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=5,
    )
    observed = json.loads(result.stdout)
    assert observed["ready"] is (outcome == "success")
    assert observed["target"] == 2.5 and observed["stillCovered"]
    assert observed["visibility"] == "visible" and observed["removed"] == 1
    assert observed["pendingFrames"] == 0
