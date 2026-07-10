"""Media store (R2) config gating + offline presign (#408).

Pure tests: `is_configured` toggling, MediaStoreError when unconfigured, and a
real (offline, no network) presigned-URL signature with fake credentials.
"""
from __future__ import annotations

import pytest

from scribe.config import settings
from scribe.pipeline import media_store


@pytest.fixture()
def configured(monkeypatch):
    monkeypatch.setattr(settings, "media_s3_endpoint", "https://acct.r2.cloudflarestorage.com")
    monkeypatch.setattr(settings, "media_s3_bucket", "scribe-media")
    monkeypatch.setattr(settings, "media_s3_access_key", "AKIAFAKE")
    monkeypatch.setattr(settings, "media_s3_secret_key", "secretfake")
    monkeypatch.setattr(settings, "media_s3_region", "auto")
    monkeypatch.setattr(settings, "media_presign_ttl_seconds", 3600)


def test_is_configured_false_when_unset(monkeypatch):
    for field in ("media_s3_endpoint", "media_s3_bucket", "media_s3_access_key", "media_s3_secret_key"):
        monkeypatch.setattr(settings, field, "")
    assert media_store.is_configured() is False


def test_is_configured_false_when_partial(monkeypatch, configured):
    monkeypatch.setattr(settings, "media_s3_secret_key", "")
    assert media_store.is_configured() is False


def test_is_configured_true_when_all_set(configured):
    assert media_store.is_configured() is True


def test_upload_and_presign_raise_when_unconfigured(monkeypatch, tmp_path):
    for field in ("media_s3_endpoint", "media_s3_bucket", "media_s3_access_key", "media_s3_secret_key"):
        monkeypatch.setattr(settings, field, "")
    with pytest.raises(media_store.MediaStoreError):
        media_store.generate_presigned_url("media/x/1.mp4")
    with pytest.raises(media_store.MediaStoreError):
        media_store.upload_file(tmp_path / "nope.mp4", "media/x/1.mp4", "video/mp4")


def test_presigned_url_is_signed_offline(configured):
    url = media_store.generate_presigned_url("media/upload_abc/7.mp4", ttl_seconds=1200)
    # boto3 signs locally — no network. Path-style URL embeds bucket + key.
    assert url.startswith("https://acct.r2.cloudflarestorage.com/scribe-media/media/upload_abc/7.mp4")
    assert "X-Amz-Expires=1200" in url
    assert "X-Amz-Signature=" in url
