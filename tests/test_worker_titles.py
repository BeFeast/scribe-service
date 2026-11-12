"""Persist bounded display titles and preserve raw captions across summary retries."""
import pytest

from scribe.db.models import Job, JobStatus, Transcript
from scribe.pipeline.downloader import DownloadResult
from scribe.pipeline.summary_validator import SummaryResult
from scribe.pipeline.titles import normalize_title
from scribe.worker import loop

RAW = "9.9K views · 61 reactions | " + "A long social caption about shared AI memory. " * 30 + "| Sergey Pimenov"


@pytest.mark.parametrize("summarize", [True, False])
def test_download_to_persisted_title(db_session, monkeypatch, tmp_path, summarize):
    from scribe.pipeline.whisper_client import TranscribeResult

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"audio")
    normalized, _ = normalize_title(RAW, author="Sergey Pimenov")
    monkeypatch.setattr(loop.downloader, "download_audio", lambda *a, **k: DownloadResult(
        audio_path=audio, title=normalized, original_title=RAW, video_id=f"facebook:title-{summarize}",
        duration_seconds=12, author_name="Sergey Pimenov",
    ))
    monkeypatch.setattr(loop.ffmpeg, "to_wav_16k_mono", lambda *a, **k: audio)

    def transcribe(*args, **kwargs):
        # Title is already safe while transcription is in progress.
        db_session.refresh(job)
        assert job.title == normalized
        assert job.original_title == RAW
        return TranscribeResult(transcript_md="spoken words", detected_language="en", duration_seconds=12,
                                backend="test", vast_instance_id=1, vast_cost=0.0)

    monkeypatch.setattr(loop.whisper_client, "transcribe", transcribe)

    def summary(*args, **kwargs):
        assert kwargs["title"] == normalized
        return SummaryResult(summary_md='---\nsource: "[[old-caption]]"\n---\n## Notion shared memory', tags=[])

    monkeypatch.setattr(loop.summarizer, "summarize", summary)
    job = Job(url="https://facebook.com/reel/example", video_id=f"pending:title-{summarize}",
              status=JobStatus.downloading, summarize=summarize)
    db_session.add(job)
    db_session.commit()
    loop.process_job(db_session, job)
    db_session.refresh(job)
    transcript = db_session.query(Transcript).filter_by(job_id=job.id).one()
    assert job.status == JobStatus.done
    assert job.title == transcript.title == ("Notion shared memory" if summarize else normalized)
    assert job.original_title == transcript.original_title == RAW
    if summarize:
        assert 'source: "[[notion-shared-memory]]"' in transcript.summary_md


@pytest.mark.parametrize("raw,heading,expected", [
    (RAW, "## Кратко\n## Память агентов", "Память агентов"),
    (RAW, "## Summary\nNo heading", normalize_title(RAW, author="Sergey Pimenov")[0]),
    ("Short title", "## Better heading", "Short title"),
])
def test_resume_legacy_partial(db_session, monkeypatch, raw, heading, expected):
    job = Job(url="https://facebook.com/reel/retry", video_id="titles:resume", status=JobStatus.failed, title=raw)
    db_session.add(job)
    db_session.flush()
    transcript = Transcript(job_id=job.id, video_id=job.video_id, title=raw, transcript_md="body",
                            author_name="Sergey Pimenov")
    db_session.add(transcript)
    db_session.commit()
    monkeypatch.setattr(loop.summarizer, "summarize", lambda *a, **k: SummaryResult(summary_md=heading, tags=[]))
    loop._summarize_and_finalize(db_session, job, transcript, raw, promoted=True)
    db_session.expire_all()
    assert transcript.title == job.title == expected
    assert transcript.original_title == (raw if expected != raw else None)
