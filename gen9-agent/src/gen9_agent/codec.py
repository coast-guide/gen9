"""Temporal payload encryption: every payload gen9-agent sends to Temporal (workflow and Activity
inputs, results, Update arguments, failure messages) is AES-256-GCM encrypted before it leaves
the process, so Temporal's database and web UI hold ciphertext (docs/temporal.md, "Security").

Adapted from Temporal's Python encryption sample (samples-python/encryption/codec.py). Keys come
from TEMPORAL_PAYLOAD_KEYS ("id:base64,id:base64", the first encrypts, all decrypt), so a key can
be rotated without breaking payloads written with the old one. Payloads without the encrypted
encoding pass through unchanged, so histories written before encryption still replay.
"""

import base64
import dataclasses
import os
import threading
from collections.abc import Iterable, Sequence

import temporalio.api.failure.v1
import temporalio.converter
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from temporalio.api.common.v1 import Payload

ENCODING = b"binary/encrypted"
NONCE_BYTES = 12


def parse_keys(
    value: str, name: str = "TEMPORAL_PAYLOAD_KEYS"
) -> list[tuple[str, bytes]]:
    """ "k2:<base64>,k1:<base64>" -> [(k2, key), (k1, key)]; each key is 32 bytes (AES-256). The
    first encrypts, all decrypt. `name` is the setting's, for the error."""
    keys = []
    for item in value.split(","):
        key_id, _, encoded = item.strip().partition(":")
        key = base64.b64decode(encoded)
        if not key_id or len(key) != 32:
            raise ValueError(f"{name}: each entry is id:<32 bytes in base64>")
        keys.append((key_id, key))
    if not keys:
        raise ValueError(f"{name} is empty")
    return keys


def new_key(key_id: str) -> str:
    """One TEMPORAL_PAYLOAD_KEYS entry with a fresh random key."""
    return f"{key_id}:{base64.b64encode(os.urandom(32)).decode()}"


class EncryptionCodec(temporalio.converter.PayloadCodec):
    def __init__(self, keys: Sequence[tuple[str, bytes]]) -> None:
        super().__init__()
        self.current_id = keys[0][0]
        self.ciphers = {key_id: AESGCM(key) for key_id, key in keys}

    async def encode(self, payloads: Iterable[Payload]) -> list[Payload]:
        cipher = self.ciphers[self.current_id]
        encoded = []
        for p in payloads:
            nonce = os.urandom(NONCE_BYTES)
            encoded.append(
                Payload(
                    metadata={
                        "encoding": ENCODING,
                        "encryption-key-id": self.current_id.encode(),
                    },
                    data=nonce + cipher.encrypt(nonce, p.SerializeToString(), None),
                )
            )
        return encoded

    async def decode(self, payloads: Iterable[Payload]) -> list[Payload]:
        decoded = []
        for p in payloads:
            if p.metadata.get("encoding", b"") != ENCODING:
                decoded.append(p)  # written before encryption, or not ours: as is
                continue
            key_id = p.metadata.get("encryption-key-id", b"").decode()
            cipher = self.ciphers.get(key_id)
            if cipher is None:
                raise ValueError(f"No key with id {key_id!r} in TEMPORAL_PAYLOAD_KEYS")
            plain = cipher.decrypt(p.data[:NONCE_BYTES], p.data[NONCE_BYTES:], None)
            decoded.append(Payload.FromString(plain))
        return decoded


class FailureConverter(
    temporalio.converter.DefaultFailureConverterWithEncodedAttributes
):
    """Failure messages and stack traces encrypted, and a chain of causes that loops cut where it
    loops. OpenSandbox's SDK raises `to_sandbox_exception(e) from e`, which is `e` itself for its
    own errors, so each is its own cause; Temporal's converter follows causes without looking
    back and fails with RecursionError, losing the message (temporalio/sdk-python#697)."""

    # The exceptions being converted, per thread: workflows run in threads of their own
    _converting = threading.local()

    def to_failure(
        self,
        exception: BaseException,
        payload_converter: temporalio.converter.PayloadConverter,
        failure: temporalio.api.failure.v1.Failure,
    ) -> None:
        converting: set[int] = self._converting.__dict__.setdefault("ids", set())
        if id(exception) in converting:
            return  # a cause already in the chain: it ends here
        converting.add(id(exception))
        try:
            super().to_failure(exception, payload_converter, failure)
        finally:
            converting.discard(id(exception))


def data_converter(
    keys: Sequence[tuple[str, bytes]],
) -> temporalio.converter.DataConverter:
    """The default converter with encrypted payloads, failure messages and stack traces included
    (by default Temporal stores those in the clear)."""
    return dataclasses.replace(
        temporalio.converter.default(),
        payload_codec=EncryptionCodec(keys),
        failure_converter_class=FailureConverter,
    )
