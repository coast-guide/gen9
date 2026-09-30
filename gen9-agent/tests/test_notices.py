"""Notices about background runs (notices.py): what the email says, and whom it reaches."""

import uuid

import pytest

from gen9_agent import notices
from gen9_agent.settings import Settings

pytestmark = pytest.mark.asyncio


async def test_the_email_names_the_task_links_the_chat_and_holds_no_answer() -> None:
    settings = Settings.model_construct(
        smtp_from="Gen9 <gen9@gen9.local>", gen9_ui_url="http://localhost:14000/"
    )
    chat = uuid.UUID("00000000-0000-0000-0000-000000000007")
    email = notices.message(
        settings, "alan@gen9.test", "Morning brief", "waiting", chat
    )
    assert email["Subject"] == "Morning brief needs you"
    assert email["To"] == "alan@gen9.test" and email["From"] == "Gen9 <gen9@gen9.local>"
    body = email.get_content()
    assert f"http://localhost:14000/chat/{chat}" in body
    assert "http://localhost:14000/settings" in body


async def test_who_gets_which() -> None:
    assert notices.WANTS["all"] == {"done", "waiting", "failed", "unmet"}
    assert notices.WANTS["needs_you"] == {"waiting"}
    assert not notices.WANTS["never"]


async def test_a_graded_task_that_missed_its_rubric_says_so() -> None:
    settings = Settings.model_construct(
        smtp_from="Gen9 <gen9@gen9.local>", gen9_ui_url="http://localhost:14000"
    )
    email = notices.message(
        settings, "alan@gen9.test", "Weekly report", "unmet", uuid.uuid4()
    )
    assert email["Subject"] == "Weekly report didn't meet its rubric"


async def test_the_email_carries_the_headers_mail_services_require() -> None:
    """RFC 5322 §3.6 requires Date; Gmail refuses mail without a Message-ID (P4-C4)."""
    from email.utils import parsedate_to_datetime

    settings = Settings.model_construct(
        smtp_from="Gen9 <gen9@mail.example.org>", gen9_ui_url="http://localhost:14000"
    )
    chat = uuid.UUID("00000000-0000-0000-0000-000000000007")
    first = notices.message(settings, "alan@gen9.test", "Brief", "done", chat)
    second = notices.message(settings, "alan@gen9.test", "Brief", "done", chat)
    assert parsedate_to_datetime(first["Date"]).tzinfo is not None
    assert first["Message-ID"].endswith("@mail.example.org>")
    assert first["Message-ID"] != second["Message-ID"]
    assert first["Auto-Submitted"] == "auto-generated"


async def test_a_task_named_across_lines_still_gets_its_email() -> None:
    """The policy refused a header with a line break, and with it the whole email (P4-C4)."""
    settings = Settings.model_construct(
        smtp_from="Gen9 <gen9@gen9.local>", gen9_ui_url="http://localhost:14000"
    )
    chat = uuid.UUID("00000000-0000-0000-0000-000000000007")
    email = notices.message(
        settings, "alan@gen9.test", "Weekly\r\nBcc: x@example.com", "done", chat
    )
    assert email["Subject"] == "Weekly Bcc: x@example.com is done"
    assert "Bcc" not in email
    assert b"\nBcc:" not in email.as_bytes()
    # Other scripts are encoded, and come back as written
    accented = notices.message(settings, "alan@gen9.test", "Résumé 週報", "done", chat)
    assert accented["Subject"] == "Résumé 週報 is done"
    assert "=?utf-8?" in accented.as_string()
