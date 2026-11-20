"""One networked run: lock, pause gate, pacing, connect, login, logout.

Wraps the IMAP session of every networked command and maps IMAP and
network failures to the exit-code contract: a rejected login is
permanent (exit 1) with a human explanation, a server answer that means
"you are being limited" is retryable (exit 2) and its pause is persisted,
a network failure is retryable without a persisted pause.
"""

import ast
import logging
import re
import ssl
from contextlib import contextmanager

from imapclient import IMAPClient
from imapclient.exceptions import IMAPClientAbortError, IMAPClientError, LoginError

from . import config, throttle
from .errors import PermanentError
from .mailbox import ReadOnlyMailbox

# Socket timeout for connecting and for every server answer, in seconds.
TIMEOUT = 60

# Pauses, in seconds, for server answers that mean "you are being
# limited". Yandex does not say for how long; these conservative guesses
# apply unless the answer names a duration itself.
CONNECTION_LIMIT_PAUSE = 120
LOGIN_BLOCK_PAUSE = 900
SERVER_LIMIT_PAUSE = 300

# imaplib (under imapclient) logs every command at DEBUG level, LOGIN with
# the password included. Pin these loggers above DEBUG so no logging
# configuration can ever write the password out.
for _logger_name in ("imapclient", "imapclient.imaplib"):
    logging.getLogger(_logger_name).setLevel(logging.WARNING)

_LIMIT_RULES = [
    (
        re.compile(
            r"too many (simultaneous |concurrent |open |parallel )?"
            r"(connections|sessions)|connection limit|maximum number of "
            r"(connections|sessions)|слишком много (одновременных )?(подключений|соединений|сессий)",
            re.IGNORECASE,
        ),
        CONNECTION_LIMIT_PAUSE,
        "too many connections",
    ),
    (
        re.compile(
            r"blocked|locked|frozen|too many (login|auth\w*|failed|attempts)"
            r"|заблокирован|слишком много попыток",
            re.IGNORECASE,
        ),
        LOGIN_BLOCK_PAUSE,
        "login temporarily blocked",
    ),
    (
        re.compile(
            r"\[(LIMIT|UNAVAILABLE|INUSE|THROTTLED|OVERQUOTA)\]|rate.?limit|too many"
            r"|limit exceeded|temporar|try (again )?later|слишком много|временно"
            r"|попробуйте позже",
            re.IGNORECASE,
        ),
        SERVER_LIMIT_PAUSE,
        "the server is limiting requests",
    ),
]

_DURATION = re.compile(
    r"(\d+)\s*(seconds?|secs?|minutes?|mins?|hours?|сек\w*|мин\w*|час\w*)",
    re.IGNORECASE,
)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "с": 1, "м": 60, "ч": 3600}

# imaplib reports server answers as bytes reprs: b'[CODE] text'.
_BYTES_REPR = re.compile(r"""b(['"])((?:\\.|(?!\1).)*)\1""")


class AuthError(PermanentError):
    """Yandex rejected the login and password (exit code 1)."""


def server_text(error: BaseException) -> str:
    """The server's answer carried by an IMAP exception, as readable text."""

    def decode(match: re.Match) -> str:
        try:
            return ast.literal_eval(match.group(0)).decode("utf-8", "replace")
        except (ValueError, SyntaxError):
            return match.group(0)

    return _BYTES_REPR.sub(decode, str(error)).strip()


def limit_pause(text: str) -> tuple[int, str] | None:
    """(pause seconds, reason) when a server answer means "slow down"."""
    for pattern, default_pause, reason in _LIMIT_RULES:
        if pattern.search(text):
            return _named_duration(text) or default_pause, reason
    return None


def _named_duration(text: str) -> int | None:
    match = _DURATION.search(text)
    if match is None:
        return None
    seconds = int(match.group(1)) * _UNIT_SECONDS[match.group(2)[0].lower()]
    return seconds if 0 < seconds <= throttle.MAX_PAUSE else None


