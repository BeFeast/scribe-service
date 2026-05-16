"""Prompt-template API tests."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scribe.api import routes as routes_module
from scribe.db.models import Job, JobStatus, Transcript
from scribe.main import app
from scribe.pipeline import prompts
from scribe.pipeline import summarizer as summarizer_module

PROMPT_BODY = """You are a test summarizer.

## TL;DR

Write a short summary.

## Details

Use {date} and {transcript_slug}.
"""


@pytest.fixture()
def prompt_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    for version in prompts.PROMPT_VERSIONS:
        (tmp_path / f"transcript-summary.{version}.md").write_text(
            PROMPT_BODY.replace("test summarizer", f"test summarizer {version}"),
            encoding="utf-8",
        )
    (tmp_path / "transcript-summary.active").write_text("v3\n", encoding="utf-8")
    monkeypatch.setattr(prompts.settings, "prompt_dir", str(tmp_path))
    return tmp_path


@pytest.fixture()
def pure_client(prompt_dir):
    with TestClient(app) as tc:
        yield tc


@pytest.fixture()
def db_client(db_session, prompt_dir):
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.pop(routes_module.get_session, None)


def _seed_done_transcript(session) -> Transcript:
    job = Job(url="https://youtu.be/promptdry12", video_id="promptdry12", status=JobStatus.done)
    session.add(job)
    session.flush()
    transcript = Transcript(
        job_id=job.id,
        video_id=job.video_id,
        title="Prompt Dry Run",
        transcript_md="Transcript body",
        summary_md="Original summary",
        tags=["old"],
    )
    session.add(transcript)
    session.commit()
    return transcript


def test_list_prompts(pure_client):
    resp = pure_client.get("/api/prompts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["active_version"] == "v3"
    assert [row["id"] for row in body["versions"]] == ["v1", "v2", "v3"]
    active = [row for row in body["versions"] if row["is_active"]]
    assert len(active) == 1
    assert active[0]["id"] == "v3"
    assert all(row["len_chars"] > 0 and row["len_tokens_est"] > 0 for row in body["versions"])


def test_fetch_prompt_version(pure_client):
    resp = pure_client.get("/api/prompts/v2")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/markdown")
    assert "test summarizer v2" in resp.text


def test_write_prompt_version_is_atomic_and_validates(pure_client, prompt_dir):
    new_body = PROMPT_BODY.replace("short summary", "focused summary")
    resp = pure_client.post("/api/prompts/v1", json={"body": new_body})
    assert resp.status_code == 204, resp.text
    assert (prompt_dir / "transcript-summary.v1.md").read_text(encoding="utf-8") == new_body
    assert not (prompt_dir / "transcript-summary.v1.md.tmp").exists()

    invalid = pure_client.post("/api/prompts/v1", json={"body": "## Details\nmissing required header"})
    assert invalid.status_code == 422
    assert "TL;DR" in invalid.text


def test_switch_active_prompt(pure_client, prompt_dir):
    resp = pure_client.post("/api/prompts/active", json={"version": "v1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["active_version"] == "v1"
    assert (prompt_dir / "transcript-summary.active").read_text(encoding="utf-8") == "v1\n"
    assert not (prompt_dir / "transcript-summary.active.tmp").exists()


def test_dry_run_prompt_does_not_persist(db_client, db_session, monkeypatch):
    transcript = _seed_done_transcript(db_session)

    def fake_summarize(transcript_md, *, title, lock_timeout=None, prompt_version=None, **_):
        assert transcript_md == "Transcript body"
        assert title == "Prompt Dry Run"
        assert prompt_version == "v2"
        assert lock_timeout == routes_module._RESUMMARIZE_LOCK_TIMEOUT_S
        return summarizer_module.SummaryResult(summary_md="Dry-run summary", tags=["new"])

    log_events = []

    def fake_log_info(message, *args, **kwargs):
        log_events.append((message, kwargs.get("extra")))

    monkeypatch.setattr(summarizer_module, "summarize", fake_summarize)
    monkeypatch.setattr(routes_module.log, "info", fake_log_info)
    resp = db_client.post("/api/prompts/dry-run", json={"version": "v2", "transcript_id": transcript.id})

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "version": "v2",
        "transcript_id": transcript.id,
        "summary_md": "Dry-run summary",
        "tags": ["new"],
    }
    db_session.refresh(transcript)
    assert transcript.summary_md == "Original summary"
    assert transcript.tags == ["old"]
    assert log_events == [
        (
            "prompt_dry_run",
            {"prompt_version": "v2", "transcript_id": transcript.id, "video_id": "promptdry12"},
        )
    ]


def test_prompts_are_in_openapi(pure_client):
    resp = pure_client.get("/openapi.json")
    assert resp.status_code == 200
    paths = resp.json()["paths"]
    assert "/api/prompts" in paths
    assert "/api/prompts/{version}" in paths
    assert "/api/prompts/dry-run" in paths
