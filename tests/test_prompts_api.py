from __future__ import annotations

from fastapi.testclient import TestClient

from scribe.api import routes as routes_module
from scribe.db.models import Transcript
from scribe.main import app
from scribe.pipeline import prompts, summarizer


def _template(label: str) -> str:
    return (
        f"You are prompt {label}.\n\n"
        "## TL;DR\n\n"
        "Summarize the core claim.\n\n"
        "## Details\n\n"
        "Use {date} and {transcript_slug}.\n"
    )


def _seed_prompts(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(prompts, "PROMPTS_DIR", tmp_path)
    for version in prompts.VALID_VERSIONS:
        (tmp_path / f"transcript-summary.{version}.md").write_text(_template(version), encoding="utf-8")
    (tmp_path / "transcript-summary.active").write_text("v2\n", encoding="utf-8")


def test_prompts_list_fetch_write_and_switch_active(tmp_path, monkeypatch):
    _seed_prompts(tmp_path, monkeypatch)
    client = TestClient(app)

    listed = client.get("/api/prompts")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert body["active_version"] == "v2"
    assert [row["id"] for row in body["versions"]] == ["v1", "v2", "v3"]
    active = next(row for row in body["versions"] if row["id"] == "v2")
    assert active["is_active"] is True
    assert active["len_tokens_est"] > 0
    assert active["first_line"] == "You are prompt v2."

    fetched = client.get("/api/prompts/v1")
    assert fetched.status_code == 200, fetched.text
    assert fetched.headers["content-type"].startswith("text/markdown")
    assert "You are prompt v1." in fetched.text

    updated_body = _template("updated")
    written = client.post("/api/prompts/v3", json={"body": updated_body})
    assert written.status_code == 200, written.text
    assert written.json()["id"] == "v3"
    assert (tmp_path / "transcript-summary.v3.md").read_text(encoding="utf-8") == updated_body
    assert not (tmp_path / "transcript-summary.v3.md.tmp").exists()

    switched = client.post("/api/prompts/active", json={"version": "v3"})
    assert switched.status_code == 200, switched.text
    assert switched.json() == {"active_version": "v3"}
    assert (tmp_path / "transcript-summary.active").read_text(encoding="utf-8") == "v3\n"
    assert not (tmp_path / "transcript-summary.active.tmp").exists()


def test_prompt_write_validates_contract(tmp_path, monkeypatch):
    _seed_prompts(tmp_path, monkeypatch)
    client = TestClient(app)

    too_long = "## TL;DR\n\n" + ("x" * prompts.MAX_TEMPLATE_CHARS)
    resp = client.post("/api/prompts/v1", json={"body": too_long})
    assert resp.status_code == 422
    assert "16" in resp.text

    missing_tldr = client.post("/api/prompts/v1", json={"body": "## Details\n\nBody"})
    assert missing_tldr.status_code == 422
    assert "TL;DR" in missing_tldr.text


def test_prompt_dry_run_uses_requested_version_without_persisting(tmp_path, monkeypatch, caplog):
    _seed_prompts(tmp_path, monkeypatch)
    transcript = Transcript(
        id=142,
        job_id=1,
        video_id="video123",
        title="Dry Run",
        transcript_md="Transcript body",
        summary_md="existing summary",
        tags=["old"],
    )

    class FakeSession:
        def __init__(self) -> None:
            self.committed = False

        def get(self, model, ident):
            assert model is Transcript
            assert ident == 142
            return transcript

        def commit(self) -> None:
            self.committed = True

    fake_session = FakeSession()

    def _fake_summarize(transcript_md, *, title, prompt_version=None, lock_timeout=None, **_):
        assert transcript_md == "Transcript body"
        assert title == "Dry Run"
        assert prompt_version == "v3"
        assert lock_timeout == routes_module._RESUMMARIZE_LOCK_TIMEOUT_S
        return summarizer.SummaryResult(summary_md="dry summary", tags=["dry", "run"])

    def _fake_get_session():
        yield fake_session

    monkeypatch.setattr(summarizer, "summarize", _fake_summarize)
    app.dependency_overrides[routes_module.get_session] = _fake_get_session
    try:
        caplog.set_level("INFO", logger="scribe.api")
        client = TestClient(app)
        resp = client.post("/api/prompts/dry-run", json={"version": "v3", "transcript_id": 142})
    finally:
        app.dependency_overrides.pop(routes_module.get_session, None)

    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "version": "v3",
        "transcript_id": 142,
        "summary_md": "dry summary",
        "tags": ["dry", "run"],
    }
    assert transcript.summary_md == "existing summary"
    assert transcript.tags == ["old"]
    assert fake_session.committed is False
    assert any(record.message == "prompt_dry_run" for record in caplog.records)


def test_prompts_are_in_openapi(tmp_path, monkeypatch):
    _seed_prompts(tmp_path, monkeypatch)
    client = TestClient(app)
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/prompts" in paths
    assert "/api/prompts/{version}" in paths
    assert "/api/prompts/active" in paths
    assert "/api/prompts/dry-run" in paths
