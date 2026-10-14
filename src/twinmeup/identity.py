"""Who the principal is, and proof that the principal is the one at the keyboard.

Human-only operations (confirming lessons, granting authority, designing evaluations,
erasing records) need the principal's passphrase, typed at a terminal prompt or piped in
deliberately. Agents never receive it, so an agent with shell access still cannot ratify
its own learning or widen its own authority through the command line.

The passphrase is stored as a salted scrypt hash in an owner-only settings file. It is
never read from an environment variable: an agent started from the same shell would
inherit it.
"""

from __future__ import annotations

import getpass
import hashlib
import hmac
import json
import os
import re
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from pydantic import Field

from .errors import AuthorityError
from .models import ActorId, Model

SETTINGS_FILE = "settings.json"
MIN_PASSPHRASE = 8
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


class PassphraseHash(Model):
    algorithm: str = "scrypt"
    salt: str
    digest: str
    n: int = _SCRYPT["n"]
    r: int = _SCRYPT["r"]
    p: int = _SCRYPT["p"]


class Settings(Model):
    principal: str = Field(min_length=1, max_length=80)
    human_id: ActorId
    passphrase: PassphraseHash | None = None


def _derive(secret: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        secret.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=64 * 1024 * 1024, dklen=32
    )


def hash_passphrase(secret: str) -> PassphraseHash:
    if len(secret) < MIN_PASSPHRASE:
        raise AuthorityError(f"choose a passphrase of at least {MIN_PASSPHRASE} characters")
    salt = os.urandom(16)
    digest = _derive(secret, salt, **_SCRYPT)
    return PassphraseHash(salt=salt.hex(), digest=digest.hex())


def check_passphrase(secret: str, stored: PassphraseHash) -> bool:
    candidate = _derive(secret, bytes.fromhex(stored.salt), stored.n, stored.r, stored.p)
    return hmac.compare_digest(candidate, bytes.fromhex(stored.digest))


def human_id_for(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60] or "principal"
    if not slug[0].isalnum():
        slug = "p" + slug
    return f"human:{slug}"


def settings_path(home: Path) -> Path:
    return home / SETTINGS_FILE


def load_settings(home: Path) -> Settings | None:
    path = settings_path(home)
    if not path.exists():
        return None
    return Settings.model_validate_json(path.read_text(encoding="utf-8"))


def save_settings(home: Path, settings: Settings) -> Path:
    """Atomic, owner-only write."""
    home.mkdir(parents=True, exist_ok=True)
    os.chmod(home, 0o700)
    path = settings_path(home)
    fd, tmp = tempfile.mkstemp(dir=home, prefix=f".{SETTINGS_FILE}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(settings.model_dump(mode="json"), indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def read_secret(prompt: str, *, from_stdin: bool) -> str:
    """Read a secret from stdin (deliberate piping) or from the controlling terminal."""
    if from_stdin:
        line = sys.stdin.readline()
        if not line:
            raise AuthorityError("expected the passphrase on stdin")
        return line.rstrip("\r\n")
    if not has_terminal():
        raise AuthorityError(
            "this is a human-only operation: run it yourself in a terminal so you can enter "
            "your passphrase (agents are not given it)"
        )
    return getpass.getpass(prompt)


def has_terminal() -> bool:
    if os.name == "nt":  # getpass reads the Windows console directly
        return sys.stdin.isatty()
    try:
        with open("/dev/tty"):
            return True
    except OSError:
        return False


def prove_human(
    settings: Settings,
    *,
    from_stdin: bool,
    reader: Callable[[str], str] | None = None,
    warn: Callable[[str], None] | None = None,
) -> str:
    """Return the principal's actor id once the passphrase checks out."""
    if settings.passphrase is None:
        if warn:
            warn(
                "warning: this twin has no passphrase, so any program running as you, including "
                "agents, could perform human-only operations. Set one: `twinmeup init --force`."
            )
        return settings.human_id
    secret = (
        reader(f"Passphrase for {settings.principal}: ")
        if reader
        else read_secret(f"Passphrase for {settings.principal}: ", from_stdin=from_stdin)
    )
    if not check_passphrase(secret, settings.passphrase):
        time.sleep(1.0)  # slow down guessing
        raise AuthorityError("passphrase did not match; nothing was changed")
    return settings.human_id
