"""Opt-in workflow stress checks. Run with pytest -m slow tests/test_workflow_stress.py."""

import errno
import random
import shutil
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from stat import S_ISREG
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock, Thread
from time import perf_counter

import pytest
from review_app.app.config import DEFAULT_DB_FILENAME
from review_app.backend.annotation_package import delivery
from review_app.backend.annotation_package.archive import decode_archive
from review_app.backend.annotation_package.naming import package_stem
from review_app.backend.annotation_package.scope import AssignmentRequest
from review_app.backend.db import backup
from review_app.backend.db.models import DistanceReviewTask, IndividualObservation, Species, Video
from review_app.backend.provider import local_data_provider
from review_app.backend.provider.local_data_provider import LocalDataProvider
from sqlalchemy import event, insert, select

pytestmark = pytest.mark.slow

MIB = 1024**2
GIB = 1024**3


@pytest.mark.parametrize("task_count", [1_000, 10_000])
@pytest.mark.parametrize("preexisting_fraction", [0.5, 1.0])
@pytest.mark.parametrize("operation", ["queue", "export preview"])
def test_distance_only_materializes_missing_tasks(
    tmp_db, task_count, preexisting_fraction, operation, capsys
):
    dp = LocalDataProvider()
    project = dp.create_project("Queue stress", str(tmp_db["video_dir"]))
    dp.set_distance_review_species(project.id, ["deer"])
    video_ids = [f"clip-{index:05d}" for index in range(task_count)]
    preexisting = int(task_count * preexisting_fraction)
    try:
        with dp.Session.begin() as session:
            species_id = session.scalar(
                select(Species.id).where(Species.scientific_name == "deer")
            )
            session.execute(
                insert(Video),
                [
                    dict(
                        video_id=video_id,
                        project_id=project.id,
                        video_path=str(tmp_db["video_dir"] / f"{video_id}.mp4"),
                        folder_camera_label="cam_a",
                        duration_sec=10,
                    )
                    for video_id in video_ids
                ],
            )
            session.execute(
                insert(IndividualObservation),
                [
                    dict(
                        video_id=video_id,
                        id=1,
                        species_id=species_id,
                        count=2,
                    )
                    for video_id in video_ids
                ],
            )
            session.execute(
                insert(DistanceReviewTask),
                [
                    dict(id=video_id, video_id=video_id, species_id=species_id, source_count=3)
                    for video_id in video_ids[:preexisting]
                ],
            )

        dp.initialize_project_deployments(project.id)

        task_inserts = insert_calls = selects = 0

        def count_statements(conn, cursor, sql, parameters, context, executemany):
            nonlocal task_inserts, insert_calls, selects
            statement = sql.lstrip().upper()
            if statement.startswith("INSERT") and "distance_review_tasks" in sql:
                task_inserts += len(parameters) if executemany else 1
                insert_calls += 1
            if statement.startswith("SELECT"):
                selects += 1

        def read_tasks():
            if operation == "queue":
                return dp.get_distance_video_queue(project.id, species_id)
            return dp.preview_distance_export(project.id, species_id)["tasks"]

        event.listen(dp.engine, "before_cursor_execute", count_statements)
        try:
            started = perf_counter()
            initial = read_tasks()
            initial_time = perf_counter() - started
            initial_inserts = task_inserts
            task_inserts = insert_calls = selects = 0
            started = perf_counter()
            refreshed = read_tasks()
            refresh_time = perf_counter() - started
        finally:
            event.remove(dp.engine, "before_cursor_execute", count_statements)

        with capsys.disabled():
            print(
                f"\n{operation}, {task_count:,} tasks ({preexisting:,} already created): "
                f"initial {initial_time:.2f}s / {initial_inserts:,} INSERTs, "
                f"refresh {refresh_time:.2f}s / {task_inserts:,} INSERTs / {selects:,} SELECTs"
            )
        assert len(initial) == task_count
        assert refreshed == initial
        with dp.Session() as session:
            assert (
                list(
                    session.scalars(
                        select(DistanceReviewTask.source_count).where(
                            DistanceReviewTask.id == DistanceReviewTask.video_id
                        )
                    )
                )
                == [3] * preexisting
            )
        assert initial_inserts == task_count - preexisting
        assert task_inserts == insert_calls == 0, "refresh must not attempt any task inserts"
        if operation == "export preview":
            assert selects <= 6, "preview eligibility must use a fixed number of SELECTs"
    finally:
        dp.engine.dispose()


