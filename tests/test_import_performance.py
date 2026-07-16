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
    """Print the cost of the whole-frame validation, base pass and mapping pass.

    The mapping pass is the one that matters: the import page runs it once per species
    the user maps, so it is what stands between a usable page and one that grinds for
    seconds per dropdown change.
    """
    dp, paths = _make_provider(tmp_db)
    df = _make_frame(paths)

    with capsys.disabled():
        print(f"\nframe: {len(df)} rows over {len(paths)} videos")

        t0 = time.monotonic()
        cleaned, errors, _sm, unmapped = dp.validate_model_csv(df, {}, None)
        print(
            f"  validate_model_csv (both)  {time.monotonic() - t0:6.2f}s  "
            f"cleaned={len(cleaned):7d} errors={len(errors):7d} unmapped={len(unmapped)}"
        )

        t0 = time.monotonic()
        base = dp.validate_model_csv_base(df, None)
        base_dt = time.monotonic() - t0
        print(f"  base pass (once/upload)    {base_dt:6.2f}s  rows={len(base.rows)}")

        for label, mappings in [
            ("apply, no mappings", {}),
            ("apply, 1 species mapped", {"wild_boar_XX": "deer"}),
            ("apply, 2 species mapped", {"wild_boar_XX": "deer", "unknown_thing_YY": "fox"}),
        ]:
            t0 = time.monotonic()
            cleaned, errors, _sm, unmapped = dp.apply_species_mappings(base, mappings)
            apply_dt = time.monotonic() - t0
            print(
                f"  {label:26s} {apply_dt:6.2f}s  cleaned={len(cleaned):7d} "
                f"errors={len(errors):7d} unmapped={len(unmapped)} "
                f"({base_dt / max(apply_dt, 1e-9):.0f}x cheaper than the base pass)"
            )


@pytest.mark.slow
def test_apply_species_mappings_is_fast_enough_for_interactive_use(tmp_db, mock_probe):
    """The mapping pass runs on every dropdown change, so it must feel instant.

    Threshold is loose (annotators' laptops are much slower than a dev machine) but far
    below the ~8s that the old whole-frame revalidation cost per mapped species.
    """
    dp, paths = _make_provider(tmp_db)
    base = dp.validate_model_csv_base(_make_frame(paths), None)

    t0 = time.monotonic()
    dp.apply_species_mappings(base, {"wild_boar_XX": "deer"})
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0, (
        f"applying a species mapping to {N_ROWS} rows took {elapsed:.2f}s; "
        "the import page runs this on every dropdown change"
    )


@pytest.mark.slow
def test_import_annotations_csv_scales_by_rows_not_transactions(tmp_db, mock_probe, capsys):
    """Annotations re-import at annotator-bundle scale must stay in bulk territory.

    The old path paid ~6 queries and 2-3 transactions per video; at tens of thousands
    of videos that is minutes of dead "please wait" and a force-kill risk mid-write.
    The bulk path reads the maps once and commits one transaction, like
    import_model_csv. The bound is generous (slow laptops), but a regression back to
    per-video transactions costs minutes and fails it loudly.
    """
    n_videos = 20_000
    from review_app.backend.provider.local_data_provider import LocalDataProvider

    video_dir = tmp_db["video_dir"]
    (video_dir / "cam_a").mkdir()
    for i in range(n_videos):
        (video_dir / "cam_a" / f"v{i}.mp4").touch()

    dp = LocalDataProvider()
    dp.sync_videos(progress_callback=None, video_dir=video_dir)
    with dp.engine.connect() as conn:
        from sqlalchemy import text

        paths = [r[0] for r in conn.execute(text("SELECT video_path FROM videos")).fetchall()]

    df = pd.DataFrame(
        {
            "video_path": paths,
            "is_blank": [1 if i % 3 == 0 else 0 for i in range(n_videos)],
            "species": [None if i % 3 == 0 else SPECIES[i % 2] for i in range(n_videos)],
            "attributes": [None if i % 3 == 0 else "grazing" for i in range(n_videos)],
            "count": [None if i % 3 == 0 else 1 + i % 4 for i in range(n_videos)],
            "start_sec": 0.0,
            "annotator": [f"annotator_{i % 5}" for i in range(n_videos)],
            "assigned_to": [f"annotator_{i % 5}" for i in range(n_videos)],
            "review_later": 0,
        }
    )

    t0 = time.monotonic()
    result = dp.import_annotations_csv(df, None, mode="override")
    elapsed = time.monotonic() - t0

    with capsys.disabled():
        print(f"\nimport_annotations_csv: {n_videos} videos in {elapsed:.1f}s")

    assert result["imported"] == n_videos
    assert elapsed < 60.0, (
        f"importing {n_videos} annotation rows took {elapsed:.1f}s; "
        "this smells like a regression to per-video queries/transactions"
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
