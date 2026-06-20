"""Worker-level end-to-end: Vast forced to fail → job completes via the
configured fallback transcription provider, and the transcript records it
(acceptance criteria #1 and #2 of issue #358).

Uses the real provider chain (build_provider_chain + transcribe_with_chain):
the only seams are the Vast GPU call (forced to raise) and the OpenAI HTTP POST
(mocked), so the fallback wiring itself is exercised, not stubbed out.
"""
from __future__ import annotations

import pytest

from scribe.db.models import Job, JobStatus, Transcript
from scribe.pipeline.downloader import DownloadResult


class _FakeResp:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = ""

    def json(self) -> dict:
        return self._payload


def test_vast_failure_completes_job_via_openai_fallback(db_session, monkeypatch, tmp_path):
    from scribe.config import settings
    from scribe.pipeline import summarizer as summarizer_module
    from scribe.pipeline import transcribe_providers, whisper_client
    from scribe.worker import loop as worker_loop

    transcribe_providers._reset_breakers_for_test()

    audio = tmp_path / "audio.m4a"
    audio.write_text("audio", encoding="utf-8")
    wav = tmp_path / "input-16k.wav"
    wav.write_bytes(b"RIFFfake-wav-bytes")

    # Opt into the fallback chain and give the hosted provider a key.
    monkeypatch.setattr(settings, "transcribe_providers", ["vast", "openai"])
    monkeypatch.setattr(settings, "openai_transcribe_api_key", "test-key")

    monkeypatch.setattr(
        worker_loop.downloader,
        "download_audio",
        lambda *_a, **_k: DownloadResult(
            audio_path=audio,
            title="fallback video",
            video_id="youtube:fallback",
            duration_seconds=60,
        ),
    )
    monkeypatch.setattr(worker_loop.ffmpeg, "to_wav_16k_mono", lambda *_a, **_k: wav)

    # Vast is forced to fail as if every offer is unavailable.
    def vast_down(*_a, **_k):
        raise whisper_client.WhisperError("no Vast instance became ready; last error: outage")

    monkeypatch.setattr(worker_loop.whisper_client, "transcribe", vast_down)

    # The hosted Whisper API answers successfully.
    def fake_post(url, **_kwargs):
        return _FakeResp(200, {"text": "fallback transcript text", "language": "en", "duration": 60.0})

    monkeypatch.setattr(transcribe_providers.httpx, "post", fake_post)

    monkeypatch.setattr(
        worker_loop.summarizer,
        "summarize",
        lambda *_a, **_k: summarizer_module.SummaryResult(summary_md="summary", tags=["x"]),
    )
    monkeypatch.setattr(worker_loop.shutil, "rmtree", lambda *_a, **_k: None)

    job = Job(
        url="https://youtu.be/fallback",
        video_id="pending:fallback",
        status=JobStatus.downloading,
    )
    db_session.add(job)
    db_session.commit()

    spend_before = _spend(transcribe_providers.metrics, provider="openai")
    worker_loop.process_job(db_session, job)

    db_session.refresh(job)
    transcript = db_session.query(Transcript).filter_by(job_id=job.id).one()
    assert job.status == JobStatus.done
    # Provider selection is recorded on the transcript (AC2).
    assert transcript.transcribe_provider == "openai"
    # vast_cost stays NULL for a non-Vast provider (daily cap is Vast-only).
    assert transcript.vast_cost is None
    assert "fallback transcript text" in transcript.transcript_md
    # Hosted spend is surfaced on its own metric line: 60s = 1min * $0.006.
    spend_after = _spend(transcribe_providers.metrics, provider="openai")
    assert spend_after - spend_before == pytest.approx(0.006)


def _spend(metrics_module, *, provider: str) -> float:
    counter = metrics_module.transcribe_provider_spend_usd_total
    for metric in counter.collect():
        for sample in metric.samples:
            if sample.name.endswith("_total") and sample.labels.get("provider") == provider:
                return float(sample.value)
    return 0.0
