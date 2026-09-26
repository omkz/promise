from __future__ import annotations

from promise_shared.aws_config import boto_config

"""Coverage for the shared boto3 retry configuration (promise_shared.aws_config.boto_config)
and that every AWS-backed client this codebase constructs -- DynamoDB, S3, Secrets Manager,
Bedrock -- actually passes it through, rather than falling back to boto3's default ("legacy")
retry mode. No live AWS credentials or network required: boto3.client/resource are
monkeypatched to hand-written fakes that capture their call kwargs, same style as
tests/test_dynamodb_index.py / tests/test_document_blob_store.py / tests/test_llm_provider_errors.py."""


def test_boto_config_defaults_to_standard_mode_and_five_attempts(monkeypatch):
    monkeypatch.delenv("AWS_MAX_RETRY_ATTEMPTS", raising=False)
    config = boto_config()
    assert config.retries == {"mode": "standard", "max_attempts": 5}


def test_boto_config_reads_max_attempts_from_env(monkeypatch):
    monkeypatch.setenv("AWS_MAX_RETRY_ATTEMPTS", "10")
    config = boto_config()
    assert config.retries == {"mode": "standard", "max_attempts": 10}


def test_dynamodb_store_passes_the_shared_retry_config_to_boto3_resource(monkeypatch):
    import boto3
    from promise_shared.store.dynamodb import DynamoEntityStore

    calls: list[dict] = []

    class _FakeTable:
        pass

    class _FakeResource:
        def Table(self, name):
            return _FakeTable()

    def fake_resource(*args, **kwargs):
        calls.append(kwargs)
        return _FakeResource()

    monkeypatch.setattr(boto3, "resource", fake_resource)
    DynamoEntityStore(table_name="PROMISE", region="us-east-1")

    assert calls[0]["config"].retries["mode"] == "standard"


def test_s3_blob_store_passes_the_shared_retry_config_to_boto3_client(monkeypatch):
    import boto3
    from promise_shared.blobs.s3_blob_store import S3BlobStore

    calls: list[dict] = []
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: calls.append(kw))
    S3BlobStore(bucket="promise-documents", region="us-east-1")

    assert calls[0]["config"].retries["mode"] == "standard"


def test_secrets_manager_store_passes_the_shared_retry_config_to_boto3_client(monkeypatch):
    import boto3
    from promise_shared.secrets.aws_secrets_manager import AwsSecretsManagerStore

    calls: list[dict] = []
    monkeypatch.setattr(boto3, "client", lambda *a, **kw: calls.append(kw))
    AwsSecretsManagerStore(region="us-east-1")

    assert calls[0]["config"].retries["mode"] == "standard"


def test_bedrock_converse_passes_the_shared_retry_config_to_boto3_client(monkeypatch):
    import boto3
    from promise_agent import llm

    calls: list[dict] = []

    class _FakeClient:
        def converse(self, **kwargs):
            return {"output": {"message": {"content": [{"text": "ok"}]}}}

    def fake_client(*args, **kwargs):
        calls.append(kwargs)
        return _FakeClient()

    monkeypatch.setattr(boto3, "client", fake_client)
    llm.bedrock_converse("prompt")

    assert calls[0]["config"].retries["mode"] == "standard"


def test_commitment_extraction_bedrock_provider_passes_the_shared_retry_config(monkeypatch):
    import boto3
    from promise_agent.commitment_extraction.provider import BedrockCommitmentExtractionProvider

    calls: list[dict] = []

    class _FakeClient:
        def converse(self, **kwargs):
            return {"output": {"message": {"content": []}}}  # no toolUse -> extract() raises, which is fine here

    def fake_client(*args, **kwargs):
        calls.append(kwargs)
        return _FakeClient()

    monkeypatch.setattr(boto3, "client", fake_client)
    provider = BedrockCommitmentExtractionProvider()
    try:
        provider.extract("I'll send the report tomorrow")
    except Exception:
        pass  # only the boto3.client(...) call kwargs are under test here

    assert calls[0]["config"].retries["mode"] == "standard"
