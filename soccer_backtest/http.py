"""Polite HTTP client: retries, per-host throttling, on-disk caching.

Both sources are static files/HTML, so we cache raw responses on disk. Re-runs
are then free and don't re-hit the servers (football-data returns 429 when hit
too fast).
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from . import config

_last_request_at: dict[str, float] = {}


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": config.USER_AGENT})
    retry = Retry(
        total=4,
        backoff_factor=2.0,          # 0, 2, 4, 8s between retries
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
        respect_retry_after_header=True,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


SESSION = _session()


def _throttle(url: str) -> None:
    host = urlparse(url).netloc
    now = time.monotonic()
    last = _last_request_at.get(host)
    if last is not None:
        wait = config.REQUEST_MIN_INTERVAL - (now - last)
        if wait > 0:
            time.sleep(wait)
    _last_request_at[host] = time.monotonic()


def _cache_path(url: str, suffix: str) -> Path:
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    return config.RAW_DIR / f"{key}{suffix}"


def get_bytes(url: str, *, use_cache: bool = True, suffix: str = ".bin") -> bytes:
    """Fetch ``url`` as bytes, caching the raw response under data/raw/."""
    cache = _cache_path(url, suffix)
    if use_cache and cache.exists():
        return cache.read_bytes()

    _throttle(url)
    resp = SESSION.get(url, timeout=config.REQUEST_TIMEOUT)
    resp.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(resp.content)
    return resp.content


def get_text(url: str, *, use_cache: bool = True, suffix: str = ".html") -> str:
    return get_bytes(url, use_cache=use_cache, suffix=suffix).decode(
        "utf-8", errors="replace"
    )


def url_exists(url: str) -> bool:
    """Cheap existence probe via a ranged GET (many static hosts ignore HEAD)."""
    _throttle(url)
    try:
        resp = SESSION.get(
            url, timeout=config.REQUEST_TIMEOUT, headers={"Range": "bytes=0-0"}
        )
    except requests.RequestException:
        return False
    return resp.status_code in (200, 206)
