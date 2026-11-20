"""Persisted pauses, run spacing and the retry-later error."""

import json
import time

import pytest

from yandex_mail import throttle
from yandex_mail.throttle import RetryLaterError


def write_state(config_dir, **state):
    (config_dir / throttle.STATE_FILENAME).write_text(json.dumps(state), "utf-8")


def test_retry_later_message():
    error = RetryLaterError("busy", 12.3)
    assert error.retry_after == 12.3
    assert str(error) == "busy; retry after 13s"


def test_no_state_no_pause(config_dir):
    throttle.check_pause()


def test_active_pause_refuses(config_dir):
    throttle.record_pause(100, "test")
    with pytest.raises(RetryLaterError) as excinfo:
        throttle.check_pause()
    assert 0 < excinfo.value.retry_after <= 100


def test_stale_deadline_capped_by_recorded_pause(config_dir):
    # A backwards clock jump must not stretch a 60s pause into hours.
    write_state(config_dir, pause_until=time.time() + 10000, pause_seconds=60)
    with pytest.raises(RetryLaterError) as excinfo:
        throttle.check_pause()
    assert excinfo.value.retry_after <= 60


def test_damaged_state_is_ignored(config_dir):
    (config_dir / throttle.STATE_FILENAME).write_text("{not json", "utf-8")
    throttle.check_pause()
    write_state(config_dir, pause_until="soon", last_run_at=[1])
    throttle.check_pause()
    throttle.pace()


def test_pace_sleeps_between_runs(config_dir, monkeypatch):
    slept = []
    monkeypatch.setattr(throttle.time, "sleep", slept.append)
    throttle.pace()
    throttle.pace()
    assert len(slept) == 1 and 0 < slept[0] <= throttle.MIN_INTERVAL
