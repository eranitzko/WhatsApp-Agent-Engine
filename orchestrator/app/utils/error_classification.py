"""Classify an unhandled exception into a short, WhatsApp-postable message
naming which layer failed, so a "bot not responding" report can be triaged
from the reply itself instead of requiring a log dive.

Note on scope: this can only cover failures that happen *after* a message
reaches the orchestrator and *before* the reply-send itself fails — if the
WhatsApp bridge or the group's internet path is what's actually down, there
is no channel left to deliver a diagnosis through (see main.py's _send,
which already swallows its own failures for exactly this reason). Bridge
connectivity issues are visible in server logs instead, and the bridge's
own watchdog (bridge/src/connection.js) auto-recovers a stuck connection
without needing a WhatsApp-side notification.
"""

from __future__ import annotations

import anthropic
import httpx
import sqlalchemy.exc


def classify_error(exc: Exception) -> str:
    """Return a short, user-facing WhatsApp message identifying the failed
    layer. Falls back to a generic message for anything unrecognized."""

    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 400 and "credit balance" in str(exc).lower():
            return (
                "💳 Out of AI credits — the Anthropic account balance is too low. "
                "An admin needs to add credits at console.anthropic.com."
            )
        if exc.status_code == 429:
            return "⏳ AI service is rate-limited right now. Please try again in a minute."
        if exc.status_code in (401, 403):
            return "🔑 AI service authentication failed — the API key needs attention. Contact the admin."
        if exc.status_code >= 500:
            return "🌐 The AI service (Anthropic) is having issues on their end right now. Please try again shortly."
        return "🤖 The AI service rejected the request. Please try again — if it keeps happening, let the admin know."

    if isinstance(exc, anthropic.APIConnectionError):
        return "📡 Couldn't reach the AI service — likely a network issue reaching Anthropic. Please try again shortly."

    if isinstance(exc, sqlalchemy.exc.SQLAlchemyError):
        return "🗄️ Database error while processing that. Please try again — if it persists, contact the admin."

    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError)):
        return "📡 Network issue reaching an external service. Please try again shortly."

    return (
        "⚠️ Something went wrong processing that. Please try again in a moment — "
        "if it keeps happening, let the admin know."
    )
