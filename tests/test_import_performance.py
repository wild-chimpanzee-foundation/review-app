"""Performance characteristics of model-CSV validation.

Context: CI reported an import that ran to completion server-side but left the browser
stuck on the loading spinner, because the NiceGUI client was deleted mid-session. A
client is only deleted after its socket drops, so these tests pin down (a) how long a
validation of a realistic frame takes, and (b) whether running it through run.io_bound
starves the asyncio event loop enough to drop that socket.

The frame size (~205k rows) matches the real CI import. Timings are printed rather than
asserted — they are hardware-dependent, and the annotators' laptops are considerably
slower than a dev machine. Only the loop-starvation check is a hard assertion, since
that one is about mechanism rather than speed.
"""

import asyncio
import time

import pandas as pd
import pytest

N_ROWS = 205_028
N_VIDEOS = 500
SPECIES = ["deer", "fox", "wild_boar_XX", "unknown_thing_YY", "badger"]


def _make_provider(tmp_db):
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_a").mkdir()
    for i in range(N_VIDEOS):
        (video_dir / "cam_a" / f"v{i}.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    queue = dp.get_video_queue({}, active_project_id=None)
    paths = [dp.get_video_detail(v)["video_path"] for v in queue]
    return dp, paths


def _make_frame(paths, n_rows=N_ROWS):
    return pd.DataFrame(
        {
            "video_path": [paths[i % len(paths)] for i in range(n_rows)],
            "annotation_type": "species",
            "model_name": "megadetector",
            "value_text": [SPECIES[i % len(SPECIES)] for i in range(n_rows)],
            "probability": 0.9,
            "t_start_sec": 0.0,
            "t_end_sec": 1.0,
        }
    )


@pytest.mark.slow
def test_validate_model_csv_timing(tmp_db, mock_probe, capsys):
    """Print the cost of one validation pass, with and without a species mapping.

    The app re-runs this on every species-mapping dropdown change, so this cost is paid
    once per mapped species, not once per import.
    """
    dp, paths = _make_provider(tmp_db)
    df = _make_frame(paths)

    with capsys.disabled():
        print(f"\nframe: {len(df)} rows over {len(paths)} videos")
        for label, mappings in [
            ("first pass (no mappings)", {}),
            ("after mapping 1 species", {"wild_boar_XX": "deer"}),
        ]:
            t0 = time.monotonic()
            cleaned, errors, _sm, unmapped = dp.validate_model_csv(df, mappings, None)
            dt = time.monotonic() - t0
            print(
                f"  {label:26s} {dt:6.1f}s  cleaned={len(cleaned):7d} "
                f"errors={len(errors):7d} unmapped={len(unmapped)}"
            )


@pytest.mark.slow
def test_validate_does_not_starve_event_loop(tmp_db, mock_probe, capsys):
    """Validation on a worker thread must not stall the loop past the socket budget.

    NiceGUI runs validation via run.io_bound (a thread). The work is Python-level and
    holds the GIL, but CPython should hand the GIL back every sys.getswitchinterval()
    (5ms), so the loop should keep ticking throughout.

    This matters because engine.io drops the browser socket after roughly
    ping_interval + ping_timeout (~72s at the app's reconnect_timeout=60), and a deleted
    client is what strands the loading spinner. Asserting a bound here means a future
    change that *does* block the loop (e.g. validating inline instead of on a thread)
    fails loudly rather than silently breaking long imports in the field.
    """
    dp, paths = _make_provider(tmp_db)
    df = _make_frame(paths)

    async def _run():
        lags = []
        stop = asyncio.Event()

        async def _sample():
            interval = 0.05
            while not stop.is_set():
                before = time.monotonic()
                await asyncio.sleep(interval)
                lags.append(time.monotonic() - before - interval)

        sampler = asyncio.create_task(_sample())
        t0 = time.monotonic()
        # Mirror the app: validation is pushed to a thread, not run on the loop.
        await asyncio.to_thread(dp.validate_model_csv, df, {}, None)
        elapsed = time.monotonic() - t0
        stop.set()
        await sampler
        return elapsed, max(lags), sum(lags) / len(lags)

    elapsed, worst_lag, mean_lag = asyncio.run(_run())

    with capsys.disabled():
        print(
            f"\nvalidate {len(df)} rows on a thread: {elapsed:.1f}s | "
            f"loop lag worst {worst_lag * 1000:.0f}ms mean {mean_lag * 1000:.0f}ms"
        )

    # Generous next to the ~72s socket budget: this is a mechanism check, not a speed
    # check, so it must hold on a slow laptop too.
    assert worst_lag < 5.0, (
        f"event loop stalled {worst_lag:.1f}s during a threaded validation; "
        "long imports will drop the browser socket and strand the loading spinner"
    )