@pytest.fixture
def footage_project(tmp_db, mock_probe):
    dp = LocalDataProvider()
    project = dp.create_project("Handoff stress", str(tmp_db["video_dir"]))
    try:
        for root_number in range(3):
            root = (
                tmp_db["video_dir"] if root_number == 0 else tmp_db["root"] / f"root-{root_number}"
            )
            camera = root / "cam_a"
            camera.mkdir(parents=True)
            for index in range(128):
                payload = f"root {root_number}, clip {index:03d}\n".encode().ljust(
                    256 * 1024, b"x"
                )
                (camera / f"clip-{index:03d}.mp4").write_bytes(payload)
            if root_number:
                dp.add_project_dir(project.id, str(root))
            dp.sync_videos(video_dir=root, active_project_id=project.id)
        with dp.Session() as session:
            paths = {
                video_id: Path(path)
                for video_id, path in session.execute(
                    select(Video.video_id, Video.video_path)
                    .where(Video.project_id == project.id)
                    .order_by(Video.video_path)
                )
            }
        assert len(paths) == 384
        roots = {root.id: root.path for root in dp.get_project_dirs(project.id)}
        yield dp, project, roots, paths
    finally:
        dp.engine.dispose()


def test_multi_recipient_delivery_survives_full_disk_and_retries(
    footage_project, tmp_path, monkeypatch, capsys
):
    dp, project, _, paths = footage_project
    original_copy = delivery.shutil.copy2
    started = perf_counter()
    for slot, recipient in enumerate(("alice", "bob", "carol")):
        ids = list(paths)[slot::3]
        saved = dp.prepare_assignment(project.id, AssignmentRequest(recipient, tuple(ids)))
        missing_id = ids[-1]
        missing = paths[missing_id]
        contents = missing.read_bytes()
        missing.unlink()
        started_copies = 0
        copy_lock = Lock()
        expected_failures = set()

        def simulate_full_disk(source, target):
            nonlocal started_copies
            with copy_lock:
                started_copies += 1
                if started_copies > 64:
                    expected_failures.add(source)
                    Path(target).write_bytes(b"partial footage")
                    raise OSError(errno.ENOSPC, "simulated full disk")
            return original_copy(source, target)

        progress = []
        with monkeypatch.context() as patch:
            patch.setattr(delivery.shutil, "copy2", simulate_full_disk)
            partial = dp.save_assignment_delivery(
                project.id,
                saved.id,
                tmp_path / "partial",
                include_footage=True,
                progress_callback=lambda done, total: progress.append((done, total)),
            )
        assert progress == [(done, len(ids)) for done in range(1, len(ids) + 1)]
        assert partial.copied_count == 64
        assert partial.missing_video_ids == (missing_id,)
        failed_ids = {video_id for video_id in ids if paths[video_id] in expected_failures}
        assert len(failed_ids) == 63
        assert set(partial.failures) == failed_ids
        assert all("simulated full disk" in error for error in partial.failures.values())
        assignment = next(row for row in dp.list_assignments(project.id) if row["id"] == saved.id)
        setup_zip = (Path(partial.path) / f"{package_stem(assignment)}.zip").read_bytes()
        assert setup_zip == dp.export_assignment(saved.id)
        _, context, _ = decode_archive(setup_zip)
        assert len(partial.media_roots) == 3
        incomplete = failed_ids | {missing_id}
        assert not list(Path(partial.path).rglob(".copy-*"))
        for row in context["media"]:
            target = Path(partial.media_roots[row["root_id"]]) / row["video_path"]
            if row["video_id"] in incomplete:
                assert not target.exists()
            else:
                assert target.read_bytes() == paths[row["video_id"]].read_bytes()
        assert set(dp.check_assignment_media(setup_zip, partial.media_roots)) == incomplete

        missing.write_bytes(contents)
        retry = dp.save_assignment_delivery(
            project.id, saved.id, tmp_path / "retry", include_footage=True
        )
        assert retry.copied_count == len(ids)
        assert not retry.missing_video_ids and not retry.failures
        assert not dp.check_assignment_media(setup_zip, retry.media_roots)
        for row in context["media"]:
            target = Path(retry.media_roots[row["root_id"]]) / row["video_path"]
            assert target.read_bytes() == paths[row["video_id"]].read_bytes()
        assert set(dp.check_assignment_media(setup_zip, partial.media_roots)) == incomplete
    with capsys.disabled():
        print(
            f"\n3 deliveries, 384 files (96 MiB), fault and retry completed in {perf_counter() - started:.2f}s"
        )


