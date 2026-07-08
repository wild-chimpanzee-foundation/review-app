import json
import logging
import re
from datetime import datetime, timedelta, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen

from packaging.version import InvalidVersion, Version

from review_app import __version__
from review_app.app.config import get_user_data_dir

GITHUB_REPO = "wild-chimpanzee-foundation/review-app"
_CACHE_TTL = timedelta(hours=1)
logger = logging.getLogger(__name__)


def _normalize(v: str) -> str:
    """Convert semver tag to PEP 440: 'v1.0.0-beta.1' → '1.0.0b1', 'v1.0.0' → '1.0.0'."""
    v = v.lstrip("v")
    v = re.sub(r"-beta\.?(\d+)", r"b\1", v)
    v = re.sub(r"-alpha\.?(\d+)", r"a\1", v)
    v = re.sub(r"-rc\.?(\d+)", r"rc\1", v)
    return v


def _is_newer(tag: str) -> bool:
    try:
        return Version(_normalize(tag)) > Version(_normalize(__version__))
    except InvalidVersion:
        return False


def _cache_path():
    return get_user_data_dir() / "update_cache.json"


def _load_cache() -> dict | None:
    try:
        data = json.loads(_cache_path().read_text())
        if datetime.now(timezone.utc) - datetime.fromisoformat(data["cached_at"]) < _CACHE_TTL:
            return data
    except Exception:
        pass
    return None


def _write_cache(tag: str | None) -> None:
    try:
        _cache_path().write_text(
            json.dumps({"tag": tag, "cached_at": datetime.now(timezone.utc).isoformat()})
        )
    except Exception:
        pass


def _fetch_latest_tag() -> str | None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    logger.info("Checking for updates from %s", url)
    try:
        req = Request(url, headers={"Accept": "application/vnd.github+json"})
        with urlopen(req, timeout=5) as resp:
            tag = json.loads(resp.read()).get("tag_name")
            logger.info("Latest release tag: %s", tag)
            return tag
    except (URLError, Exception):
        logger.warning("Update check failed", exc_info=True)
        return None


def check_for_update() -> tuple[str, str] | None:
    """Return (tag, release_url) if a newer release exists, else None. Blocking — run in executor."""
    logger.info("Current version: %s", __version__)
    cached = _load_cache()
    if cached is not None:
        logger.debug("Using cached update info (tag=%s)", cached.get("tag"))
    tag = cached["tag"] if cached is not None else _fetch_latest_tag()
    if cached is None:
        _write_cache(tag)
    if tag and _is_newer(tag):
        logger.info("Update available: %s → %s", __version__, tag)
        return tag, f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"
    logger.info("No update available (latest=%s, current=%s)", tag, __version__)
    return None


def force_check_for_update() -> tuple[tuple[str, str] | None, str | None]:
    """Like check_for_update but always bypasses the cache.

    Returns (update_result, latest_tag) where update_result is (tag, url) if newer, else None.
    """
    try:
        _cache_path().unlink(missing_ok=True)
    except Exception:
        pass
    tag = _fetch_latest_tag()
    _write_cache(tag)
    update = None
    if tag and _is_newer(tag):
        logger.info("Update available: %s → %s", __version__, tag)
        update = tag, f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"
    else:
        logger.info("No update available (latest=%s, current=%s)", tag, __version__)
    return update, tag
