from __future__ import annotations

import io

"""Binary artifact generation for a *revised* document -- kept separate from
`send_message_planner.py`'s orchestration logic so the "how do I produce real
bytes for this format" concern has one obvious home.

Deliberately narrow (v1): DOCX sources get a real, valid DOCX artifact (via
`python-docx`); anything else falls back to the revised text's own UTF-8
bytes under its own actual content type -- which is truthful, not a
workaround, since plain text already *is* its own valid binary
representation. This function never labels plain text as DOCX/PDF, and never
generates a fake binary artifact by writing text into a file with a
misleading extension.
"""

DOCX_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def generate_revised_artifact(*, source_type: str, revised_text: str) -> tuple[bytes, str]:
    """Returns `(artifact_bytes, content_type)` for the revised document,
    matching the source document's own format where a real generator exists."""
    if source_type == DOCX_CONTENT_TYPE:
        return _build_docx(revised_text), DOCX_CONTENT_TYPE
    return revised_text.encode("utf-8"), source_type or "text/plain"


def _build_docx(text: str) -> bytes:
    import docx  # python-docx -- the package name and the import name deliberately differ

    document = docx.Document()
    for paragraph in text.split("\n"):
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()
