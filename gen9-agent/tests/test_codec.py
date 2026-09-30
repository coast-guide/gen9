"""Payload encryption: what Temporal stores is ciphertext, keys rotate, and payloads written
before encryption (recorded histories, workflows in flight) still decode and replay."""

from pathlib import Path

import pytest
from temporalio.api.common.v1 import Payload
from temporalio.api.failure.v1 import Failure
from temporalio.client import WorkflowHistory
from temporalio.converter import default
from temporalio.worker import Replayer

from gen9_agent.codec import EncryptionCodec, data_converter, new_key, parse_keys
from gen9_agent.temporal import WORKFLOW_RUNNER
from gen9_agent.workflows.registry import ALL_WORKFLOWS
from gen9_agent.workflows.runs import RunInput

pytestmark = pytest.mark.asyncio

HISTORIES = {
    path.stem: path.read_text()
    for path in sorted(Path(__file__).with_name("histories").glob("*.json"))
}


def payload(value) -> Payload:
    return default().payload_converter.to_payloads([value])[0]


async def test_round_trip_and_ciphertext_only():
    codec = EncryptionCodec(parse_keys(new_key("k1")))
    plain = payload(RunInput(run_id="r1", user_sub="sub-1"))
    [encrypted] = await codec.encode([plain])
    assert encrypted.metadata["encoding"] == b"binary/encrypted"
    assert encrypted.metadata["encryption-key-id"] == b"k1"
    assert b"sub-1" not in encrypted.data and b"r1" not in encrypted.data
    assert await codec.decode([encrypted]) == [plain]


async def test_rotation_new_key_encrypts_old_key_still_decrypts():
    old, new = new_key("k1"), new_key("k2")
    [written_before] = await EncryptionCodec(parse_keys(old)).encode([payload("hello")])
    rotated = EncryptionCodec(parse_keys(f"{new},{old}"))
    assert await rotated.decode([written_before]) == [payload("hello")]
    [written_after] = await rotated.encode([payload("hello")])
    assert written_after.metadata["encryption-key-id"] == b"k2"


async def test_unknown_key_is_an_error_not_garbage():
    [encrypted] = await EncryptionCodec(parse_keys(new_key("k1"))).encode([payload(1)])
    with pytest.raises(ValueError, match="k1"):
        await EncryptionCodec(parse_keys(new_key("k9"))).decode([encrypted])


async def test_plain_payloads_pass_through():
    codec = EncryptionCodec(parse_keys(new_key("k1")))
    assert await codec.decode([payload("plain")]) == [payload("plain")]


@pytest.mark.parametrize("name", sorted(HISTORIES))
async def test_histories_from_before_encryption_still_replay(name: str):
    history = WorkflowHistory.from_json(name, HISTORIES[name])
    replayer = Replayer(
        workflows=ALL_WORKFLOWS,
        workflow_runner=WORKFLOW_RUNNER,
        data_converter=data_converter(parse_keys(new_key("k1"))),
    )
    await replayer.replay_workflow(history)


async def test_keys_must_be_32_bytes():
    with pytest.raises(ValueError):
        parse_keys("k1:c2hvcnQ=")


async def test_a_failure_that_is_its_own_cause_keeps_its_message():
    """OpenSandbox's SDK raises its errors `from` themselves: the failure still says what happened
    (Temporal's own converter fails with RecursionError, temporalio/sdk-python#697)."""
    converter = data_converter(parse_keys(new_key("k1")))
    try:
        try:
            raise ConnectionError("Server disconnected without sending a response")
        except ConnectionError as e:
            raise e from e
    except ConnectionError as e:
        looped = e
    assert looped.__cause__ is looped
    failure = Failure()
    converter.failure_converter.to_failure(looped, converter.payload_converter, failure)
    error = converter.failure_converter.from_failure(
        failure, converter.payload_converter
    )
    assert "Server disconnected" in str(error)
    assert error.__cause__ is None

    # A longer loop ends where it comes back, keeping every exception before it
    outer, inner = RuntimeError("outer"), ValueError("inner")
    outer.__cause__, inner.__cause__ = inner, outer
    failure = Failure()
    converter.failure_converter.to_failure(outer, converter.payload_converter, failure)
    error = converter.failure_converter.from_failure(
        failure, converter.payload_converter
    )
    assert "outer" in str(error) and "inner" in str(error.__cause__)
    assert error.__cause__.__cause__ is None
