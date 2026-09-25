from __future__ import annotations

import base64

from promise_integrations.gmail.mime import build_raw_send_message, extract_body, extract_headers, normalize_gmail_message


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def test_extract_body_prefers_text_plain():
    payload = {
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("plain body")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>html body</p>")}},
        ]
    }
    assert extract_body(payload) == "plain body"


def test_extract_body_falls_back_to_html_when_no_plain_part():
    payload = {"parts": [{"mimeType": "text/html", "body": {"data": _b64("<p>Hello <b>world</b></p>")}}]}
    assert extract_body(payload) == "Hello world"


def test_extract_body_strips_script_and_style_blocks_from_html_fallback():
    html = "<style>.x{color:red}</style><script>alert(1)</script><p>Visible text</p>"
    payload = {"parts": [{"mimeType": "text/html", "body": {"data": _b64(html)}}]}
    assert extract_body(payload) == "Visible text"


def test_extract_body_handles_nested_multipart():
    payload = {
        "parts": [
            {"mimeType": "multipart/alternative", "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64("nested plain")}},
            ]},
        ]
    }
    assert extract_body(payload) == "nested plain"


def test_extract_body_returns_empty_string_when_no_body_at_all():
    assert extract_body({"mimeType": "text/plain", "body": {}}) == ""


def test_extract_body_handles_missing_base64_padding():
    # a body whose length isn't a multiple of 4 once encoded is common; urlsafe_b64decode
    # requires padding, which _decode_body_data must add back.
    data = base64.urlsafe_b64encode(b"odd length!").decode("ascii").rstrip("=")
    payload = {"mimeType": "text/plain", "body": {"data": data}}
    assert extract_body(payload) == "odd length!"


def test_extract_headers_only_keeps_preserved_names():
    payload = {"headers": [
        {"name": "From", "value": "a@example.com"},
        {"name": "To", "value": "b@example.com"},
        {"name": "Subject", "value": "Hi"},
        {"name": "Date", "value": "Tue, 1 Jan 2026 00:00:00 +0000"},
        {"name": "Message-ID", "value": "<abc@mail.gmail.com>"},
        {"name": "X-Some-Other-Header", "value": "ignored"},
    ]}
    headers = extract_headers(payload)
    assert headers == {
        "From": "a@example.com", "To": "b@example.com", "Subject": "Hi",
        "Date": "Tue, 1 Jan 2026 00:00:00 +0000", "Message-ID": "<abc@mail.gmail.com>",
    }


def test_normalize_gmail_message_shape_matches_local_message_dict_keys():
    raw = {
        "id": "gmail_msg_1",
        "threadId": "gmail_thread_1",
        "snippet": "Hey, following up...",
        "internalDate": "1767225600000",  # 2026-01-01T00:00:00Z in ms
        "payload": {
            "headers": [
                {"name": "From", "value": "sarah@example.com"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Re: proposal"},
            ],
            "mimeType": "text/plain",
            "body": {"data": _b64("Looks good, ship it.")},
        },
    }
    normalized = normalize_gmail_message(raw, workspace_id="ws_1")

    assert normalized["id"] == "gmail_msg_1"
    assert normalized["workspace_id"] == "ws_1"
    assert normalized["thread_id"] == "gmail_thread_1"
    assert normalized["sender"] == "sarah@example.com"
    assert normalized["recipient"] == "me@example.com"
    assert normalized["subject"] == "Re: proposal"
    assert normalized["content"] == "Looks good, ship it."
    assert normalized["occurred_at"].startswith("2026-01-01")
    assert normalized["created_at"] == normalized["occurred_at"]


def test_normalize_gmail_message_never_fabricates_a_timestamp():
    raw = {"id": "m1", "payload": {"headers": [], "body": {}}}  # no internalDate at all
    normalized = normalize_gmail_message(raw, workspace_id="ws_1")
    assert normalized["occurred_at"] == ""


def test_build_raw_send_message_is_valid_base64url_rfc2822():
    import email

    raw = build_raw_send_message(to="andi@example.com", subject="Revised proposal", body="Please see attached.")
    decoded = base64.urlsafe_b64decode(raw.encode("ascii"))
    message = email.message_from_bytes(decoded)

    assert message["To"] == "andi@example.com"
    assert message["Subject"] == "Revised proposal"
    assert message.get_content_type() == "text/plain"
    assert "Please see attached." in message.get_payload(decode=True).decode("utf-8")


# ---- attachments -------------------------------------------------------------------------------

def _decode(raw: str):
    import email

    return email.message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))


def test_build_raw_send_message_with_attachment_is_multipart_mixed():
    raw = build_raw_send_message(
        to="andi@example.com", subject="Revised proposal", body="Please see attached.",
        attachment={"filename": "proposal_revised.txt", "content_type": "text/plain", "content_bytes": b"Revised proposal text."},
    )
    message = _decode(raw)

    assert message.is_multipart()
    assert message.get_content_type() == "multipart/mixed"
    assert message["To"] == "andi@example.com"
    assert message["Subject"] == "Revised proposal"


def test_attachment_body_part_preserves_the_plain_text_body():
    raw = build_raw_send_message(
        to="andi@example.com", subject="s", body="Please see attached.",
        attachment={"filename": "f.txt", "content_type": "text/plain", "content_bytes": b"attachment body"},
    )
    message = _decode(raw)
    body_part = message.get_payload()[0]
    assert body_part.get_content_type() == "text/plain"
    assert body_part.get_payload(decode=True).decode("utf-8") == "Please see attached."


def test_attachment_part_has_correct_filename_content_type_and_bytes():
    raw = build_raw_send_message(
        to="andi@example.com", subject="s", body="body",
        attachment={"filename": "proposal_revised.txt", "content_type": "text/plain", "content_bytes": b"Here is the revised text."},
    )
    message = _decode(raw)
    attachment_part = message.get_payload()[1]

    assert attachment_part.get_filename() == "proposal_revised.txt"
    assert attachment_part.get_content_type() == "text/plain"
    assert attachment_part.get("Content-Disposition", "").startswith("attachment")
    assert attachment_part.get_payload(decode=True) == b"Here is the revised text."


def test_attachment_content_type_maintype_subtype_are_split_correctly():
    raw = build_raw_send_message(
        to="a@b.com", subject="s", body="body",
        attachment={"filename": "data.json", "content_type": "application/json", "content_bytes": b'{"a": 1}'},
    )
    attachment_part = _decode(raw).get_payload()[1]
    assert attachment_part.get_content_maintype() == "application"
    assert attachment_part.get_content_subtype() == "json"


def test_attachment_content_type_falls_back_to_octet_stream_when_malformed():
    raw = build_raw_send_message(
        to="a@b.com", subject="s", body="body",
        attachment={"filename": "f", "content_type": "", "content_bytes": b"x"},
    )
    attachment_part = _decode(raw).get_payload()[1]
    assert attachment_part.get_content_type() == "application/octet-stream"


def test_no_attachment_still_produces_a_plain_text_message():
    raw = build_raw_send_message(to="a@b.com", subject="s", body="plain body only")
    message = _decode(raw)
    assert not message.is_multipart()
    assert message.get_content_type() == "text/plain"
    assert message.get_payload(decode=True).decode("utf-8") == "plain body only"
