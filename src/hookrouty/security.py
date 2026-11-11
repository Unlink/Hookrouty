import base64
import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError
from cryptography.fernet import Fernet

PASSWORDS = PasswordHasher()


def now():
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def future(seconds):
    return (
        (datetime.now(UTC) + timedelta(seconds=seconds))
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def hash_password(password):
    return PASSWORDS.hash(password)


def verify_password(password, digest):
    try:
        return PASSWORDS.verify(digest, password)
    except (VerificationError, ValueError):
        return False


def token_digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def new_token():
    return secrets.token_urlsafe(32)


class SecretBox:
    def __init__(self, key):
        raw = base64.urlsafe_b64decode(key)
        self.fernet = Fernet(
            base64.urlsafe_b64encode(hmac.digest(raw, b"hookrouty:encryption:v1", "sha256"))
        )
        self.session_key = hmac.digest(raw, b"hookrouty:sessions:v1", "sha256")
        self.fingerprint = hmac.digest(raw, b"hookrouty:key-check:v1", "sha256").hex()

    def encrypt(self, value):
        return self.fernet.encrypt(value.encode()).decode()

    def decrypt(self, value):
        return self.fernet.decrypt(value.encode()).decode()

    def session_digest(self, value):
        return hmac.digest(self.session_key, value.encode(), "sha256").hex()
