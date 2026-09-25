from __future__ import annotations

import base64
import re
from datetime import datetime, timezone
from email.encoders import encode_base64
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, TypedDict

_PRESERVED_HEADERS = ("From", "To", "Subject", "Date", "Message-ID")


def _decode_body_data(data: str) -> str:
    """Gmail body parts are base64url, and Google's encoder sometimes omits the
    trailing `=` padding -- `urlsafe_b64decode` requires it, so pad back up to a
    multiple of 4 before decoding."""
    padded = data + "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace")


def _strip_html(html: str) -> str:
    """Deliberately not a full HTML parser -- this is a *fallback* for when no
    `text/plain` part exists at all, used only to produce readable context-
    retrieval text, never re-rendered as HTML anywhere. Strips
    script/style blocks first so their contents never leak into the text."""
    text = re.sub(r"(?is)<(script|style)\b[^>]*>.*?</\1>", "", html)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", text).strip()


def _walk_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Gmail's `payload` is a tree (multipart messages nest `parts` inside
    `parts`); flatten it once so callers don't need to require raw RFC 2822/MIME
    tree-walking knowledge themselves."""
    parts = [payload]
    for part in payload.get("parts") or []:
        parts.extend(_walk_parts(part))
    return parts


def extract_body(payload: dict[str, Any]) -> str:
    """`text/plain` first; `text/html` (stripped to plain text) as a fallback
    only when no `text/plain` part exists at all."""
    parts = _walk_parts(payload)
    plain = next((p for p in parts if p.get("mimeType") == "text/plain" and p.get("body", {}).get("data")), None)
    if plain is not None:
        return _decode_body_data(plain["body"]["data"])
    html = next((p for p in parts if p.get("mimeType") == "text/html" and p.get("body", {}).get("data")), None)
    if html is not None:
        return _strip_html(_decode_body_data(html["body"]["data"]))
    return ""


def extract_headers(payload: dict[str, Any]) -> dict[str, str]:
    return {h["name"]: h["value"] for h in payload.get("headers", []) or [] if h.get("name") in _PRESERVED_HEADERS}


def _epoch_ms_to_iso(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def normalize_gmail_message(raw: dict[str, Any], *, workspace_id: str) -> dict[str, Any]:
    """Gmail `messages.get(format="full")` response -> the same dict shape
    `LocalIntegrationProvider`'s Message rows already dump to (`id`,
    `workspace_id`, `sender`, `recipient`, `subject`, `content`, `occurred_at`,
    `created_at`), plus `thread_id`/`headers` -- extra keys `MessageSearchProvider`
    and existing callers simply ignore, so this needs no changes anywhere
    upstream. `source_system`/`source_id` provenance comes from the caller
    being `GmailIntegrationProvider` (`provider_name = "gmail"`) and this
    dict's own `id` (Gmail's message id) -- see `providers.py`'s
    `MessageSearchProvider.fetch_candidates`.

    `occurred_at` always comes from Gmail's own `internalDate` -- never a
    locally-fabricated timestamp.
    """
    payload = raw.get("payload", {}) or {}
    headers = extract_headers(payload)
    internal_date_ms = raw.get("internalDate")
    occurred_at = _epoch_ms_to_iso(int(internal_date_ms)) if internal_date_ms else ""
    return {
        "id": raw["id"],
        "workspace_id": workspace_id,
        "thread_id": raw.get("threadId"),
        "sender": headers.get("From", ""),
        "recipient": headers.get("To", ""),
        "subject": headers.get("Subject") or raw.get("snippet", ""),
        "content": extract_body(payload),
        "snippet": raw.get("snippet", ""),
        "occurred_at": occurred_at,
        "created_at": occurred_at,
        "headers": {"Message-ID": headers.get("Message-ID"), "Date": headers.get("Date")},
    }


class MessageAttachment(TypedDict):
    filename: str
    content_type: str
    content_bytes: bytes
    """The exact binary artifact bytes -- never `Document.content_text` (extracted
    text) re-encoded. `GmailIntegrationProvider.send_message` loads these from
    `DocumentBlobStore`, the same bytes a human would get by downloading the
    document; this function never re-derives or transforms them, only wraps
    them in a MIME part."""


def build_raw_send_message(
    *, to: str, subject: str, body: str, sender: str | None = None, attachment: MessageAttachment | None = None
) -> str:
    """A valid RFC 2822 MIME message, base64url-encoded for the Gmail API's
    `messages.send` `raw` field -- exactly the shape Google documents
    (https://developers.google.com/gmail/api/guides/sending), built with the
    standard library's own MIME writer rather than hand-assembling headers.

    No `attachment`: a plain `text/plain` message (unchanged from before
    attachments existed). With one: `multipart/mixed` -- a `text/plain` body
    part plus one attachment part, base64 content-transfer-encoded, with a
    `Content-Disposition: attachment; filename=...` header carrying the
    document's own name (Python's `email` package RFC 2231-encodes this
    automatically when the filename isn't pure ASCII -- no special handling
    needed here, only round-trip test coverage). Attachment bytes are written
    to the MIME part exactly as given -- `encode_base64` is the only transform
    applied, and it's reversible (Gmail/any MIME reader decodes it back to the
    identical bytes); this function never touches text encoding for binary
    content. Never Gmail's own Draft API, never SMTP -- this is only ever
    handed to `messages.send`'s `raw` field.
    """
    message: MIMEText | MIMEMultipart
    if attachment is None:
        message = MIMEText(body, "plain", "utf-8")
    else:
        message = MIMEMultipart("mixed")
        message.attach(MIMEText(body, "plain", "utf-8"))

        content_type = attachment["content_type"] or "application/octet-stream"
        maintype, _, subtype = content_type.partition("/")
        part = MIMEBase(maintype or "application", subtype or "octet-stream")
        part.set_payload(attachment["content_bytes"])
        encode_base64(part)
        part.add_header("Content-Disposition", "attachment", filename=attachment["filename"])
        message.attach(part)

    message["To"] = to
    message["Subject"] = subject
    if sender:
        message["From"] = sender
    return base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
