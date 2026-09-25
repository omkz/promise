from __future__ import annotations

import os
from dataclasses import dataclass

"""Centralized Gmail OAuth/API configuration -- the one place scope strings and
Google's endpoint URLs are defined. Nothing else in the codebase should hard-code
a `https://www.googleapis.com/auth/gmail...` string or a Google endpoint URL.

Scopes (v1, deliberately minimal -- see the root README's "Gmail Integration"
section for why): `gmail.readonly` (search/read, for Context Retrieval) and
`gmail.send` (send-only, for the approval-gated execution step). Never
`gmail.modify`/`gmail.compose`/`mail.google.com` -- v1 has no reason to touch
Gmail's own Draft API or mutate a user's mailbox beyond sending a message
PROMISE's own approval flow already authorized.
"""

GMAIL_AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

DEFAULT_SCOPES: tuple[str, ...] = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
)

DEFAULT_STATE_TTL_SECONDS = 600

# Gmail's own documented `messages.send` limit is 25 MB TOTAL MESSAGE SIZE -- the
# complete base64url-encoded RFC 2822 message (headers, body, and attachment together),
# not the attachment's own raw byte count: https://developers.google.com/gmail/api/guides/sending.
# `GmailIntegrationProvider.send_message` treats this as the authoritative limit, checked
# against the literal length of the final encoded message right before calling Gmail --
# see `attachment_encoding_margin` below for the earlier, fast pre-check this budget also
# drives.
DEFAULT_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024

# base64 content-transfer-encoding inflates content by exactly 4/3; MIME multipart
# headers/boundaries/Content-* headers add a further small, roughly-fixed overhead on
# top of that. 1.35 is a documented, fixed safety margin (never tuned per-message) used
# only for a fast, conservative pre-check on the attachment's own raw byte count --
# `raw_bytes > max_attachment_bytes / attachment_encoding_margin` rejects an attachment
# before any MIME message is even built when it's obviously going to blow the limit once
# encoded. It is deliberately conservative (i.e. rejects some borderline-fine attachments
# too) because the actual pass/fail authority is the final encoded-message-size check
# that always runs afterward, not this estimate.
DEFAULT_ATTACHMENT_ENCODING_MARGIN = 1.35


@dataclass(frozen=True)
class GmailConfig:
    client_id: str
    client_secret: str
    redirect_uri: str
    scopes: tuple[str, ...]
    state_ttl_seconds: int
    max_attachment_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES
    attachment_encoding_margin: float = DEFAULT_ATTACHMENT_ENCODING_MARGIN


class GmailNotConfigured(RuntimeError):
    """`GMAIL_ENABLED=true` but one or more required environment variables is
    missing. Never raised just because Gmail is disabled -- callers check
    `gmail_enabled()` first; this is a configuration mistake, not a normal
    "not connected" state (see `promise_shared.errors.IntegrationNotConnected`
    for that)."""


def gmail_enabled() -> bool:
    return os.getenv("GMAIL_ENABLED", "false").strip().lower() == "true"


def load_gmail_config() -> GmailConfig:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI")
    if not client_id or not client_secret or not redirect_uri:
        raise GmailNotConfigured(
            "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REDIRECT_URI must all be set (GMAIL_ENABLED=true)"
        )
    raw_scopes = os.getenv("GMAIL_OAUTH_SCOPES")
    scopes = tuple(s.strip() for s in raw_scopes.split(",") if s.strip()) if raw_scopes else DEFAULT_SCOPES
    return GmailConfig(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scopes=scopes,
        state_ttl_seconds=int(os.getenv("GOOGLE_OAUTH_STATE_TTL", str(DEFAULT_STATE_TTL_SECONDS))),
        max_attachment_bytes=int(os.getenv("GMAIL_MAX_ATTACHMENT_BYTES", str(DEFAULT_MAX_ATTACHMENT_BYTES))),
        attachment_encoding_margin=float(
            os.getenv("GMAIL_ATTACHMENT_ENCODING_MARGIN", str(DEFAULT_ATTACHMENT_ENCODING_MARGIN))
        ),
    )
