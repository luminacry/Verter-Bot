"""Password hashing and secret generation built on the standard library."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

_SCHEME = "scrypt"
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Hash a password with scrypt and a random salt.

    Returns:
        ``scrypt$N$r$p$salt$hash`` with base64-encoded salt and hash.
    """
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN)
    return "$".join((_SCHEME, str(_N), str(_R), str(_P), _b64(salt), _b64(digest)))


def verify_password(password: str, encoded: str) -> bool:
    """Check a password against a hash produced by :func:`hash_password`.

    Malformed hashes never match; they do not raise.
    """
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != _SCHEME:
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt = base64.b64decode(parts[4], validate=True)
        expected = base64.b64decode(parts[5], validate=True)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


def generate_password(nbytes: int = 12) -> str:
    """Generate a URL-safe random password (16 characters for the default 12 bytes)."""
    return secrets.token_urlsafe(nbytes)


def generate_secret(nbytes: int = 32) -> str:
    """Generate a URL-safe random secret suitable for signing session tokens."""
    return secrets.token_urlsafe(nbytes)


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")
