"""Server-friendly pacing: inter-process lock, persisted pauses, run spacing.

Yandex answers clients that connect too often or too many at once with
"too many connections" or a temporary login block. Retrying through such
an answer only extends it, so the pause is persisted to disk and every
later run refuses to touch the network until it expires. A single
inter-process lock keeps this tool to one IMAP connection at a time, and
a minimum interval between runs smooths out bursts from a misbehaving
caller.
"""

import json
import math
import time
from datetime import UTC, datetime

from filelock import FileLock, Timeout

from . import config

STATE_FILENAME = "throttle.json"
LOCK_FILENAME = "yandex_mail.lock"

# How long a run waits for another yandex-mail process before giving up.
LOCK_TIMEOUT = 30
# Minimum interval between consecutive networked runs, in seconds.
MIN_INTERVAL = 2.0
# Retry hint, in seconds, reported when the server cannot be reached over
# the network (a transient condition: exit code 2, not persisted).
NETWORK_RETRY_HINT = 30
# Longest pause the tool ever persists (one day). A persisted deadline
# further in the future than this is impossible: the system clock jumped
# backwards or the state file is damaged.
MAX_PAUSE = 86400
# Diagnostic record of the last persisted pause: why and when. Written for
# a human reading the state file; the tool never acts on it.
LAST_PAUSE_KEY = "last_pause"


class RetryLaterError(Exception):
    """Raised when the request cannot run now but may succeed later.

    The CLI maps this to exit code 2 so callers can tell "wait and retry"
    apart from permanent errors.
    """

    def __init__(self, message: str, retry_after: float):
        self.retry_after = retry_after
        super().__init__(f"{message}; retry after {math.ceil(retry_after)}s")


def _state_path():
    return config.config_dir() / STATE_FILENAME


def _load_state() -> dict:
    path = _state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # A missing or damaged state file must not brick the tool; losing
        # throttle state is harmless.
        return {}
    if not isinstance(data, dict):
        return {}
    # Guard against JSON-valid damage (e.g. a hand-edited file): a
    # non-numeric value would break the arithmetic below on every run.
    return {
        key: value
        for key, value in data.items()
        if (isinstance(value, (int, float)) and not isinstance(value, bool))
        or (key == LAST_PAUSE_KEY and isinstance(value, dict))
    }


def _save_state(state: dict) -> None:
    # Best-effort: losing throttle state is harmless, but failing to write
    # it must not turn a retryable failure being raised into a permanent one.
    try:
        config.ensure_config_dir()
        path = _state_path()
        temp_path = path.with_name(path.name + ".tmp")
        temp_path.write_text(json.dumps(state) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except OSError:
        pass


def acquire_lock() -> FileLock:
    """Take the global inter-process lock; the caller must release it."""
    config.ensure_config_dir()
    lock = FileLock(str(config.config_dir() / LOCK_FILENAME))
    try:
        lock.acquire(timeout=LOCK_TIMEOUT)
    except Timeout:
        raise RetryLaterError(
            "another yandex-mail process is using the mailbox", LOCK_TIMEOUT
        ) from None
    return lock


def check_pause() -> None:
    """Refuse to run while a persisted server-imposed pause is active."""
    state = _load_state()
    remaining = state.get("pause_until", 0) - time.time()
    # A remaining pause longer than the one recorded (or than any the tool
    # records) is impossible: the clock jumped backwards or the file is
    # damaged. Cap and rewrite it so a stale deadline cannot lock the tool
    # out for longer than the pause itself.
    longest = state.get("pause_seconds", MAX_PAUSE)
    if not 0 < longest <= MAX_PAUSE:
        longest = MAX_PAUSE
    if remaining > longest:
        remaining = longest
        state["pause_until"] = time.time() + remaining
        _save_state(state)
    if remaining > 0:
        raise RetryLaterError(
            "a pause requested by the mail server is active", remaining
        )


def pace() -> None:
    """Keep networked runs at least MIN_INTERVAL apart, then record this run.

    Must be called under the lock: it read-modify-writes the state file.
    """
    state = _load_state()
    wait = state.get("last_run_at", 0) + MIN_INTERVAL - time.time()
    if wait > 0:
        # A last_run_at in the future (backwards clock jump, damaged state
        # file) must not stall the run for longer than the interval itself.
        time.sleep(min(wait, MIN_INTERVAL))
    state["last_run_at"] = time.time()
    _save_state(state)


def record_pause(seconds: float, reason: str) -> None:
    """Persist a server-imposed pause so later runs refuse until it expires."""
    seconds = min(seconds, MAX_PAUSE)
    state = _load_state()
    now = time.time()
    state["pause_until"] = now + seconds
    state["pause_seconds"] = seconds
    state[LAST_PAUSE_KEY] = {
        "at": datetime.fromtimestamp(now, UTC).isoformat(timespec="seconds"),
        "seconds": seconds,
        "reason": reason,
    }
    _save_state(state)
