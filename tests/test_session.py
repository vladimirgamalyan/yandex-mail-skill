"""Login, server limits and network failures mapped to the exit-code contract."""

import json
import logging

import pytest
from conftest import FakeFolder, FakeIMAP
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError

from yandex_mail import session, throttle
from yandex_mail.cli import main
from yandex_mail.config import CredentialsError
from yandex_mail.session import AuthError, limit_pause, mailbox_session, server_text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[AUTHENTICATIONFAILED] LOGIN Invalid credentials or IMAP is disabled", None),
        ("LOGIN failure: please use app password", None),
        ("[UNAVAILABLE] Too many simultaneous connections", 120),
        ("Login temporarily blocked, try again later", 900),
        ("too many login attempts", 900),
        ("[LIMIT] rate limit exceeded", 300),
        ("[UNAVAILABLE] Internal error", 300),
        ("Account is blocked, try again in 10 minutes", 600),
        ("Слишком много подключений", 120),
        ("Вход временно заблокирован, попробуйте позже", 900),
    ],
)
def test_limit_pause_classification(text, expected):
    result = limit_pause(text)
    assert (result[0] if result else None) == expected


def test_server_text_unwraps_bytes_repr():
    error = IMAPClientError(
        "b'[AUTHENTICATIONFAILED] \\xd0\\x9e\\xd1\\x88\\xd0\\xb8\\xd0\\xb1\\xd0\\xba\\xd0\\xb0'"
    )
    assert server_text(error) == "[AUTHENTICATIONFAILED] Ошибка"


def test_imapclient_debug_logging_is_pinned_off():
    # imaplib logs LOGIN with the password at DEBUG level.
    assert (
        logging.getLogger("imapclient.imaplib").getEffectiveLevel() >= logging.WARNING
    )
    assert logging.getLogger("imapclient").getEffectiveLevel() >= logging.WARNING


def test_missing_credentials_file(config_dir):
    with pytest.raises(CredentialsError, match="credentials.json"), mailbox_session():
        pass


def test_credentials_with_bom_and_damage(config_dir):
    path = config_dir / "credentials.json"
    path.write_bytes(b'\xef\xbb\xbf{"login": "a@yandex.ru", "password": "p"}')
    from yandex_mail.config import load_credentials

    assert load_credentials().login == "a@yandex.ru"
    assert "password" not in repr(load_credentials())
    path.write_text('{"login": "a@yandex.ru", "password": "pass-123', "utf-8")
    with pytest.raises(CredentialsError) as excinfo:
        load_credentials()
    assert "pass-123" not in str(excinfo.value)


def test_rejected_login_is_permanent_and_explained(server, capsys):
    server.login_error = (
        "b'[AUTHENTICATIONFAILED] LOGIN Invalid credentials or IMAP is disabled'"
    )
    assert main(["check"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "IMAP is enabled" in captured.err and "app password" in captured.err
    assert "secret-app-pass" not in captured.err
    assert ("logout",) in server.calls


def test_connection_limit_pause_is_persisted(server, config_dir, capsys, monkeypatch):
    server.login_error = "b'[UNAVAILABLE] Too many simultaneous connections'"
    assert main(["folders"]) == 2
    assert "retry after 120s" in capsys.readouterr().err
    state = json.loads((config_dir / "throttle.json").read_text("utf-8"))
    assert (
        state["pause_seconds"] == 120
        and "too many connections" in state["last_pause"]["reason"]
    )

    def no_connect():
        raise AssertionError("must not connect during a pause")

    monkeypatch.setattr(session, "connect", no_connect)
    assert main(["folders"]) == 2
    assert "pause requested by the mail server" in capsys.readouterr().err


def test_network_failure_is_retryable_without_pause(
    credentials, config_dir, no_sleep, monkeypatch, capsys
):
    def refuse():
        raise ConnectionRefusedError(10061, "refused")

    monkeypatch.setattr(session, "connect", refuse)
    assert main(["check"]) == 2
    assert "cannot reach imap.yandex.ru:993" in capsys.readouterr().err
    assert not (
        config_dir / "throttle.json"
    ).exists() or "pause_until" not in json.loads(
        (config_dir / "throttle.json").read_text("utf-8")
    )


def test_bye_greeting_with_limit(
    credentials, config_dir, no_sleep, monkeypatch, capsys
):
    def bye():
        raise IMAPClientError("b'* BYE Too many connections'")

    monkeypatch.setattr(session, "connect", bye)
    assert main(["check"]) == 2
    assert "retry after 120s" in capsys.readouterr().err


def test_connection_dropped_mid_session(server, capsys, monkeypatch):
    def dropped(*args, **kwargs):
        raise IMAPClientAbortError("socket error: EOF")

    monkeypatch.setattr(FakeIMAP, "list_folders", dropped)
    assert main(["folders"]) == 2
    assert "connection to imap.yandex.ru broke" in capsys.readouterr().err


def test_lock_held_by_another_process(server, config_dir, capsys, monkeypatch):
    monkeypatch.setattr(throttle, "LOCK_TIMEOUT", 0.01)
    lock = throttle.acquire_lock()
    try:
        assert main(["check"]) == 2
    finally:
        lock.release()
    assert "another yandex-mail process" in capsys.readouterr().err


def test_check_output(server, capsys):
    server.folders["INBOX"] = FakeFolder()
    assert main(["check"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result == {
        "account": "owner@yandex.ru",
        "server": "imap.yandex.ru:993",
        "inbox": {"messages": 0, "unseen": 0},
        "folders": 5,
    }
    assert server.calls_of("login") == [("login", "owner@yandex.ru")]


def test_auth_error_is_permanent():
    assert issubclass(AuthError, session.PermanentError)
