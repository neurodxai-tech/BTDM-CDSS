"""
One password policy for the whole system (admin-created accounts, the
first-login password change, and one-time passwords generated on approval).

Chosen passwords:   PASSWORD_MIN_LENGTH..PASSWORD_MAX_LENGTH characters
                    (default 8..64), at least one letter and one digit, must
                    not contain the username, must not be a common password.
Generated passwords: GENERATED_PASSWORD_LENGTH characters (default 8, allowed
                    6..16) with upper case, lower case and a digit, from an
                    alphabet without look-alikes (no 0/O, 1/l/I). They are
                    single-use: the doctor must replace them at first login.

User IDs:           generated as rad-###, ref-###, tech-### (7–8 characters);
                    manually created IDs are 3–32 of a-z 0-9 . _ -
"""
from __future__ import annotations

import os
import re
import secrets

MIN_LENGTH = max(6, int(os.getenv("PASSWORD_MIN_LENGTH", "8")))
MAX_LENGTH = max(MIN_LENGTH, int(os.getenv("PASSWORD_MAX_LENGTH", "64")))
GENERATED_LENGTH = min(16, max(6, int(os.getenv("GENERATED_PASSWORD_LENGTH", "8"))))

_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_USERNAME_RE = re.compile(r"^[a-z0-9._-]{3,32}$")
_COMMON = {
    "password", "password1", "password12", "password123", "passw0rd", "12345678", "123456789",
    "1234567890", "qwerty123", "qwertyui", "abc12345", "abcd1234", "11111111", "iloveyou1",
    "admin123", "admin1234", "welcome1", "letmein1", "changeme", "changeme123", "doctor123",
    "hospital1", "nepal123", "kathmandu1",
}


def describe() -> dict:
    return {
        "min_length": MIN_LENGTH,
        "max_length": MAX_LENGTH,
        "requires_letter": True,
        "requires_digit": True,
        "generated_length": GENERATED_LENGTH,
    }


def validate(password: str, username: str | None = None) -> None:
    """Raises ValueError with one user-facing message listing every problem."""
    problems = []
    if len(password) < MIN_LENGTH:
        problems.append(f"at least {MIN_LENGTH} characters")
    if len(password) > MAX_LENGTH:
        problems.append(f"at most {MAX_LENGTH} characters")
    if not re.search(r"[A-Za-z]", password):
        problems.append("at least one letter")
    if not re.search(r"\d", password):
        problems.append("at least one number")
    if username and len(username) >= 3 and username.lower() in password.lower():
        problems.append("must not contain the user ID")
    if password.lower() in _COMMON:
        problems.append("must not be a common password")
    if problems:
        raise ValueError("Password needs: " + "; ".join(problems) + ".")


def validate_username(username: str) -> None:
    if not _USERNAME_RE.match(username):
        raise ValueError("User ID must be 3–32 characters: lowercase letters, digits, '.', '_' or '-'")


def generate() -> str:
    while True:
        pw = "".join(secrets.choice(_ALPHABET) for _ in range(GENERATED_LENGTH))
        if any(c.isdigit() for c in pw) and any(c.isupper() for c in pw) and any(c.islower() for c in pw):
            return pw
