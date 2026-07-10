"""Cloudflare R2 (S3-compatible) media store for archival upload copies (#408).

The upload-your-own-video flow transcodes a downscaled archival copy of the
user's source and stores it in a private R2 bucket. Objects are retrieved only
via short-lived presigned URLs (GET /transcripts/{id}/media -> 302 redirect).

The whole feature is optional: :func:`is_configured` returns ``False`` until
all four R2 credentials are set, in which case the upload endpoint returns 503
and the worker never enters the archiving stage. boto3 is imported lazily so a
misconfiguration (or the absence of the optional dependency) surfaces as a
clean :class:`MediaStoreError` rather than an import-time crash of the whole
service.
"""
from __future__ import annotations

from pathlib import Path

from scribe.config import settings


class MediaStoreError(RuntimeError):
    """Raised when an R2 upload / presign fails, or the store is unconfigured."""


def is_configured() -> bool:
    """True only when every R2 credential is present. The feature is off
    (endpoint 503, no archiving) until an operator provisions the bucket."""
    return all(
        value.strip()
        for value in (
            settings.media_s3_endpoint,
            settings.media_s3_bucket,
            settings.media_s3_access_key,
            settings.media_s3_secret_key,
        )
    )


def _client():
    if not is_configured():
        raise MediaStoreError("media storage is not configured")
    try:
        import boto3
        from botocore.config import Config
    except ImportError as exc:  # pragma: no cover - boto3 is a hard dependency
        raise MediaStoreError("boto3 is not installed") from exc
    return boto3.client(
        "s3",
        endpoint_url=settings.media_s3_endpoint.strip(),
        aws_access_key_id=settings.media_s3_access_key.strip(),
        aws_secret_access_key=settings.media_s3_secret_key.strip(),
        region_name=settings.media_s3_region.strip() or "auto",
        # R2 requires SigV4; the virtual-hosted addressing style breaks against
        # a custom endpoint, so pin path-style.
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def upload_file(local_path: Path, object_key: str, content_type: str) -> None:
    """Upload ``local_path`` to ``object_key`` in the configured bucket."""
    client = _client()
    try:
        client.upload_file(
            str(local_path),
            settings.media_s3_bucket.strip(),
            object_key,
            ExtraArgs={"ContentType": content_type},
        )
    except Exception as exc:  # boto3/botocore raise a broad hierarchy
        raise MediaStoreError(f"R2 upload failed: {exc}") from exc


def generate_presigned_url(object_key: str, ttl_seconds: int | None = None) -> str:
    """Return a presigned GET URL for ``object_key`` valid for ``ttl_seconds``."""
    client = _client()
    ttl = ttl_seconds if ttl_seconds is not None else settings.media_presign_ttl_seconds
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.media_s3_bucket.strip(), "Key": object_key},
            ExpiresIn=int(ttl),
        )
    except Exception as exc:
        raise MediaStoreError(f"R2 presign failed: {exc}") from exc
