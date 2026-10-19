"""Optional real Chromium regression: uv run --with playwright pytest -q THIS_FILE."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.async_api")


@pytest.fixture
def companion_server(tmp_path):
    if not shutil.which("chromium") or not shutil.which("ffmpeg"):
        pytest.skip("Companion browser regression requires Chromium and ffmpeg")
    videos = tmp_path / "videos" / "cam_x"
    videos.mkdir(parents=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=640x360:rate=25",
            "-t",
            "10",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(videos / "a.mp4"),
        ],
        check=True,
        timeout=30,
    )
    for name in ("b.mp4", "reference.mp4"):
        shutil.copyfile(videos / "a.mp4", videos / name)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    root = Path(__file__).resolve().parents[1]
    log_path = tmp_path / "server.log"
    with log_path.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                str(root / "tests/fixtures/distance_companion_app.py"),
                str(tmp_path),
                str(port),
            ],
            cwd=root,
            env={
                **{
                    key: value for key, value in os.environ.items() if key != "PYTEST_CURRENT_TEST"
                },
                "PYTHONPATH": str(root),
                "NICEGUI_STORAGE_PATH": str(tmp_path / "storage"),
            },
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            for _ in range(200):
                if process.poll() is not None:
                    pytest.fail(log_path.read_text())
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("Browser harness did not start: " + log_path.read_text())
            yield f"http://127.0.0.1:{port}", tmp_path
        finally:
            process.terminate()
            process.wait(timeout=10)


async def test_companion_controls_review_replay_reattachment_and_source_changes(companion_server):
    url, artifacts = companion_server
    async with playwright.async_playwright() as p:
        browser = await p.chromium.launch(
            executable_path=shutil.which("chromium"), args=["--no-sandbox"], headless=True
        )
        context = await browser.new_context(viewport={"width": 1600, "height": 1000})
        main = await context.new_page()
        errors = []
        main.on("pageerror", lambda error: errors.append(str(error)))

        async def state():
            response = await context.request.get(url + "/test-state")
            return await response.json()

        async def settled(predicate):
            for _ in range(200):
                current = await state()
                if not current.get("busy", True) and predicate(current):
                    return current
                await asyncio.sleep(0.05)
            pytest.fail(f"Review did not settle: {await state()}; JS errors: {errors}")

        async def player_state(page):
            return await page.locator("video:visible").first.evaluate(
                "v => v._reviewPlayer.getState()"
            )

        async def open_companion():
            async with main.expect_popup() as opened:
                await main.locator('[data-distance-action="popout-animal"]').click()
            child = await opened.value
            child.on("pageerror", lambda error: errors.append(str(error)))
            await child.get_by_text("Animal video in companion window", exact=True).wait_for()
            await settled(lambda current: current["detached"])
            return child

        try:
            await main.goto(url)
            await main.wait_for_function(
                "!!document.querySelector('video')?._reviewPlayer && "
                "document.querySelector('video').readyState >= 2"
            )
            initial = await settled(lambda current: current["sample_offset"] == 1000)
            await main.evaluate("window.savedOpen = window.open; window.open = () => null")
            await main.locator('[data-distance-action="popout-animal"]').click()
            await main.get_by_text(
                "The browser blocked the companion window.", exact=False
            ).wait_for()
            assert not (await state())["detached"]
            await main.evaluate("window.open = window.savedOpen")
            await main.get_by_role("button", name="Absent", exact=True).click()
            draft = await state()
            await main.locator('input[id^="vp-brightness-"]').first.evaluate(
                "e => { e.value = 1.4; e.dispatchEvent(new Event('input')); }"
            )
            await main.locator("video").first.hover()
            await main.mouse.wheel(0, -200)
            before = await player_state(main)
            assert before["scale"] > 1
            child = await open_companion()
            remote = await player_state(child)
            assert remote["offset_ms"] == pytest.approx(1000, abs=1)
            assert remote["brightness"] == pytest.approx(1.4)
            assert remote["scale"] == before["scale"]
            assert await child.locator('select[id^="vp-speed-"]').input_value() == "1.0"
            await child.set_viewport_size({"width": 1100, "height": 500})
            controls = await child.locator('button[id^="vp-playpause-"]').bounding_box()
            assert controls and controls["y"] + controls["height"] <= 500
            assert (await state())["selected_frame_id"] == draft["selected_frame_id"]
            assert (await state())["draft"] == draft["draft"]
            assert (await state())["samples"] == []
            assert await main.locator(".distance-grid-detached").count() == 1
            assert await main.locator(".distance-tracks").is_visible()
            assert await main.locator("video:visible").count() == 1  # Reference only.
            await main.screenshot(path=str(artifacts / "detached-main.png"))
            await child.screenshot(path=str(artifacts / "companion.png"))

            await main.evaluate("document.activeElement.blur()")
            await main.keyboard.press("ArrowRight")
            await settled(lambda current: current["sample_offset"] == 3000)
            await child.wait_for_function(
                "Math.abs(document.querySelector('video').currentTime - 3) < .001"
            )

            # Inspect in the child, then manually pause: retain existing sample snapping.
            await child.locator('input[id^="vp-range-"]').evaluate(
                "e => { e.value = 6.2; e.dispatchEvent(new Event('input')); }"
            )
            await child.wait_for_function("!document.querySelector('video').seeking")
            await child.locator(".distance-player").focus()
            await child.keyboard.press("Space")
            await asyncio.sleep(0.2)
            await child.keyboard.press("Space")
            await settled(lambda current: current["sample_offset"] == 5000)
            await child.wait_for_function(
                "document.querySelector('video').paused && Math.abs(document.querySelector('video').currentTime - 5) < .001"
            )
            assert (await state())["samples"] == []

            await main.get_by_role("button", name="Absent", exact=True).click()
            await main.locator('[data-distance-action="confirm"]').click()
            saved = await settled(lambda current: current["sample_offset"] == 7000)
            assert [sample["offset_ms"] for sample in saved["samples"]] == [5000]
            assert (await player_state(child))["offset_ms"] == pytest.approx(7000, abs=1)
            await child.locator('input[id^="vp-brightness-"]').evaluate(
                "e => { e.value = 1.6; e.dispatchEvent(new Event('input')); }"
            )
            remote = await player_state(child)
            async with child.expect_event("close"):
                await main.locator('[data-distance-action="reattach-animal"]').click()
            await settled(lambda current: not current["detached"])
            inline = await player_state(main)
            for key in ("offset_ms", "brightness", "contrast", "scale", "pan_x", "pan_y"):
                assert inline[key] == pytest.approx(remote[key], abs=0.001)

            child = await open_companion()
            previous_source = await child.locator("video").get_attribute("src")
            await main.evaluate("document.activeElement.blur()")
            await main.keyboard.press("n")
            changed = await settled(lambda current: current["video_id"] != initial["video_id"])
            assert changed["detached"] and changed["sample_offset"] == 1000
            await child.wait_for_function(
                "previous => { const v = document.querySelector('video'); return v && v.readyState >= 2 && v.getAttribute('src') !== previous && Math.abs(v.currentTime - 1) < .001; }",
                arg=previous_source,
            )
            await child.close()
            await asyncio.sleep(0.3)
            await main.get_by_role("button", name="Absent", exact=True).click()
            await main.locator('[data-distance-action="confirm"]').click()
            await main.get_by_text(
                "Wait for the video to load before saving a frame.", exact=True
            ).wait_for(timeout=5000)
            assert (await state())["samples"] == []
            assert (await state())["detached"]  # Closing never implicitly reattaches.
            child = await open_companion()
            assert (await player_state(child))["offset_ms"] == pytest.approx(1000, abs=1)
            await child.close()
            await asyncio.sleep(0.3)
            await main.locator('[data-distance-action="reattach-animal"]').click()
            await settled(lambda current: not current["detached"])
            assert (await player_state(main))["offset_ms"] == pytest.approx(1000, abs=1)
            assert not errors
        finally:
            await browser.close()
