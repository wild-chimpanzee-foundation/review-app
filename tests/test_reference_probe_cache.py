"""Reference metadata is reused only while the underlying media is unchanged."""

import os
from unittest.mock import Mock

import pytest
from review_app.app.pages.distance_review.reference_probe import ReferenceProbeCache
from review_app.backend.provider.video import ProbeResult


def valid_probe():
    return ProbeResult(10.0, True, True, None, None, None, None)


def test_reuses_metadata_for_the_same_file(tmp_path):
    path = tmp_path / "reference.mp4"
    path.touch()
    cache, result = ReferenceProbeCache(), valid_probe()
    probe = Mock(return_value=result)
    assert cache.probe(path, probe) == (result, False)
    assert cache.probe(path, probe) == (result, True)
    probe.assert_called_once_with(path.resolve())


@pytest.mark.parametrize("change", ["size", "mtime"])
def test_file_changes_invalidate_metadata(tmp_path, change):
    path = tmp_path / "reference.mp4"
    path.write_bytes(b"old")
    cache = ReferenceProbeCache()
    probe = Mock(side_effect=[valid_probe(), valid_probe()])
    cache.probe(path, probe)
    if change == "size":
        stat = path.stat()
        path.write_bytes(b"new-size")
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    else:
        stat = path.stat()
        os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    _, hit = cache.probe(path, probe)
    assert not hit and probe.call_count == 2
    assert cache.probe(path, probe)[1]


def test_failed_probes_are_retried(tmp_path):
    path = tmp_path / "reference.mp4"
    path.touch()
    cache = ReferenceProbeCache()
    failure = ProbeResult(None, False, False, "temporary failure", None, None, None)
    success = valid_probe()
    probe = Mock(side_effect=[failure, success])
    assert cache.probe(path, probe) == (failure, False)
    assert cache.probe(path, probe) == (success, False)
    assert cache.probe(path, probe) == (success, True)


def test_cache_is_bounded_and_evicts_the_least_recently_used_file(tmp_path):
    paths = [tmp_path / f"reference-{index}.mp4" for index in range(3)]
    for path in paths:
        path.touch()
    cache = ReferenceProbeCache(max_entries=2)
    probe = Mock(return_value=valid_probe())
    for path in paths[:2]:
        cache.probe(path, probe)
    assert cache.probe(paths[0], probe)[1]
    cache.probe(paths[2], probe)
    assert cache.probe(paths[0], probe)[1]
    assert not cache.probe(paths[1], probe)[1]
    assert len(cache.entries) == 2


def test_media_changed_during_probe_is_not_cached(tmp_path):
    path = tmp_path / "reference.mp4"
    path.touch()
    cache = ReferenceProbeCache()

    def probe(path):
        path.write_bytes(path.read_bytes() + b"changed")
        return valid_probe()

    assert not cache.probe(path, probe)[1]
    assert not cache.probe(path, probe)[1]
    assert not cache.entries
