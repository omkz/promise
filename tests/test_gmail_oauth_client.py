from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from promise_integrations.gmail.config import GMAIL_TOKEN_ENDPOINT, GOOGLE_USERINFO_ENDPOINT, GmailConfig
from promise_integrations.gmail.oauth import GoogleOAuthClient
from promise_shared.errors import (
    IntegrationAuthorizationRevoked,
    IntegrationInvalidRequest,
    IntegrationPermissionDenied,
    IntegrationRateLimited,
    IntegrationUnavailable,
)

"""GoogleOAuthClient against httpx.MockTransport -- no live network or real Google
credentials. Mirrors the pattern tests/test_llm_provider_errors.py already uses
for Bedrock (a hand-written fake, monkeypatched in), adapted for httpx's own
first-class transport-injection seam."""


def _config() -> GmailConfig:
    return GmailConfig(
        client_id="client-id", client_secret="client-secret", redirect_uri="https://promise.example/callback",
        scopes=("https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.send"),
        state_ttl_seconds=600,
    )


def _client(handler) -> GoogleOAuthClient:
    return GoogleOAuthClient(_config(), transport=httpx.MockTransport(handler))


def test_build_authorization_url_requests_offline_access_and_carries_state():
    client = GoogleOAuthClient(_config())
    url = client.build_authorization_url(state="tok_abc123")
    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["client_id"] == ["client-id"]
    assert params["redirect_uri"] == ["https://promise.example/callback"]
    assert params["response_type"] == ["code"]
    assert params["state"] == ["tok_abc123"]
    assert params["access_type"] == ["offline"]
    assert params["prompt"] == ["consent"]
    assert "gmail.readonly" in params["scope"][0]
    assert "gmail.send" in params["scope"][0]
    assert "gmail.modify" not in params["scope"][0]


def test_exchange_code_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == GMAIL_TOKEN_ENDPOINT
        body = dict(x.split("=") for x in request.content.decode().split("&"))
        assert body["grant_type"] == "authorization_code"
        return httpx.Response(200, json={"access_token": "at_1", "refresh_token": "rt_1", "expires_in": 3600})

    result = _client(handler).exchange_code("auth-code-1")
    assert result["access_token"] == "at_1"
    assert result["refresh_token"] == "rt_1"
    assert result["expires_at"] > 0


def test_exchange_code_failure_is_invalid_request():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(IntegrationInvalidRequest):
        _client(handler).exchange_code("bad-code")


def test_exchange_code_server_error_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream down")

    with pytest.raises(IntegrationUnavailable):
        _client(handler).exchange_code("code")


def test_refresh_access_token_success_preserves_refresh_token_when_not_repeated():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "at_2", "expires_in": 3600})  # no refresh_token repeated

    result = _client(handler).refresh_access_token("rt_original")
    assert result["access_token"] == "at_2"
    assert result["refresh_token"] == "rt_original"


def test_refresh_access_token_invalid_grant_is_revoked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(IntegrationAuthorizationRevoked):
        _client(handler).refresh_access_token("rt_revoked")


def test_get_profile_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer at_1"
        return httpx.Response(200, json={"emailAddress": "andi@example.com", "historyId": "12345"})

    profile = _client(handler).get_profile(access_token="at_1")
    assert profile == {"email": "andi@example.com", "history_id": "12345"}


def test_get_profile_401_is_revoked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(IntegrationAuthorizationRevoked):
        _client(handler).get_profile(access_token="expired")


def test_get_profile_403_is_permission_denied():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    with pytest.raises(IntegrationPermissionDenied):
        _client(handler).get_profile(access_token="at_1")


def test_get_profile_429_is_rate_limited_with_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "30"})

    with pytest.raises(IntegrationRateLimited) as excinfo:
        _client(handler).get_profile(access_token="at_1")
    assert excinfo.value.retry_after == 30.0


def test_get_identity_success_calls_the_oidc_userinfo_endpoint():
    """`get_identity` -- the provider-neutral identity lookup Calendar OAuth uses
    instead of `get_profile` -- hits Google's OIDC userinfo endpoint, not Gmail's
    `users.getProfile`."""
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == GOOGLE_USERINFO_ENDPOINT
        assert request.headers["authorization"] == "Bearer at_1"
        return httpx.Response(200, json={"email": "andi@example.com", "sub": "sub_123"})

    identity = _client(handler).get_identity(access_token="at_1")
    assert identity == {"email": "andi@example.com", "sub": "sub_123"}


def test_get_identity_works_with_a_calendar_config_carrying_no_gmail_scope():
    """`get_identity` never requires a `gmail.*` scope on the config it's built
    from -- a `CalendarConfig` with only `openid`/`userinfo.email`/
    `calendar.events.owned` works identically."""
    from promise_integrations.calendar.config import CalendarConfig

    calendar_config = CalendarConfig(
        client_id="client-id", client_secret="client-secret", redirect_uri="https://promise.example/callback",
        scopes=(
            "openid", "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/calendar.events.owned",
        ),
        state_ttl_seconds=600,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"email": "andi@example.com", "sub": "sub_123"})

    client = GoogleOAuthClient(calendar_config, transport=httpx.MockTransport(handler))
    assert client.get_identity(access_token="at_1") == {"email": "andi@example.com", "sub": "sub_123"}


def test_get_identity_401_is_revoked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    with pytest.raises(IntegrationAuthorizationRevoked):
        _client(handler).get_identity(access_token="expired")


def test_get_identity_403_is_permission_denied():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={})

    with pytest.raises(IntegrationPermissionDenied):
        _client(handler).get_identity(access_token="at_1")


def test_oauth_client_never_logs_or_leaks_the_client_secret_in_errors():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_client"})

    with pytest.raises(IntegrationInvalidRequest) as excinfo:
        _client(handler).exchange_code("code")
    assert "client-secret" not in str(excinfo.value)
