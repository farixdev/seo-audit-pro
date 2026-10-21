# -*- coding: utf-8 -*-
"""
Cryptographic primitives: password hashing, access tokens, refresh tokens,
reset codes and encryption of stored credentials.
"""

import base64
import hashlib
import hmac
import json
import re
import secrets
import string
import time

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from backend import config

# ---------------------------------------------------------------------------
# Passwords (scrypt, memory-hard; stdlib implementation)
# ---------------------------------------------------------------------------
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2 ** 15, 8, 1


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
                            p=_SCRYPT_P, maxmem=128 * 1024 * 1024, dklen=32)
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode())


def verify_password(stored, password):
    """Constant-time verification. Also accepts the legacy PBKDF2
    ``salt:hexdigest`` format produced by the previous agency module."""
    if not stored or password is None:
        return False
    try:
        if stored.startswith("scrypt$"):
            _, n, r, p, salt_b64, digest_b64 = stored.split("$")
            salt = base64.b64decode(salt_b64)
            expected = base64.b64decode(digest_b64)
            test = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p),
                                  maxmem=128 * 1024 * 1024, dklen=len(expected))
            return hmac.compare_digest(expected, test)
        if ":" in stored:
            salt, hexdigest = stored.split(":", 1)
            test = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000).hex()
            return hmac.compare_digest(hexdigest, test)
    except (ValueError, TypeError):
        return False
    return False


def needs_rehash(stored):
    return not (stored or "").startswith("scrypt$%d$" % _SCRYPT_N)


# Dummy hash so failed logins for unknown emails take as long as real ones.
_DUMMY_HASH = None


def burn_password_check(password):
    global _DUMMY_HASH
    if _DUMMY_HASH is None:
        _DUMMY_HASH = hash_password(secrets.token_urlsafe(12))
    verify_password(_DUMMY_HASH, password or "")


_COMMON = {"password", "password1", "password123", "qwerty123", "letmein", "welcome1",
           "admin123", "changeme", "12345678", "123456789", "1234567890", "iloveyou"}


def password_policy_errors(password, email=""):
    """Returns a list of human-readable problems (empty == acceptable)."""
    problems = []
    pw = password or ""
    if len(pw) < 10:
        problems.append("Use at least 10 characters.")
    if len(pw) > 128:
        problems.append("Use at most 128 characters.")
    if not re.search(r"[a-z]", pw) or not re.search(r"[A-Z]", pw):
        problems.append("Mix upper- and lower-case letters.")
    if not re.search(r"\d", pw):
        problems.append("Include at least one number.")
    if pw.lower() in _COMMON:
        problems.append("This password is too common.")
    local = (email or "").split("@")[0].lower()
    if local and len(local) >= 4 and local in pw.lower():
        problems.append("Do not include your email name in the password.")
    return problems


def generate_temporary_password():
    """Readable temporary password that satisfies the policy."""
    alphabet = string.ascii_letters + string.digits
    while True:
        core = "".join(secrets.choice(alphabet) for _ in range(12))
        pw = f"{core[:4]}-{core[4:8]}-{core[8:]}"
        if not password_policy_errors(pw):
            return pw


# ---------------------------------------------------------------------------
# Access tokens (short-lived, HMAC-SHA256 signed)
# The token carries only identifiers. Role, team and active status are always
# reloaded from the database on each request.
# ---------------------------------------------------------------------------
def _b64(data):
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_access_token(user_id, session_id, ttl=None):
    payload = {"uid": user_id, "sid": session_id, "exp": int(time.time()) + (ttl or config.ACCESS_TOKEN_TTL),
               "typ": "access"}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(config.SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_access_token(token):
    """Returns the payload dict for a valid, unexpired token, else None."""
    if not token or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    expected = _b64(hmac.new(config.SECRET_KEY.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if payload.get("typ") != "access" or int(payload.get("exp", 0)) < time.time():
        return None
    return payload


def new_refresh_token():
    return secrets.token_urlsafe(48)


def sha256_hex(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_reset_code():
    """8-digit numeric code delivered by email; stored only as a hash."""
    return "".join(secrets.choice(string.digits) for _ in range(8))


def new_id(prefix=""):
    return f"{prefix}{secrets.token_hex(12)}"


# ---------------------------------------------------------------------------
# Encryption of stored credentials (Fernet / AES-128-CBC + HMAC)
# ---------------------------------------------------------------------------
def _fernet():
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"seo-audit-pro/credentials",
                info=b"smtp-password-v1")
    key = hkdf.derive(config.DATA_ENCRYPTION_KEY.encode("utf-8"))
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plain):
    return _fernet().encrypt(plain.encode("utf-8")).decode()


def decrypt_secret(token):
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None
