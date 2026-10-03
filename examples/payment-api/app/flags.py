"""Feature flags evaluated from NexOps.

A background thread polls NEXOPS_FLAGS_URL every few seconds. If NexOps is unreachable the last known values stay
in effect (flags default to off), so a NexOps outage never changes application behaviour.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request

URL = os.getenv("NEXOPS_FLAGS_URL")
TOKEN = os.getenv("NEXOPS_FLAGS_TOKEN")
INTERVAL = float(os.getenv("NEXOPS_FLAGS_POLL_SECONDS", "5"))
_flags: dict[str, dict] = {}
_lock = threading.Lock()


def enabled(key: str, unit: str = "") -> bool:
    """True when the flag is on for this unit (payment id, user id…) given the rollout percentage."""
    with _lock:
        flag = _flags.get(key)
    if not flag or not flag.get("enabled"):
        return False
    percent = int(flag.get("rollout_percent", 100))
    if percent >= 100:
        return True
    bucket = int(hashlib.sha256(f"{key}:{unit}".encode()).hexdigest(), 16) % 100
    return bucket < percent


def snapshot() -> dict[str, dict]:
    with _lock:
        return dict(_flags)


def _poll(log: logging.Logger) -> None:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    while True:
        try:
            with urllib.request.urlopen(urllib.request.Request(URL, headers=headers), timeout=3) as response:
                flags = json.loads(response.read())["flags"]
            with _lock:
                previous = dict(_flags)
                _flags.clear()
                _flags.update(flags)
            for key, value in flags.items():
                before = previous.get(key, {})
                if before.get("enabled") != value.get("enabled") or before.get("rollout_percent") != value.get("rollout_percent"):
                    state = f"on ({value.get('rollout_percent', 100)}%)" if value.get("enabled") else "off"
                    log.info(f"Feature flag {key} is now {state} (source: NexOps)")
        except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as error:
            log.debug(f"Flag refresh failed, keeping last known values: {error}")
        time.sleep(INTERVAL)


def start(log: logging.Logger) -> bool:
    if not URL:
        return False
    threading.Thread(target=_poll, args=(log,), name="flag-poller", daemon=True).start()
    return True
