"""Bounded, file-change-aware metadata caching for Distance reference playback."""

from collections import OrderedDict
from pathlib import Path
from typing import Callable

from review_app.backend.provider.video import ProbeResult


class ReferenceProbeCache:
    def __init__(self, max_entries: int = 64):
        self.max_entries = max_entries
        self.entries: OrderedDict[Path, tuple[tuple[int, int], ProbeResult]] = OrderedDict()

    def probe(
        self, path: Path, probe_video: Callable[[Path], ProbeResult]
    ) -> tuple[ProbeResult, bool]:
        """Return metadata and whether it was cached; failures are always retried."""
        path = path.resolve()
        stat = path.stat()
        fingerprint = (stat.st_mtime_ns, stat.st_size)
        cached = self.entries.pop(path, None)
        if cached and cached[0] == fingerprint:
            self.entries[path] = cached
            return cached[1], True
        result = probe_video(path)
        # Do not retain metadata if probing failed or the file changed mid-probe.
        try:
            stat = path.stat()
        except OSError:
            return result, False
        if result.is_valid and fingerprint == (stat.st_mtime_ns, stat.st_size):
            self.entries[path] = (fingerprint, result)
            while len(self.entries) > self.max_entries:
                self.entries.popitem(last=False)
        return result, False
