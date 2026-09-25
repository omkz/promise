from __future__ import annotations

import httpx
from promise_integrations.gmail.config import (
    DEFAULT_ATTACHMENT_ENCODING_MARGIN,
    DEFAULT_MAX_ATTACHMENT_BYTES,
    GmailConfig,
    load_gmail_config,
)
from promise_integrations.gmail.provider import GmailIntegrationProvider

"""GmailConfig's size-limit fields: typed, centrally read from env, with a
documented default -- never hard-coded inside GmailIntegrationProvider (which
only ever reads config.max_attachment_bytes/config.attachment_encoding_margin,
see tests/test_gmail_provider.py)."""

ENV = {"GOOGLE_CLIENT_ID": "cid", "GOOGLE_CLIENT_SECRET": "csecret", "GOOGLE_REDIRECT_URI": "https://promise.example/callback"}


def _set_env(monkeypatch, **overrides):
    for key, value in {**ENV, **overrides}.items():
        monkeypatch.setenv(key, value)


def test_default_max_attachment_bytes_is_25_mib():
    assert DEFAULT_MAX_ATTACHMENT_BYTES == 25 * 1024 * 1024


def test_default_encoding_margin_is_documented_and_greater_than_one():
    # must be > 1.0 (base64 alone already inflates by 4/3) or the pre-check would be
    # weaker than a no-op.
    assert DEFAULT_ATTACHMENT_ENCODING_MARGIN > 1.0


def test_gmail_config_max_attachment_bytes_defaults_without_env_override(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.delenv("GMAIL_MAX_ATTACHMENT_BYTES", raising=False)
    config = load_gmail_config()
    assert config.max_attachment_bytes == DEFAULT_MAX_ATTACHMENT_BYTES


def test_gmail_config_encoding_margin_defaults_without_env_override(monkeypatch):
    _set_env(monkeypatch)
    monkeypatch.delenv("GMAIL_ATTACHMENT_ENCODING_MARGIN", raising=False)
    config = load_gmail_config()
    assert config.attachment_encoding_margin == DEFAULT_ATTACHMENT_ENCODING_MARGIN


def test_gmail_max_attachment_bytes_env_override_is_read_centrally(monkeypatch):
    _set_env(monkeypatch, GMAIL_MAX_ATTACHMENT_BYTES="1048576")
    config = load_gmail_config()
    assert config.max_attachment_bytes == 1048576


def test_gmail_attachment_encoding_margin_env_override_is_read_centrally(monkeypatch):
    _set_env(monkeypatch, GMAIL_ATTACHMENT_ENCODING_MARGIN="1.5")
    config = load_gmail_config()
    assert config.attachment_encoding_margin == 1.5


def test_max_attachment_bytes_is_a_typed_int_field_not_a_magic_number_in_the_provider():
    import inspect

    from promise_integrations.gmail import provider as provider_module

    source = inspect.getsource(provider_module)
    # the provider must read the limit from config, never hard-code Gmail's 25MB figure
    assert "26214400" not in source
    assert str(25 * 1024 * 1024) not in source


def test_provider_is_constructible_with_the_existing_test_config_fixture(ctx):
    """GmailConfig's new field has a default, so every existing test's
    `GmailConfig(...)` call site (no attachment_encoding_margin given)
    continues to construct a working provider."""
    config = GmailConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600,
    )
    provider = GmailIntegrationProvider(
        account_id="ia_1", workspace_id="ws_1", secret_ref="gmail:ws_1:ia_1", secret_store=ctx.secret_store,
        config=config, drafts=ctx.repos.drafts, documents=ctx.repos.documents, blob_store=ctx.blob_store,
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    assert provider.provider_name == "gmail"


def test_provider_uses_the_configured_limit_not_the_default(ctx):
    config = GmailConfig(
        client_id="cid", client_secret="csecret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600, max_attachment_bytes=1000, attachment_encoding_margin=2.0,
    )
    provider = GmailIntegrationProvider(
        account_id="ia_1", workspace_id="ws_1", secret_ref="gmail:ws_1:ia_1", secret_store=ctx.secret_store,
        config=config, drafts=ctx.repos.drafts, documents=ctx.repos.documents, blob_store=ctx.blob_store,
        transport=httpx.MockTransport(lambda r: httpx.Response(500)),
    )
    assert provider._max_attachment_bytes == 1000
    assert provider._attachment_encoding_margin == 2.0
