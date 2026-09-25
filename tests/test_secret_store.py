from __future__ import annotations

from promise_shared.secrets.local_secret_store import LocalSecretStore


def test_get_missing_secret_returns_none(tmp_path):
    store = LocalSecretStore(str(tmp_path))
    assert store.get_secret("gmail:ws_1:ia_1") is None


def test_put_then_get_roundtrips(tmp_path):
    store = LocalSecretStore(str(tmp_path))
    store.put_secret("gmail:ws_1:ia_1", {"refresh_token": "rt_1", "access_token": "at_1", "expires_at": 123.0})
    assert store.get_secret("gmail:ws_1:ia_1") == {"refresh_token": "rt_1", "access_token": "at_1", "expires_at": 123.0}


def test_put_overwrites_existing_value(tmp_path):
    store = LocalSecretStore(str(tmp_path))
    store.put_secret("ref", {"v": 1})
    store.put_secret("ref", {"v": 2})
    assert store.get_secret("ref") == {"v": 2}


def test_delete_removes_the_secret(tmp_path):
    store = LocalSecretStore(str(tmp_path))
    store.put_secret("ref", {"v": 1})
    store.delete_secret("ref")
    assert store.get_secret("ref") is None


def test_delete_missing_secret_does_not_raise(tmp_path):
    store = LocalSecretStore(str(tmp_path))
    store.delete_secret("never-existed")  # must not raise


def test_different_refs_are_stored_independently(tmp_path):
    store = LocalSecretStore(str(tmp_path))
    store.put_secret("gmail:ws_1:ia_1", {"who": "a"})
    store.put_secret("gmail:ws_1:ia_2", {"who": "b"})
    assert store.get_secret("gmail:ws_1:ia_1") == {"who": "a"}
    assert store.get_secret("gmail:ws_1:ia_2") == {"who": "b"}


def test_ref_is_not_used_verbatim_as_a_filename(tmp_path):
    """A ref can embed a workspace/account id; the on-disk filename must not
    echo it back verbatim (see LocalSecretStore's own docstring)."""
    store = LocalSecretStore(str(tmp_path))
    store.put_secret("gmail:ws_1:ia_1", {"v": 1})
    filenames = [p.name for p in tmp_path.iterdir()]
    assert not any("gmail" in name or "ws_1" in name or "ia_1" in name for name in filenames)
