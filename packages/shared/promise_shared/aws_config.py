from __future__ import annotations

import os


def boto_config():
    """Shared `botocore.config.Config` for every boto3 client/resource this
    codebase constructs (DynamoDB, S3, Secrets Manager, Bedrock) -- boto3's own
    default retry mode ("legacy") only retries a narrow set of throttling
    errors with no backoff jitter; "standard" mode retries a wider, documented
    set of transient/throttling errors with exponential backoff, which is what
    every one of those clients should use in a deployed environment. Never
    imported at module scope by a caller (each `boto3.client(...)` call site
    imports `botocore.config.Config` itself, right next to `boto3`) so that
    importing e.g. `promise_shared.store` still doesn't require botocore to be
    installed unless a caller actually asks for the `dynamodb` backend.

    `AWS_MAX_RETRY_ATTEMPTS` (default 5) is the one shared knob -- total
    attempts including the first, same unit boto3's own `max_attempts` uses.
    """
    from botocore.config import Config

    max_attempts = int(os.getenv("AWS_MAX_RETRY_ATTEMPTS", "5"))
    return Config(retries={"mode": "standard", "max_attempts": max_attempts})
