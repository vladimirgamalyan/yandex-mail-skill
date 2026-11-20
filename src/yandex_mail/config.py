"""User configuration: config directory and the owner-created credentials file."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_config_dir

from .errors import PermanentError

APP_NAME = "yandex-mail"
CREDENTIALS_FILENAME = "credentials.json"

IMAP_HOST = "imap.yandex.ru"
IMAP_PORT = 993

CREDENTIALS_FORMAT = (
    'The mailbox owner creates it by hand as a JSON object: {"login": '
    '"name@yandex.ru", "password": "<app password>"}. The password is a Yandex '
    "app password for Mail (id.yandex.ru -> Security -> App passwords -> Mail), "
    "and IMAP must be enabled in Yandex Mail (All settings -> Email clients). "
    "An agent must not create this file or ask for the password: ask the "
    "owner. 'yandex-mail check --help' explains the setup."
)


class CredentialsError(PermanentError):
    """The credentials file is missing or unusable (exit code 1)."""


@dataclass(frozen=True)
class Credentials:
    login: str
    # Kept out of repr(): a stray debug print or traceback must not show it.
    password: str = field(repr=False)


def config_dir() -> Path:
    """Per-user configuration directory (independent of the working directory)."""
    return Path(user_config_dir(APP_NAME, appauthor=False, roaming=True))


def credentials_path() -> Path:
    return config_dir() / CREDENTIALS_FILENAME


def ensure_config_dir() -> Path:
    """Create the per-user config directory and make it private on POSIX."""
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(directory, 0o700)
    return directory


def load_credentials() -> Credentials:
    """Read the login and the app password from the credentials file.

    Error messages name the file and its format, never its content: the
    content holds the password.
    """
    path = credentials_path()
    try:
        # utf-8-sig: Windows Notepad may save UTF-8 with a byte order mark.
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise CredentialsError(
            f"No credentials file at {path}. {CREDENTIALS_FORMAT}"
        ) from None
    except (OSError, UnicodeDecodeError) as error:
        raise CredentialsError(
            f"Cannot read the credentials file {path} ({type(error).__name__}). "
            f"{CREDENTIALS_FORMAT}"
        ) from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as error:
        raise CredentialsError(
            f"The credentials file {path} is not valid JSON (line {error.lineno}, "
            f"column {error.colno}). {CREDENTIALS_FORMAT}"
        ) from None
    login = data.get("login") if isinstance(data, dict) else None
    password = data.get("password") if isinstance(data, dict) else None
    if not (isinstance(login, str) and login.strip()) or not (
        isinstance(password, str) and password.strip()
    ):
        raise CredentialsError(
            f"The credentials file {path} must hold non-empty string fields "
            f"'login' and 'password'. {CREDENTIALS_FORMAT}"
        )
    return Credentials(login.strip(), password.strip())