def test_concurrent_overlapping_returns_commit_only_one_preview(footage_project, capsys):
    dp, project, roots, paths = footage_project
    ids = list(paths)
    scopes = [ids[:192], ids[96:288]]
    sentinel = ids[-1]
    dp.update_manual_review(
        sentinel,
        [{"species": "fox", "count": 7, "tags": ["grazing"]}],
        active_project_id=project.id,
    )
    sentinel_review = dp.get_video_detail(sentinel)["manual_selections"]
    attempts = []
    for count, (recipient, scope) in enumerate(zip(("alice", "bob"), scopes), start=2):
        saved = dp.prepare_assignment(project.id, AssignmentRequest(recipient, tuple(scope)))
        setup_zip = dp.export_assignment(saved.id)
        manifest, _, _ = decode_archive(setup_zip)
        opened = dp.open_assignment(
            setup_zip, recipient, {root_id: roots[root_id] for root_id in manifest["roots"]}
        )
        working_ids = dp.get_video_queue({}, active_project_id=opened.project_id)
        assert len(working_ids) == len(scope)
        for video_id in working_ids:
            dp.update_manual_review(
                video_id,
                [{"species": "deer", "count": count, "tags": ["running"]}],
                labeled_by=recipient,
                active_project_id=opened.project_id,
            )
        payload = dp.export_assignment_return(opened.project_id)
        preview = dp.preview_assignment_return(project.id, payload)
        assert preview.totals["added"] == len(scope)
        attempts.append((scope, count, recipient, payload, preview.token))

    start_together = Barrier(2, timeout=10)

    def accept(attempt):
        _, _, _, payload, token = attempt
        start_together.wait()
        try:
            return dp.accept_assignment_return(project.id, payload, "use_imported", token)
        except ValueError as exc:
            assert "stale" in str(exc)
            return None

    started = perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(accept, attempts))
    winners = [attempt for attempt, result in zip(attempts, results) if result is not None]
    assert len(winners) == 1
    winning_scope, count, recipient, _, _ = winners[0]
    winning_ids = set(winning_scope)
    with dp.Session() as session:
        saved = session.execute(
            select(
                IndividualObservation.video_id,
                IndividualObservation.count,
                IndividualObservation.labeled_by,
            )
            .join(Video, Video.video_id == IndividualObservation.video_id)
            .where(Video.project_id == project.id)
        ).all()
    assert {tuple(row) for row in saved} == {
        (video_id, count, recipient) for video_id in winning_ids
    } | {(sentinel, 7, sentinel_review[0]["labeled_by"])}
    assert dp.get_video_detail(sentinel)["manual_selections"] == sentinel_review
    for video_id in winning_scope:
        assert dp.get_video_detail(video_id)["manual_selections"][0]["tags"] == ["running"]
    with capsys.disabled():
        print(
            f"\nConcurrent acceptance: 2 returns × 192 clips, 96 overlapping, one commit in {perf_counter() - started:.2f}s"
        )


def _allocated_bytes(directory):
    total = 0
    for path in directory.rglob("*"):
        try:
            stat = path.stat()
        except FileNotFoundError:
            continue
        if S_ISREG(stat.st_mode):
            total += stat.st_blocks * 512 if hasattr(stat, "st_blocks") else stat.st_size
    return total


