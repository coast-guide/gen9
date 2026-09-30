"""A service that doesn't answer, one line an attempt (transient.py; manual-e2e.md, P6-B6): a
transient failure leaves an Activity as a BENIGN ApplicationError of the same type, logged once
without its traceback; any other error is raised as it was."""

import dataclasses
import logging
from types import SimpleNamespace

import httpx
import openai
import psycopg
import pytest
from temporalio.common import RetryPolicy
from temporalio.exceptions import ApplicationError, ApplicationErrorCategory
from temporalio.testing import ActivityEnvironment

from gen9_agent.transient import QuietTransientFailures, is_transient

pytestmark = pytest.mark.asyncio

REQUEST = httpx.Request("POST", "http://gen9-langfuse:3000/api/public/traces")


def status(code: int) -> httpx.HTTPStatusError:
    return httpx.HTTPStatusError(
        "answered", request=REQUEST, response=httpx.Response(code, request=REQUEST)
    )


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("All connection attempts failed", request=REQUEST),
        httpx.ReadTimeout("timed out", request=REQUEST),
        status(502),
        status(503),
        status(429),
        openai.APIConnectionError(request=REQUEST),
        psycopg.OperationalError("connection is lost"),
    ],
)
async def test_a_service_not_answering_is_transient(error: BaseException) -> None:
    assert is_transient(error)


@pytest.mark.parametrize(
    "error",
    [status(400), status(401), status(404), ValueError("not an id"), KeyError("x")],
)
async def test_a_wrong_request_or_a_bug_is_not(error: BaseException) -> None:
    assert not is_transient(error)


class Failing:
    """The next interceptor: the Activity raising `error`."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def execute_activity(self, input: object) -> object:
        raise self.error


async def run(error: BaseException, attempt: int = 1) -> BaseException:
    interceptor = QuietTransientFailures().intercept_activity(Failing(error))  # ty: ignore[invalid-argument-type]
    env = ActivityEnvironment()
    env.info = dataclasses.replace(
        env.info, attempt=attempt, retry_policy=RetryPolicy(maximum_attempts=3)
    )
    with pytest.raises(BaseException) as raised:
        await env.run(
            interceptor.execute_activity,
            SimpleNamespace(),  # ty: ignore[invalid-argument-type]
        )
    return raised.value


async def test_a_transient_failure_is_one_line_and_benign(caplog) -> None:
    cause = httpx.ConnectError("All connection attempts failed", request=REQUEST)
    with caplog.at_level(logging.DEBUG, logger="gen9_agent.transient"):
        error = await run(cause)
    assert isinstance(error, ApplicationError)
    assert error.category == ApplicationErrorCategory.BENIGN
    assert error.type == "ConnectError" and not error.non_retryable
    assert error.__cause__ is cause
    [record] = caplog.records
    assert record.levelno == logging.WARNING and record.exc_info is None
    assert record.getMessage() == (
        "unknown: ConnectError: All connection attempts failed; attempt 1, to be retried"
    )


async def test_the_last_attempt_says_no_retry_is_left(caplog) -> None:
    cause = httpx.ConnectError("All connection attempts failed", request=REQUEST)
    with caplog.at_level(logging.DEBUG, logger="gen9_agent.transient"):
        error = await run(cause, attempt=3)
    assert isinstance(error, ApplicationError) and not error.non_retryable
    [record] = caplog.records
    assert record.getMessage() == (
        "unknown: ConnectError: All connection attempts failed; attempt 3 of 3, no retry left"
    )


async def test_anything_else_is_raised_as_it_was(caplog) -> None:
    cause = ValueError("not an id: 'x'")
    with caplog.at_level(logging.DEBUG, logger="gen9_agent.transient"):
        error = await run(cause)
    assert error is cause
    assert not caplog.records
