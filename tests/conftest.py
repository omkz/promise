from __future__ import annotations

import pytest
from promise_app.bootstrap import build_context
from promise_app.seed import seed as seed_demo
from promise_shared.secrets.local_secret_store import LocalSecretStore
from promise_shared.store.local_json import LocalJsonEntityStore


@pytest.fixture
def ctx(tmp_path):
    """A fresh AppContext backed by an isolated temp-dir local store (and local
    secret store) per test -- never the real repo-relative `./data/secrets`."""
    return build_context(
        store=LocalJsonEntityStore(str(tmp_path / "entities")),
        secret_store=LocalSecretStore(str(tmp_path / "secrets")),
    )


@pytest.fixture
def seeded_ctx(ctx):
    return seed_demo(ctx)
