from __future__ import annotations

import os


def auto_capture_threshold() -> float:
    """Confidence below this is returned as `needs_confirmation` instead of
    being silently persisted. See `COMMITMENT_AUTO_CAPTURE_THRESHOLD`."""
    return float(os.getenv("COMMITMENT_AUTO_CAPTURE_THRESHOLD", "0.80"))
