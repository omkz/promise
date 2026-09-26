from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import BlobObject


class S3BlobStore:
    """S3-backed `DocumentBlobStore` -- the production implementation.

    Object key is `<key_prefix><ref>` (`ref` always produced by `blob_ref()`,
    never a caller-supplied path -- see that function's docstring); no bucket
    name/account id/region is hard-coded, only `bucket`/`region`/`key_prefix`,
    all supplied by the caller (see `build_blob_store`, which reads them from
    `S3_BUCKET`/`AWS_REGION`/`S3_DOCUMENTS_PREFIX`).

    Objects are written with the bucket's default (private) ACL -- `put_object`
    never sets `ACL="public-read"` or any equivalent, so an artifact is never
    reachable except through PROMISE's own authorized read path. Filename and
    size are stored as S3 object metadata (`x-amz-meta-*`, string-valued) so
    `get()` can hand back the same `BlobObject` shape `LocalBlobStore` does
    without a separate lookup.
    """

    def __init__(self, bucket: str, region: str, key_prefix: str = "documents/") -> None:
        import boto3

        from ..aws_config import boto_config

        self._client = boto3.client("s3", region_name=region, config=boto_config())
        self._bucket = bucket
        self._key_prefix = key_prefix

    def _key(self, ref: str) -> str:
        return f"{self._key_prefix}{ref}"

    def put(self, ref: str, data: bytes, *, content_type: str, filename: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._key(ref),
            Body=data,
            ContentType=content_type,
            Metadata={"filename": filename, "size_bytes": str(len(data))},
        )

    def get(self, ref: str) -> BlobObject | None:
        from botocore.exceptions import ClientError

        from . import BlobObject

        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=self._key(ref))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        data = resp["Body"].read()
        metadata = resp.get("Metadata", {})
        return BlobObject(
            data=data,
            content_type=resp.get("ContentType") or "application/octet-stream",
            filename=metadata.get("filename", ref),
            size_bytes=int(metadata.get("size_bytes", len(data))),
        )

    def delete(self, ref: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._key(ref))
