"""A saved Whisper result can still have a healthy summary in flight."""
import pytest

from scribe.api.routes import _summary_state
from scribe.db.models import Job, JobStatus, Transcript


@pytest.mark.parametrize("status,summarize,summary,expected", [
    (JobStatus.transcribing, True, None, "generating"),
    (JobStatus.summarizing, True, None, "generating"),
    (JobStatus.queued, True, None, "generating"),
    (JobStatus.failed, True, None, "failed"),
    (JobStatus.done, False, None, "not_requested"),
    (JobStatus.failed, False, None, "not_requested"),
    (JobStatus.done, True, None, "unavailable"),
    (JobStatus.summarizing, True, "summary", "ready"),
])
def test_summary_state(status, summarize, summary, expected):
    transcript = Transcript(summary_md=summary)
    transcript.job = Job(status=status, summarize=summarize)
    assert _summary_state(transcript) == expected


def test_same_transcript_promotes_without_becoming_a_failure():
    transcript = Transcript(summary_md=None)
    transcript.job = Job(status=JobStatus.transcribing, summarize=True)
    assert _summary_state(transcript) == "generating"
    transcript.job.status = JobStatus.summarizing
    assert _summary_state(transcript) == "generating"
    transcript.summary_md = "Completed summary"
    transcript.job.status = JobStatus.done
    assert _summary_state(transcript) == "ready"


def test_library_and_detail_serialize_state_changes():
    import datetime as dt

    from scribe.api.routes import _brief, _full, _library_row

    transcript = Transcript(
        id=517, job_id=628, video_id="video517", title="Example",
        transcript_md="Saved transcript", summary_md=None,
        created_at=dt.datetime.now(dt.UTC),
    )
    transcript.job = Job(id=628, url="https://youtu.be/video517", status=JobStatus.summarizing, summarize=True)
    for serialize in (_brief, _full, _library_row):
        assert serialize(transcript).model_dump()["summary_state"] == "generating"
    transcript.summary_md = "Completed summary"
    transcript.job.status = JobStatus.done
    for serialize in (_brief, _full, _library_row):
        assert serialize(transcript).model_dump()["summary_state"] == "ready"
    assert _full(transcript).summary_md == "Completed summary"
    assert not _library_row(transcript).is_partial
