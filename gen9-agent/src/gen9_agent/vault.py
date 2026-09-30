"""Secrets people give Gen9, such as a connector's token, sealed before they reach Postgres.

AES-256-GCM under a key from a key ring (`GEN9_SECRET_KEYS`, "id:<32 bytes in base64>" pairs; the
first seals, all open), the same shape as Temporal's payload keys (codec.py). Each secret is bound
to what it belongs to (its owner and connector) as associated data, so a sealed value copied onto
another person's row doesn't open. This follows how n8n keeps credentials; a vault such as
OpenBao can take this interface over later (docs/plans/harness.md, Decision Log).

To rotate: put a new key first and keep the old ones until nothing sealed with them is left.
"""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .codec import parse_keys

NAME = "GEN9_SECRET_KEYS"


class Vault:
    def __init__(self, keys: str) -> None:
        parsed = parse_keys(keys, NAME)
        self.current_id = parsed[0][0]
        self.ciphers = {key_id: AESGCM(key) for key_id, key in parsed}

    def seal(self, secret: str, *belongs_to: str) -> str:
        """`secret`, sealed with the current key and bound to `belongs_to`: "<key id>:<base64>"."""
        nonce = os.urandom(12)
        sealed = self.ciphers[self.current_id].encrypt(
            nonce, secret.encode(), _context(belongs_to)
        )
        return f"{self.current_id}:{base64.b64encode(nonce + sealed).decode()}"

    def open(self, sealed: str, *belongs_to: str) -> str:
        """The secret back; ValueError if its key is gone, or it was sealed for something else."""
        key_id, _, body = sealed.partition(":")
        cipher = self.ciphers.get(key_id)
        if cipher is None:
            raise ValueError(f"sealed with key {key_id!r}, which {NAME} no longer has")
        raw = base64.b64decode(body)
        try:
            return cipher.decrypt(raw[:12], raw[12:], _context(belongs_to)).decode()
        except InvalidTag as e:  # tampered with, or bound to something else
            raise ValueError("this secret doesn't open here") from e


def _context(belongs_to: tuple[str, ...]) -> bytes:
    return "\x00".join(belongs_to).encode()
