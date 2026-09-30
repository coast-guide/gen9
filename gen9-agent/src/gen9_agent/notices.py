"""Notices about background runs (docs/plans/harness.md, milestone 5; principle 8: tell people
when something changes, once, and say whether it needs them).

- **Which runs.** Only background ones: a chat a scheduled task made (tasks.py). A chat someone
  is watching sends nothing.
- **When.** When the run is done, when it starts waiting for the person (an approval, a
  question), and when it didn't finish. A task with a rubric (outcomes.py) is done when its
  grading ends: it met the rubric, or it didn't in its tries. Once per run and kind
  (`run_notices`), however often an Activity is retried.
- **Whom.** The chat's person, at their email, as they chose in Settings (`users.notify`): all,
  only when it needs them, or never.
- **What.** The task's name, what happened, and a link to the chat. Never the answer: it stays
  in Gen9 (principle 9).
- **How.** SMTP (`SMTP_URL`, `aiosmtplib`). Without it, nothing is sent. A failed email is
  logged and leaves the run as it was; a retry of its Activity tries again.
"""

import asyncio
import logging
import uuid
from email.message import EmailMessage
from email.utils import formatdate, make_msgid, parseaddr
from urllib.parse import unquote, urlsplit

import aiosmtplib
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from .db_unavailable import unavailable
from .models import Run, RunNotice, Task, Thread, User
from .runtime import Runtime
from .settings import Settings

log = logging.getLogger(__name__)

SAID = {
    "done": "is done",
    "waiting": "needs you",
    "failed": "didn't finish",
    "unmet": "didn't meet its rubric",
}
WANTS = {
    "all": {"done", "waiting", "failed", "unmet"},
    "needs_you": {"waiting"},
    "never": set(),
}
SMTP_TIMEOUT_S = 15


async def send(settings: Settings, message: EmailMessage) -> None:
    """Sends through `SMTP_URL`: smtps:// with TLS from the start; smtp:// with STARTTLS when
    the server offers it."""
    assert settings.smtp_url is not None
    url = urlsplit(settings.smtp_url.get_secret_value())
    tls = url.scheme == "smtps"
    await aiosmtplib.send(
        message,
        hostname=url.hostname,
        port=url.port or (465 if tls else 25),
        username=unquote(url.username) if url.username else None,
        password=unquote(url.password) if url.password else None,
        use_tls=tls,
        start_tls=False if tls else None,
        timeout=SMTP_TIMEOUT_S,
    )


def message(
    settings: Settings, to: str, task: str, kind: str, thread_id: uuid.UUID
) -> EmailMessage:
    ui = settings.gen9_ui_url.rstrip("/")
    # On one line: a task's name may hold a line break, which a header can't (the policy refused
    # the whole email, so its person heard nothing)
    task = " ".join(task.split())
    email = EmailMessage()
    email["From"] = settings.smtp_from
    email["To"] = to
    email["Subject"] = f"{task} {SAID[kind]}"
    # RFC 5322 §3.6 requires the date; Message-ID, which mail services such as Gmail require too,
    # under the sender's domain (make_msgid would otherwise look up this host's name, blocking)
    email["Date"] = formatdate(usegmt=True)
    sender = parseaddr(settings.smtp_from)[1].rpartition("@")[2]
    email["Message-ID"] = make_msgid(domain=sender or urlsplit(ui).hostname)
    # Sent by a machine, so vacation replies and the like don't answer it (RFC 3834 §5)
    email["Auto-Submitted"] = "auto-generated"
    email.set_content(
        f"Gen9 ran your scheduled task “{task}”, and it {SAID[kind]}.\n\n"
        f"Open it: {ui}/chat/{thread_id}\n\n"
        f"You get these emails about your scheduled tasks. To change that: {ui}/settings\n"
    )
    return email


async def notify(
    runtime: Runtime, run_id: uuid.UUID, kind: str, graded: bool = False
) -> bool:
    """Emails the person about a background run's `kind` (done, waiting, failed, unmet), once,
    if they want it: whether it was sent now. A run of a task with a rubric is done only once
    `graded`."""
    settings = runtime.settings
    if settings.smtp_url is None:
        return False
    async with runtime.engine.connect() as conn:
        row = (
            await conn.execute(
                select(
                    Task.name, Task.rubric, Thread.id, User.email, User.notify, User.sub
                )
                .select_from(Run)
                .join(Thread, Thread.id == Run.thread_id)
                .join(Task, Task.id == Thread.task_id)
                .join(User, User.id == Thread.user_id)
                .where(Run.id == run_id)
            )
        ).first()
    if row is None or not row.email or kind not in WANTS.get(row.notify, set()):
        return False
    if kind == "done" and row.rubric and not graded:
        return False
    # No email to a person whose account is disabled or gone (standing.py)
    if not await runtime.standing.active(row.sub):
        return False
    async with runtime.engine.begin() as conn:
        await conn.execute(
            insert(RunNotice).values(run_id=run_id, kind=kind).on_conflict_do_nothing()
        )
        sent = await conn.scalar(
            select(RunNotice.sent_at).where(
                RunNotice.run_id == run_id, RunNotice.kind == kind
            )
        )
    if sent is not None:
        return False
    await send(settings, message(settings, row.email, row.name, kind, row.id))
    async with runtime.engine.begin() as conn:
        await conn.execute(
            update(RunNotice)
            .where(RunNotice.run_id == run_id, RunNotice.kind == kind)
            .values(sent_at=func.now())
        )
    return True


# The database not answering for a moment (a connection ended, a restart): tried again this often,
# this far apart, before the notice is given up (P6-B3: one was lost to a single ended connection).
# Safe: notify claims a notice before it sends, and sends a claimed one once
NOTICE_TRIES = 3
NOTICE_BACKOFF_S = 1.0


async def notify_safely(
    runtime: Runtime, run_id: uuid.UUID, kind: str, graded: bool = False
) -> None:
    """notify, never failing the run it's about."""
    for attempt in range(1, NOTICE_TRIES + 1):
        try:
            if await notify(runtime, run_id, kind, graded):
                log.info("run %s: emailed its person (%s)", run_id, kind)
            return
        except DBAPIError as e:
            if unavailable(e) is None or attempt == NOTICE_TRIES:
                log.warning("run %s: notice (%s) not sent: %s", run_id, kind, e)
                return
            await asyncio.sleep(NOTICE_BACKOFF_S * 2 ** (attempt - 1))
        except Exception as e:  # noqa: BLE001 (an email mustn't fail the run it's about)
            log.warning("run %s: notice (%s) not sent: %s", run_id, kind, e)
            return
