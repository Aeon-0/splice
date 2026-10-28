"""Argon2id password hashing for Splice auth."""
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

_hasher = PasswordHasher()


class Passwords:
    def hash(self, password: str) -> str:
        return _hasher.hash(password)

    def verify(self, encoded: str, password: str) -> bool:
        try:
            return _hasher.verify(encoded, password)
        except (VerifyMismatchError, InvalidHashError, TypeError, ValueError):
            return False
