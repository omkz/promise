from __future__ import annotations

import pytest
from promise_app.bootstrap import build_context
from promise_app.seed import seed as seed_demo
from promise_shared.store.local_json import LocalJsonEntityStore


@pytest.fixture
def ctx(tmp_path):
    """A fresh AppContext backed by an isolated temp-dir local store per test."""
    return build_context(store=LocalJsonEntityStore(str(tmp_path)))


@pytest.fixture
def seeded_ctx(ctx):
    return seed_demo(ctx)
