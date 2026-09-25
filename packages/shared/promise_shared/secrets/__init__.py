from __future__ import annotations

import os
from typing import Any, Protocol

from .aws_secrets_manager import AwsSecretsManagerStore
from .local_secret_store import LocalSecretStore


class SecretStore(Protocol):
    """Where PROMISE keeps secret material (today: Gmail OAuth refresh tokens) that must
    never sit in a normal application record. `IntegrationAccount.secret_ref` is a pointer
    into this store, not the secret itself -- see `IntegrationAccount`'s own docstring.

    A "secret" here is always a small JSON-serializable dict (e.g. `{"refresh_token": ...,
    "access_token": ..., "expires_at": ...}`), never a raw string blob -- keeps every
    implementation's storage shape identical regardless of backend.
    """

    def get_secret(self, ref: str) -> dict[str, Any] | None: ...

    def put_secret(self, ref: str, value: dict[str, Any]) -> None: ...

    def delete_secret(self, ref: str) -> None: ...


def build_secret_store() -> SecretStore:
    """`SECRET_STORE_BACKEND=aws` (recommended whenever `STORAGE_BACKEND=dynamodb`, i.e. any
    real deployment) -> AWS Secrets Manager. Unset/anything else -> the local file-backed
    store -- local development and tests only, exactly the same "never real production
    security, isolated behind one interface" split `AUTH_MODE=local` already uses."""
    backend = os.getenv("SECRET_STORE_BACKEND", "local").lower()
    if backend == "aws":
        return AwsSecretsManagerStore(
            region=os.getenv("AWS_REGION", "us-east-1"),
            name_prefix=os.getenv("SECRETS_MANAGER_PREFIX", "promise/"),
        )
    return LocalSecretStore(os.getenv("LOCAL_SECRETS_DIR", "./data/secrets"))


__all__ = ["SecretStore", "build_secret_store", "AwsSecretsManagerStore", "LocalSecretStore"]
