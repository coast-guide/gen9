"""Starting deletions from the API: each is a Temporal workflow (`workflows/deletion.py`) that
keeps retrying until done. The request waits a few seconds for it: 204 when it finished, 202 when
it is still going (Langfuse unreachable, say), in which case it completes on its own."""

import uuid
from datetime import datetime, timedelta

from fastapi import Response, status
from fastapi.responses import JSONResponse
from temporalio.client import (
    Client,
    WorkflowHandle,
    WorkflowUpdateRPCTimeoutOrCancelledError,
)
from temporalio.common import (
    Priority,
    SearchAttributePair,
    TypedSearchAttributes,
    WorkflowIDConflictPolicy,
)

from .temporal import GEN9_KIND, GEN9_USER
from .workflows.deletion import (
    DeleteAccountInput,
    DeleteAccountWorkflow,
    DeleteThreadInput,
    DeleteThreadWorkflow,
)
from .workflows.names import (
    PRIORITY_CHAT,
    SYSTEM_QUEUE,
    delete_account_workflow_id,
    delete_thread_workflow_id,
)

THREAD_WAIT_S = 10
ACCOUNT_WAIT_S = 15


def _attributes(user_sub: str, kind: str) -> TypedSearchAttributes:
    return TypedSearchAttributes(
        [SearchAttributePair(GEN9_USER, user_sub), SearchAttributePair(GEN9_KIND, kind)]
    )


def _again_id(workflow_id: str) -> str:
    """A deletion that must run from the start. Under the usual id it would join a deletion
    still running, whose data steps are done and which only waits to erase late traces (for
    ten minutes): a restore in that window brought the data back, and joining left it there."""
    return f"{workflow_id}-again-{uuid.uuid4().hex[:12]}"


async def start_thread_deletion(
    temporal: Client,
    thread_id: uuid.UUID,
    created_at: datetime,
    user_sub: str,
    *,
    again: bool = False,
) -> WorkflowHandle:
    """Joins a deletion already running for this thread (a repeated request). `again`: a
    deletion of its own (_again_id), for data a restore brought back."""
    workflow_id = delete_thread_workflow_id(str(thread_id))
    return await temporal.start_workflow(
        DeleteThreadWorkflow.run,
        DeleteThreadInput(thread_id=str(thread_id), since=created_at.isoformat()),
        id=_again_id(workflow_id) if again else workflow_id,
        task_queue=SYSTEM_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        search_attributes=_attributes(user_sub, "delete-thread"),
        priority=Priority(priority_key=PRIORITY_CHAT, fairness_key=user_sub),
    )


async def start_account_deletion(
    temporal: Client,
    sub: str,
    since: datetime,
    keycloak: bool = True,
    *,
    again: bool = False,
) -> WorkflowHandle:
    """`keycloak`: whether the person is still in Keycloak, to be disabled and deleted there.
    `again`: a deletion of its own (_again_id), for data a restore brought back."""
    workflow_id = delete_account_workflow_id(sub)
    return await temporal.start_workflow(
        DeleteAccountWorkflow.run,
        DeleteAccountInput(sub=sub, since=since.isoformat(), keycloak=keycloak),
        id=_again_id(workflow_id) if again else workflow_id,
        task_queue=SYSTEM_QUEUE,
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        search_attributes=_attributes(sub, "delete-account"),
        priority=Priority(priority_key=PRIORITY_CHAT, fairness_key=sub),
    )


async def finished_or_accepted(handle: WorkflowHandle, wait_s: float) -> Response:
    """204 once the person's data is gone (the workflow's `deleted` Update returned; late trace
    erasures continue); 202 if that takes longer than `wait_s` (the deletion goes on)."""
    try:
        # The SDK turns a deadline (or a cancelled wait) into its own error, not TimeoutError
        await handle.execute_update("deleted", rpc_timeout=timedelta(seconds=wait_s))
    except WorkflowUpdateRPCTimeoutOrCancelledError:
        return JSONResponse(
            {"status": "deleting", "detail": "Deleting. This finishes on its own."},
            status_code=status.HTTP_202_ACCEPTED,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