def test_restore_one_gib_and_recover_after_process_exit(tmp_path, monkeypatch, capsys):
    if shutil.disk_usage(tmp_path).free < 5 * GIB:
        pytest.skip("1 GiB restore stress check requires 5 GiB of free space")
    with TemporaryDirectory(dir=tmp_path) as temporary:
        data_dir = Path(temporary)
        monkeypatch.setattr(backup, "get_user_data_dir", lambda: data_dir)
        monkeypatch.setattr(local_data_provider, "get_user_data_dir", lambda: data_dir)
        db_path = data_dir / DEFAULT_DB_FILENAME
        dp = LocalDataProvider()
        dp.set_setting("restore-stress", "backup version")
        dp.engine.dispose()

        # A repeated 1 MiB random block stays effectively incompressible with gzip's
        # 32 KiB window and exercises a large backup without a large Python allocation.
        payload = random.Random(0).randbytes(MIB)
        with closing(sqlite3.connect(db_path)) as connection, connection:
            connection.execute("CREATE TABLE stress_payload (body BLOB NOT NULL)")
            connection.executemany(
                "INSERT INTO stress_payload VALUES (?)", ((payload,) for _ in range(1024))
            )
        assert db_path.stat().st_size >= GIB
        started = perf_counter()
        candidate = backup.create_backup(reason="stress", auto_prune=False)
        backup_time = perf_counter() - started

        original = LocalDataProvider()
        original.set_setting("restore-stress", "original before first restore")
        original.engine.dispose()
        baseline = _allocated_bytes(data_dir)
        peak = baseline
        stop = Event()

        def sample_disk_use():
            nonlocal peak
            while not stop.wait(0.01):
                peak = max(peak, _allocated_bytes(data_dir))

        sampler = Thread(target=sample_disk_use)
        sampler.start()
        try:
            started = perf_counter()
            restored = local_data_provider.restore_local_database(candidate)
            restore_time = perf_counter() - started
        finally:
            stop.set()
            sampler.join()
        assert restored.get_setting("restore-stress") == "backup version"
        with closing(sqlite3.connect(db_path)) as connection:
            assert connection.execute("SELECT count(*) FROM stress_payload").fetchone() == (1024,)
            assert connection.execute(
                "SELECT body FROM stress_payload WHERE rowid = 1024"
            ).fetchone() == (payload,)
        restored.set_setting("restore-stress", "original before interruption")
        restored.engine.dispose()
        peak = max(peak, _allocated_bytes(data_dir))

        child_setup = """
import os
import sys
from pathlib import Path
from review_app.backend.db import backup
from review_app.backend.provider import local_data_provider as provider

data_dir = Path(sys.argv[1])
backup.get_user_data_dir = provider.get_user_data_dir = lambda: data_dir
"""
        interrupt = """
def interrupt_after_reopen():
    reopened = provider.LocalDataProvider()
    assert reopened.get_setting('restore-stress') == 'backup version'
    os._exit(73)

backup.restore_backup(Path(sys.argv[2]), len(provider.MIGRATIONS), reopen=interrupt_after_reopen)
"""
        child = subprocess.run(
            [sys.executable, "-c", child_setup + interrupt, str(data_dir), str(candidate)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert child.returncode == 73, child.stderr
        rollback = db_path.with_suffix(".db.rollback")
        assert rollback.stat().st_size >= GIB

        recover = """
import sqlite3
from contextlib import closing

backup.recover_interrupted_restore()
with closing(sqlite3.connect(data_dir / provider.DEFAULT_DB_FILENAME)) as connection:
    assert connection.execute('PRAGMA quick_check').fetchall() == [('ok',)]
    assert not connection.execute('PRAGMA foreign_key_check').fetchall()
    assert connection.execute('SELECT count(*) FROM stress_payload').fetchone() == (1024,)
recovered = provider.LocalDataProvider()
assert recovered.get_setting('restore-stress') == 'original before interruption'
recovered.engine.dispose()
"""
        started = perf_counter()
        child = subprocess.run(
            [sys.executable, "-c", child_setup + recover, str(data_dir)],
            capture_output=True,
            text=True,
            timeout=90,
        )
        recovery_time = perf_counter() - started
        assert child.returncode == 0, child.stderr
        assert not rollback.exists()
        with capsys.disabled():
            print(
                f"\n{candidate.stat().st_size / GIB:.2f} GiB gzip backup: created in {backup_time:.2f}s, "
                f"restored in {restore_time:.2f}s, restart and integrity check in {recovery_time:.2f}s; "
                f"sampled disk peak {peak / GIB:.2f} GiB ({(peak - baseline) / GIB:.2f} GiB above baseline)"
            )
