"""Sealed secrets (vault.py): they open only with their key and for what they belong to."""

import base64
import os

import pytest

from gen9_agent.vault import Vault


def ring(*ids: str) -> tuple[str, dict[str, str]]:
    keys = {i: f"{i}:{base64.b64encode(os.urandom(32)).decode()}" for i in ids}
    return ",".join(keys.values()), keys


def test_a_secret_opens_for_what_it_belongs_to_only() -> None:
    keys, _ = ring("k1")
    vault = Vault(keys)
    sealed = vault.seal("token-123", "sub-a", "connector-1")
    assert sealed.startswith("k1:") and "token-123" not in sealed
    assert vault.open(sealed, "sub-a", "connector-1") == "token-123"
    with pytest.raises(ValueError, match="doesn't open here"):
        vault.open(sealed, "sub-b", "connector-1")  # copied onto another person's row


def test_rotation_seals_with_the_new_key_and_opens_the_old() -> None:
    _, keys = ring("k1", "k2")
    old = Vault(keys["k1"]).seal("s", "x")
    rotated = Vault(f"{keys['k2']},{keys['k1']}")
    assert rotated.seal("s", "x").startswith("k2:")
    assert rotated.open(old, "x") == "s"
    with pytest.raises(ValueError, match="no longer has"):
        Vault(keys["k2"]).open(old, "x")


def test_a_bad_key_ring_is_refused() -> None:
    with pytest.raises(ValueError, match="GEN9_SECRET_KEYS"):
        Vault("k1:dG9vIHNob3J0")
