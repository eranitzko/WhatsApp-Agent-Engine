"""Tests for the orchestrator-level export_report tool."""

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── delivery tests ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deliver_files_to_group_calls_send_file():
    from app.export.delivery import deliver_files

    mock_send = AsyncMock()
    with patch("app.export.delivery.send_file", mock_send):
        await deliver_files(
            group_jid="123@g.us",
            email=None,
            delivery="group",
            files=[("report.pdf", "application/pdf", b"pdf-data")],
        )

    mock_send.assert_called_once_with("123@g.us", "report.pdf", "application/pdf", b"pdf-data")


@pytest.mark.asyncio
async def test_deliver_files_by_email_calls_send_report_email():
    from app.export.delivery import deliver_files

    mock_mail = MagicMock()

    async def fake_to_thread(fn, **kwargs):
        fn(**kwargs)

    with patch("app.export.delivery.send_report_email", mock_mail), \
         patch("app.export.delivery.asyncio.to_thread", fake_to_thread):
        await deliver_files(
            group_jid="123@g.us",
            email="user@example.com",
            delivery="email",
            files=[("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"xlsx")],
            subject="My Report",
            body="See attached.",
        )

    mock_mail.assert_called_once_with(
        to="user@example.com",
        subject="My Report",
        body="See attached.",
        attachments=[("report.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", b"xlsx")],
    )


@pytest.mark.asyncio
async def test_deliver_files_both_calls_send_file_and_email():
    from app.export.delivery import deliver_files

    mock_send = AsyncMock()
    mock_mail = MagicMock()

    async def fake_to_thread(fn, **kwargs):
        fn(**kwargs)

    with patch("app.export.delivery.send_file", mock_send), \
         patch("app.export.delivery.send_report_email", mock_mail), \
         patch("app.export.delivery.asyncio.to_thread", fake_to_thread):
        await deliver_files(
            group_jid="123@g.us",
            email="user@example.com",
            delivery="both",
            files=[("report.pdf", "application/pdf", b"pdf")],
            subject="Report",
            body="Attached.",
        )

    mock_send.assert_called_once()
    mock_mail.assert_called_once()
