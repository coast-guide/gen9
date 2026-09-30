"""A service that doesn't answer, logged as one line an attempt (docs/plans/manual-e2e.md, P6-B6).

An Activity whose call to another stack fails is retried until it answers (a deletion never gives
up, F22), and Temporal's SDK logged each failed attempt as a warning with its whole traceback: with
Langfuse stopped, one chat's deletion wrote 12 tracebacks in 3 minutes, for as long as the outage
lasted, while OWASP A10:2025 asks that repeated errors be shown "as statistics only".

A failure known to be transient (the service unreachable or timing out, answering 5xx or 429, or
the database not taking the request) is now one warning line naming the Activity, the error and the
attempt (or that it was the last), and the attempt fails as a BENIGN ApplicationError, which the
SDK logs at DEBUG and keeps out of its failure metrics: Temporal's advice for "expected transient
failures that will be retried" (docs.temporal.io, "Benign exceptions"). The error keeps the exception's class name as its
type, so retry policies and workflows see the same failure; how often attempts come is the retry
policy's, as before. Anything else is left as it was, traceback and all.
"""

import logging
from typing import Any

import httpx
import openai
from temporalio import activity
from temporalio.exceptions import ApplicationError, ApplicationErrorCategory
from temporalio.worker import (
    ActivityInboundInterceptor,
    ExecuteActivityInput,
    Interceptor,
)

from .db_unavailable import unavailable

log = logging.getLogger(__name__)


def is_transient(error: BaseException) -> bool:
    """A failure that retrying later can cure: nothing about the request was wrong."""
    if isinstance(error, httpx.TransportError):
        return True  # unreachable, a timeout, a dropped connection
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        return status >= 500 or status == 429
    if isinstance(error, (openai.APIConnectionError, openai.InternalServerError)):
        return True  # the router unreachable, or failing on its side
    return unavailable(error) is not None


class _QuietTransientFailures(ActivityInboundInterceptor):
    async def execute_activity(self, input: ExecuteActivityInput) -> Any:
        try:
            return await self.next.execute_activity(input)
        except Exception as error:
            if not is_transient(error):
                raise
            info = activity.info()
            said = f"{type(error).__name__}: {error}".splitlines()[0][:300]
            most = info.retry_policy.maximum_attempts if info.retry_policy else 0
            log.warning(
                "%s: %s; %s",
                info.activity_type,
                said,
                f"attempt {info.attempt} of {most}, no retry left"
                if most and info.attempt >= most
                else f"attempt {info.attempt}, to be retried",
            )
            raise ApplicationError(
                said,
                type=type(error).__name__,
                category=ApplicationErrorCategory.BENIGN,
            ) from error


class QuietTransientFailures(Interceptor):
    """The worker's interceptor: each Activity's transient failures, one line an attempt."""

    def intercept_activity(
        self, next: ActivityInboundInterceptor
    ) -> ActivityInboundInterceptor:
        return _QuietTransientFailures(next)
