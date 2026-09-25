from __future__ import annotations

import base64
import email
from pathlib import Path

from promise_integrations.gmail.mime import build_raw_send_message

"""MIME regression tests against real binary fixtures (tests/fixtures/sample.docx,
tests/fixtures/sample.pdf) -- minimal, genuinely valid files, never renamed text.
Proves the generic Gmail attachment pipeline (build_raw_send_message) preserves
arbitrary binary content exactly, regardless of format -- DOCX generation itself
is exercised separately in tests/test_gmail_approval_flow.py, through the real
revision planner."""

FIXTURES_DIR = Path(__file__).parent / "fixtures"
DOCX_BYTES = (FIXTURES_DIR / "sample.docx").read_bytes()
PDF_BYTES = (FIXTURES_DIR / "sample.pdf").read_bytes()
DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _decode(raw: str) -> email.message.Message:
    return email.message_from_bytes(base64.urlsafe_b64decode(raw.encode("ascii")))


def test_fixtures_are_real_binary_files_not_renamed_text():
    assert DOCX_BYTES.startswith(b"PK\x03\x04")  # DOCX is a ZIP container
    assert PDF_BYTES.startswith(b"%PDF-1.4")
    # neither fixture is plausible as plain ASCII/UTF-8 text throughout
    assert any(b > 127 for b in DOCX_BYTES)


def test_docx_attachment_multipart_structure_and_exact_bytes():
    raw = build_raw_send_message(
        to="andi@example.com", subject="Revised proposal", body="Please see the attached revised proposal.",
        attachment={"filename": "sample.docx", "content_type": DOCX_CONTENT_TYPE, "content_bytes": DOCX_BYTES},
    )
    message = _decode(raw)

    assert message.is_multipart()
    assert message.get_content_type() == "multipart/mixed"
    body_part, attachment_part = message.get_payload()

    # plain-text body present
    assert body_part.get_content_type() == "text/plain"
    assert body_part.get_payload(decode=True).decode("utf-8") == "Please see the attached revised proposal."

    # filename/content-type correct, attachment bytes EXACTLY equal fixture bytes
    assert attachment_part.get_filename() == "sample.docx"
    assert attachment_part.get_content_type() == DOCX_CONTENT_TYPE
    assert attachment_part.get("Content-Disposition", "").startswith("attachment")
    assert attachment_part.get_payload(decode=True) == DOCX_BYTES


def test_docx_attachment_parses_back_as_a_valid_docx_via_python_docx():
    import io

    import docx

    raw = build_raw_send_message(
        to="a@b.com", subject="s", body="body",
        attachment={"filename": "sample.docx", "content_type": DOCX_CONTENT_TYPE, "content_bytes": DOCX_BYTES},
    )
    attachment_part = _decode(raw).get_payload()[1]
    roundtripped = attachment_part.get_payload(decode=True)

    parsed = docx.Document(io.BytesIO(roundtripped))
    assert [p.text for p in parsed.paragraphs] == [
        "This is a minimal valid DOCX fixture used only by PROMISE test suite.",
        "It proves the Gmail attachment pipeline preserves exact binary bytes.",
    ]


def test_pdf_attachment_multipart_structure_and_exact_bytes():
    raw = build_raw_send_message(
        to="andi@example.com", subject="Report", body="Please see the attached report.",
        attachment={"filename": "sample.pdf", "content_type": "application/pdf", "content_bytes": PDF_BYTES},
    )
    message = _decode(raw)
    body_part, attachment_part = message.get_payload()

    assert message.get_content_type() == "multipart/mixed"
    assert body_part.get_payload(decode=True).decode("utf-8") == "Please see the attached report."
    assert attachment_part.get_filename() == "sample.pdf"
    assert attachment_part.get_content_type() == "application/pdf"
    assert attachment_part.get_payload(decode=True) == PDF_BYTES


def test_pdf_attachment_bytes_are_not_corrupted_by_base64_round_trip():
    """Every byte, including the non-ASCII bytes real PDF content streams
    contain, must survive base64 content-transfer-encoding intact."""
    raw = build_raw_send_message(
        to="a@b.com", subject="s", body="b",
        attachment={"filename": "sample.pdf", "content_type": "application/pdf", "content_bytes": PDF_BYTES},
    )
    attachment_part = _decode(raw).get_payload()[1]
    assert len(attachment_part.get_payload(decode=True)) == len(PDF_BYTES)
    assert attachment_part.get_payload(decode=True) == PDF_BYTES


def test_no_attachment_message_remains_a_valid_plain_text_message():
    raw = build_raw_send_message(to="andi@example.com", subject="s", body="No attachment here.")
    message = _decode(raw)
    assert not message.is_multipart()
    assert message.get_content_type() == "text/plain"
    assert message.get_payload(decode=True).decode("utf-8") == "No attachment here."


def test_docx_attachment_with_international_filename_survives_round_trip():
    raw = build_raw_send_message(
        to="a@b.com", subject="s", body="b",
        attachment={"filename": "改訂版_提案書.docx", "content_type": DOCX_CONTENT_TYPE, "content_bytes": DOCX_BYTES},
    )
    attachment_part = _decode(raw).get_payload()[1]
    assert attachment_part.get_filename() == "改訂版_提案書.docx"
    assert attachment_part.get_payload(decode=True) == DOCX_BYTES
