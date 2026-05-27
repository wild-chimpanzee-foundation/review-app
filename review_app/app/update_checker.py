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
_CACHE_TTL = timedelta(hours=24)
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
    try:
        req = Request(url, headers={"Accept": "application/vnd.github+json"})
        with urlopen(req, timeout=5) as resp:
            return json.loads(resp.read()).get("tag_name")
    except (URLError, Exception):
        logger.debug("Update check failed", exc_info=True)
        return None


def check_for_update() -> tuple[str, str] | None:
    """Return (tag, release_url) if a newer release exists, else None. Blocking — run in executor."""
    cached = _load_cache()
    tag = cached["tag"] if cached is not None else _fetch_latest_tag()
    if cached is None:
        _write_cache(tag)
    if tag and _is_newer(tag):
        return tag, f"https://github.com/{GITHUB_REPO}/releases/tag/{tag}"
    return None
