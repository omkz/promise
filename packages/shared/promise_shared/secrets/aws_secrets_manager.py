from __future__ import annotations

import json
from typing import Any


class AwsSecretsManagerStore:
    """AWS Secrets Manager-backed secret store -- the production implementation of
    `SecretStore`. Each `ref` (a backend-agnostic logical id, e.g.
    `"gmail:<workspace_id>:<account_id>"`) maps to one Secrets Manager secret named
    `<name_prefix><ref>`; the value is always the JSON-encoded dict passed to
    `put_secret`.

    No AWS account id, region, or secret ARN is ever hard-coded here -- `region`/
    `name_prefix` are the only configuration, both supplied by the caller (see
    `build_secret_store`, which reads them from `AWS_REGION`/`SECRETS_MANAGER_PREFIX`).
    """

    def __init__(self, region: str, name_prefix: str = "promise/") -> None:
        import boto3

        from ..aws_config import boto_config

        self._client = boto3.client("secretsmanager", region_name=region, config=boto_config())
        self._name_prefix = name_prefix

    def _name(self, ref: str) -> str:
        return f"{self._name_prefix}{ref}"

    def get_secret(self, ref: str) -> dict[str, Any] | None:
        from botocore.exceptions import ClientError

        try:
            resp = self._client.get_secret_value(SecretId=self._name(ref))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
                return None
            raise
        return json.loads(resp["SecretString"])

    def put_secret(self, ref: str, value: dict[str, Any]) -> None:
        from botocore.exceptions import ClientError

        name = self._name(ref)
        secret_string = json.dumps(value)
        try:
            self._client.put_secret_value(SecretId=name, SecretString=secret_string)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise
            self._client.create_secret(Name=name, SecretString=secret_string)

    def delete_secret(self, ref: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.delete_secret(SecretId=self._name(ref), ForceDeleteWithoutRecovery=True)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
                raise
