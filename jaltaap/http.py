"""Shared HTTP session: disk cache + retries, so we don't hammer free APIs."""
import os
import time

import requests
import requests_cache

CACHE_DIR = os.environ.get("JALTAAP_CACHE", os.path.join(os.path.dirname(__file__), "..", ".cache"))
os.makedirs(CACHE_DIR, exist_ok=True)

# history barely changes, forecasts change every few hours
_session = requests_cache.CachedSession(
    os.path.join(CACHE_DIR, "http"),
    expire_after=3 * 3600,
    allowable_codes=(200,),
)


def get_json(url: str, params: dict, tries: int = 4, long_cache: bool = False):
    """GET with retry/backoff. long_cache=True keeps the answer for 30 days (history calls)."""
    expire = 30 * 24 * 3600 if long_cache else None
    last = None
    for i in range(tries):
        try:
            r = _session.get(url, params=params, timeout=90, expire_after=expire) if expire \
                else _session.get(url, params=params, timeout=90)
            if r.status_code == 429:  # rate limited
                time.sleep(2 + 3 * i)
                continue
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as e:
            last = e
            time.sleep(1 + 2 * i)
    raise RuntimeError(f"request failed after {tries} tries: {url} ({last})")