def _limited_or_transient(text: str) -> throttle.RetryLaterError:
    """Retry contract for a broken connection or a refusing server answer."""
    pause = limit_pause(text)
    if pause is None:
        return throttle.RetryLaterError(
            f"the connection to {config.IMAP_HOST} broke ({text})",
            throttle.NETWORK_RETRY_HINT,
        )
    seconds, reason = pause
    throttle.record_pause(seconds, f"{reason}: {text}")
    return throttle.RetryLaterError(f"Yandex refused: {reason} ({text})", seconds)


def _unreachable(error: OSError) -> throttle.RetryLaterError:
    return throttle.RetryLaterError(
        f"cannot reach {config.IMAP_HOST}:{config.IMAP_PORT} "
        f"({type(error).__name__}: {error})",
        throttle.NETWORK_RETRY_HINT,
    )


def _untrusted_certificate(error: ssl.SSLCertVerificationError) -> PermanentError:
    return PermanentError(
        f"The TLS certificate of {config.IMAP_HOST} did not verify ({error}); "
        "the connection may be intercepted. Not retrying."
    )


def connect() -> IMAPClient:
    """Open the TLS connection; replaced by a fake client in tests."""
    return IMAPClient(
        config.IMAP_HOST, port=config.IMAP_PORT, ssl=True, timeout=TIMEOUT
    )


def _connect() -> IMAPClient:
    try:
        return connect()
    except ssl.SSLCertVerificationError as error:
        raise _untrusted_certificate(error) from error
    except IMAPClientError as error:
        # The greeting was a BYE (e.g. too many connections) or garbage.
        raise _limited_or_transient(server_text(error)) from error
    except OSError as error:
        raise _unreachable(error) from error


def _login(client: IMAPClient, credentials: config.Credentials) -> None:
    try:
        client.login(credentials.login, credentials.password)
    except LoginError as error:
        text = server_text(error)
        if limit_pause(text) is not None:
            raise _limited_or_transient(text) from error
        raise AuthError(
            f"Yandex rejected the login for {credentials.login} ({text}). Yandex "
            "gives this answer for several causes; ask the mailbox owner to "
            "check, in this order: (1) IMAP is enabled: Yandex Mail -> All "
            "settings (Все настройки) -> Email clients (Почтовые программы) -> "
            "'From the imap.yandex.ru server via IMAP', with app passwords "
            "allowed as the IMAP authorization method; (2) the password in "
            f"{config.credentials_path()} is an app password for Mail, created "
            "at id.yandex.ru -> Security (Безопасность) -> App passwords (Пароли "
            "приложений) -> Mail (Почта), not the account password; (3) that app "
            "password was not revoked and the login has no typo. Do not retry "
            "until the owner has fixed it: repeated failed logins make Yandex "
            "block logins for a while."
        ) from None


def _logout(client: IMAPClient) -> None:
    # Best-effort: a failing logout (likely the same dead connection that
    # broke the command) must not replace the error in flight.
    try:
        client.logout()
    except (IMAPClientError, OSError):
        pass


@contextmanager
def mailbox_session():
    """Yield a logged-in ReadOnlyMailbox under the inter-process lock.

    The whole session runs under the lock, so this tool holds at most one
    IMAP connection at a time, and a persisted server pause is honored
    before anything touches the network.
    """
    credentials = config.load_credentials()
    lock = throttle.acquire_lock()
    try:
        throttle.check_pause()
        throttle.pace()
        client = _connect()
        try:
            _login(client, credentials)
            yield ReadOnlyMailbox(client, credentials.login)
        except IMAPClientAbortError as error:
            # BYE or a dropped connection in the middle of the session.
            raise _limited_or_transient(server_text(error)) from error
        except IMAPClientError as error:
            text = server_text(error)
            if limit_pause(text) is not None:
                raise _limited_or_transient(text) from error
            raise PermanentError(
                f"The IMAP server refused a command: {text}"
            ) from error
        except ssl.SSLCertVerificationError as error:
            raise _untrusted_certificate(error) from error
        except OSError as error:
            # Commands wrap their own local file errors as permanent errors,
            # so an OSError reaching this point is a network failure.
            raise _unreachable(error) from error
        finally:
            _logout(client)
    finally:
        lock.release()
