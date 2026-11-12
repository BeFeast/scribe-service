"""Copy for AI: the `ai_transcript` share kind (#489).

The first half tests the pure rendering helpers in `scribe.api.ai_share`
(no DB). The second half drives `/api/transcripts/{id}/share-links` and
`/share/{token}` end to end against the test Postgres.
"""
from __future__ import annotations

import datetime as dt
import re
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from scribe.api import ai_share
from scribe.api import routes as routes_module
from scribe.config import settings
from scribe.db.models import Job, JobStatus, Owner, Transcript, TranscriptShareLink, User
from scribe.main import app

SHARE_HEADERS = {
    "x-robots-tag": "noindex, nofollow, noarchive",
    "cache-control": "no-store",
    "access-control-allow-origin": "*",
    "x-content-type-options": "nosniff",
    "referrer-policy": "no-referrer",
}

TRANSCRIPT_MD = (
    "# Interview With A Guest\n\n"
    "## Metadata\n"
    "- Source URL: https://youtu.be/aishare1111\n"
    "- Source audio: /var/tmp/scribe/job-77/audio.m4a\n"
    "- Transcription model: faster-whisper (large-v3-turbo, float16, cuda)\n"
    "- Detected language: en\n"
    "- Language probability: 0.998\n"
    "- Duration: 3723.00s\n\n"
    "## Transcript\n\n"
    "Hello and welcome. Today we talk about systems.\n"
)


def _fake(**overrides):
    transcript = dict(
        title="Interview With A Guest",
        video_id="aishare1111",
        source_platform="youtube",
        author_name="Jane Doe",
        author_handle="@janedoe",
        duration_seconds=3723,
        lang="en",
        created_at=dt.datetime(2026, 11, 1, 10, 30, tzinfo=dt.UTC),
    )
    job_url = overrides.pop("job_url", "https://youtu.be/aishare1111")
    transcript.update(overrides)
    return SimpleNamespace(**transcript), SimpleNamespace(url=job_url)


def _fields(transcript, job, **kwargs) -> dict[str, str]:
    kwargs.setdefault("length", 1000)
    return dict(ai_share.ai_header_fields(transcript, job, **kwargs))


# ─── Pure rendering ──────────────────────────────────────────────────────────


def test_header_full_metadata_order_and_values():
    transcript, job = _fake()
    fields = ai_share.ai_header_fields(transcript, job, length=12_345)

    assert [key for key, _ in fields] == [
        "Title",
        "Source",
        "Author",
        "Platform",
        "Duration",
        "Language",
        "Transcribed",
        "Length",
        "Timestamps",
        "Note",
    ]
    values = dict(fields)
    assert values["Title"] == "Interview With A Guest"
    assert values["Source"] == "https://youtu.be/aishare1111"
    assert values["Author"] == "Jane Doe (@janedoe)"
    assert values["Platform"] == "youtube"
    assert values["Duration"] == "1:02:03"
    assert values["Language"] == "en"
    assert values["Transcribed"] == "2026-11-01"
    assert values["Length"] == "12,345 characters, ~3,086 tokens"
    assert values["Timestamps"] == "none"
    assert values["Note"] == (
        "This is a raw automatic speech-to-text transcript with no summary or editing; "
        "names and terms may be misrecognized. Use it as source material."
    )


def test_header_sparse_metadata_omits_empty_fields():
    transcript, job = _fake(
        source_platform=None,
        author_name=None,
        author_handle=None,
        duration_seconds=None,
        lang=None,
        created_at=None,
        job_url="tg:AgACAgIAAxkBAAIBY2Zfile",
    )
    fields = ai_share.ai_header_fields(transcript, job, length=10)

    assert [key for key, _ in fields] == ["Title", "Length", "Timestamps", "Note"]
    assert all("None" not in value for _, value in fields)


def test_header_author_falls_back_to_whichever_exists():
    assert _fields(*_fake(author_handle=None))["Author"] == "Jane Doe"
    assert _fields(*_fake(author_name=None))["Author"] == "@janedoe"
    assert _fields(*_fake(duration_seconds=0)).get("Duration") is None


def test_header_platform_falls_back_to_source_label():
    fields = _fields(*_fake(source_platform=None, job_url="https://x.com/jane/status/1"))
    assert fields["Platform"] == "Twitter/X"


def test_header_collapses_newlines_in_values():
    fields = _fields(*_fake(title="Line one\nLine two\r\n  three"))
    assert fields["Title"] == "Line one Line two three"
    document = ai_share.render_ai_document(list(fields.items()), "body")
    assert document.splitlines()[0] == "Title: Line one Line two three"


def test_render_document_separator_is_exact():
    transcript, job = _fake()
    fields = ai_share.ai_header_fields(transcript, job, length=4)
    document = ai_share.render_ai_document(fields, "body")

    header, body = document.split("\n\n---\n\n")
    assert header.splitlines()[-1].startswith("Note: ")
    assert body == "body\n"
    assert document.count("\n---\n") == 1


def test_body_strips_title_and_metadata():
    body = ai_share.ai_transcript_body(TRANSCRIPT_MD)

    assert body == "Hello and welcome. Today we talk about systems."
    for leaked in ("Source audio", "Language probability", "Source URL", "Transcription model", "# Interview"):
        assert leaked not in body


def test_body_legacy_without_transcript_marker():
    legacy = (
        "# Legacy Title\n\n"
        "## Metadata\n"
        "- Source audio: /tmp/a.m4a\n"
        "- Language probability: 0.5\n\n"
        "Spoken words here."
    )
    assert ai_share.ai_transcript_body(legacy) == "Spoken words here."
    assert ai_share.ai_transcript_body("Just text.") == "Just text."
    assert ai_share.ai_transcript_body(None) == ""


def test_source_upload_prints_uploaded_file():
    transcript, job = _fake(
        source_platform="upload",
        video_id="upload:0123456789abcdef",
        job_url="upload:secret-board-meeting.mp4",
    )
    fields = ai_share.ai_header_fields(transcript, job, length=4)
    document = ai_share.render_ai_document(fields, "body")

    assert dict(fields)["Source"] == "uploaded file"
    assert "Platform" not in dict(fields)
    assert "upload:" not in document
    assert "secret-board-meeting" not in document


def test_source_upload_detected_by_job_url_or_video_id():
    assert _fields(*_fake(source_platform=None, job_url="upload:x.mp4"))["Source"] == "uploaded file"
    assert _fields(*_fake(source_platform=None, video_id="upload:abc", job_url=None))["Source"] == "uploaded file"


def test_source_telegram_file_reference_is_omitted():
    fields = _fields(*_fake(source_platform="telegram", video_id="telegram:abc", job_url="tg:AgACAgIAAxk"))
    assert "Source" not in fields
    assert fields["Platform"] == "telegram"


@pytest.mark.parametrize(
    "url",
    [
        "https://t.me/c/1234567890/42",
        "https://telegram.me/c/1234567890/42",
        "https://www.t.me/c/99/1",
    ],
)
def test_source_telegram_private_chat_hides_url(url):
    transcript, job = _fake(source_platform="telegram", job_url=url)
    fields = ai_share.ai_header_fields(transcript, job, length=4)
    document = ai_share.render_ai_document(fields, "body")

    assert dict(fields)["Source"] == "Telegram (private chat)"
    assert "/c/" not in document
    assert "1234567890" not in document


def test_source_public_urls_are_printed():
    assert _fields(*_fake())["Source"] == "https://youtu.be/aishare1111"
    assert _fields(*_fake(job_url="https://t.me/publicchannel/7"))["Source"] == "https://t.me/publicchannel/7"
    assert "Source" not in _fields(*_fake(job_url="ftp://example.com/file.mp3"))


def test_header_part_lines():
    transcript, job = _fake()
    middle = _fields(transcript, job, part=2, parts=3, next_url="https://s.example/share/t?part=3")
    last = _fields(transcript, job, part=3, parts=3, next_url=None)
    listing = ai_share.ai_header_fields(
        transcript, job, length=10, parts=2, part_urls=["https://s/p?part=1", "https://s/p?part=2"]
    )

    assert middle["Part"] == "2 of 3"
    assert middle["Next part"] == "https://s.example/share/t?part=3"
    assert last["Part"] == "3 of 3"
    assert "Next part" not in last
    keys = [key for key, _ in listing]
    assert "Part" not in keys
    assert dict(listing)["Parts available"] == "2"
    assert dict(listing)["Part 1 of 2"] == "https://s/p?part=1"
    assert dict(listing)["Part 2 of 2"] == "https://s/p?part=2"
    assert keys.index("Parts available") < keys.index("Part 1 of 2") < keys.index("Timestamps")
    assert "Parts available" not in _fields(transcript, job)


def _words(chunks: list[str]) -> list[str]:
    return " ".join(chunks).split()


def test_chunker_short_body_is_one_chunk():
    assert ai_share.chunk_ai_body("Short body.", limit=100) == ["Short body."]
    assert ai_share.chunk_ai_body("x" * 100, limit=100) == ["x" * 100]


def test_chunker_long_single_line_cuts_after_sentences():
    sentence = "The quick brown fox jumps over the lazy dog again and again. "
    body = (sentence * 4200).strip()  # ~250k characters, one line
    assert len(body) > 250_000

    chunks = ai_share.chunk_ai_body(body, limit=ai_share.AI_PART_CHARS)

    assert len(chunks) == 3
    assert all(len(chunk) <= ai_share.AI_PART_CHARS for chunk in chunks)
    assert all(chunk.endswith(".") for chunk in chunks)
    assert _words(chunks) == body.split()
    assert chunks == ai_share.chunk_ai_body(body, limit=ai_share.AI_PART_CHARS)


def test_chunker_without_sentence_ends_cuts_at_whitespace():
    words = [f"word{i}" for i in range(5000)]
    body = " ".join(words)

    chunks = ai_share.chunk_ai_body(body, limit=1000)

    assert len(chunks) > 1
    assert all(len(chunk) <= 1000 for chunk in chunks)
    assert _words(chunks) == words
    for chunk in chunks:
        assert chunk == chunk.strip()
        assert all(re.fullmatch(r"word\d+", token) for token in chunk.split())


def test_chunker_prefers_sentence_end_only_past_half_window():
    # Sentence end at 10% of the window: whitespace near the edge wins.
    body = "Tiny. " + " ".join(["filler"] * 400)
    chunks = ai_share.chunk_ai_body(body, limit=200)
    assert len(chunks[0]) > 100
    assert _words(chunks) == body.split()


def test_chunker_cjk_sentence_enders_need_no_whitespace():
    body = "你好世界这是测试。" * 50
    chunks = ai_share.chunk_ai_body(body, limit=100)

    assert all(len(chunk) <= 100 for chunk in chunks)
    assert all(chunk.endswith("。") for chunk in chunks)
    assert "".join(chunks) == body


def test_chunker_closing_quotes_stay_with_sentence():
    body = ('He said "stop." ' * 40).strip()
    chunks = ai_share.chunk_ai_body(body, limit=100)
    assert all(chunk.endswith('."') for chunk in chunks)
    assert _words(chunks) == body.split()


def test_chunker_hard_cuts_only_without_whitespace():
    body = "x" * 250
    chunks = ai_share.chunk_ai_body(body, limit=100)
    assert chunks == ["x" * 100, "x" * 100, "x" * 50]


# ─── HTTP / DB ───────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_tables(request):
    if "db_session" not in request.fixturenames:
        yield
        return
    session = request.getfixturevalue("db_session")

    def wipe():
        session.execute(delete(TranscriptShareLink))
        session.execute(delete(Job))
        session.execute(delete(User))
        session.execute(delete(Owner))
        session.commit()

    wipe()
    yield
    wipe()


@pytest.fixture()
def client(db_session, monkeypatch):
    monkeypatch.setattr(settings, "config_api_bearer_token", "")
    monkeypatch.setattr(settings, "auth_test_mode", True)
    app.dependency_overrides[routes_module.get_session] = lambda: db_session
    with TestClient(app) as tc:
        yield tc
    app.dependency_overrides.pop(routes_module.get_session, None)


def _auth_headers(subject: str) -> dict[str, str]:
    return {"x-scribe-test-clerk-sub": subject, "x-scribe-test-email": f"{subject}@example.test"}


def _seed_user(session, subject: str) -> User:
    email = f"{subject}@example.test"
    user = User(
        owner=Owner(display_name=email),
        clerk_subject=subject,
        primary_email=email,
        display_name=email,
        role="user",
    )
    session.add(user)
    session.commit()
    return user


def _seed_transcript(
    session,
    *,
    video_id: str = "aishare1111",
    url: str | None = None,
    title: str = "Interview With A Guest",
    transcript_md: str = TRANSCRIPT_MD,
    summary_md: str | None = "summary",
    source_platform: str | None = "youtube",
    owner_subject: str = "user_a",
) -> Transcript:
    job = Job(
        url=url or f"https://youtu.be/{video_id}",
        video_id=video_id,
        status=JobStatus.done,
        title=title,
        owner_subject=owner_subject,
    )
    session.add(job)
    session.flush()
    transcript = Transcript(
        job_id=job.id,
        video_id=video_id,
        title=title,
        transcript_md=transcript_md,
        summary_md=summary_md,
        duration_seconds=3723,
        lang="en",
        author_name="Jane Doe",
        author_handle="@janedoe",
        source_platform=source_platform,
        transcribe_provider="vast",
        vast_cost=0.0123,
        owner_subject=owner_subject,
        owner_email=f"{owner_subject}@example.test",
    )
    session.add(transcript)
    session.commit()
    return transcript


def _create(client, transcript_id: int, *, kind: str = "ai_transcript", subject: str = "user_a", **extra):
    return client.post(
        f"/api/transcripts/{transcript_id}/share-links",
        headers=_auth_headers(subject),
        json={"target_kind": kind, "label": "copy-for-ai", **extra},
    )


def _path(created: dict) -> str:
    return "/share/" + created["token"]


def _assert_share_headers(response) -> None:
    for key, value in SHARE_HEADERS.items():
        assert response.headers.get(key) == value, (key, response.status_code)
    assert "access-control-allow-credentials" not in response.headers


@pytest.fixture()
def ai_link(client, db_session):
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(db_session)
    created = _create(client, transcript.id)
    assert created.status_code == 201, created.text
    return transcript, created.json()


def test_create_ai_transcript_returns_share_url_md(client, db_session, ai_link):
    transcript, created = ai_link

    assert created["target_kind"] == "ai_transcript"
    assert created["share_url"].endswith("/share/" + created["token"])
    assert created["share_url_md"] == created["share_url"] + ".md"
    assert created["access_count"] == 0
    assert created["last_accessed_at"] is None

    page = _create(client, transcript.id, kind="page").json()
    assert page["share_url_md"] is None

    unknown = _create(client, transcript.id, kind="ai_summary_bogus")
    assert unknown.status_code == 422
    assert "ai_transcript" in unknown.json()["detail"]


def test_create_uses_public_base_url_when_configured(client, db_session, ai_link, monkeypatch):
    transcript, _ = ai_link
    monkeypatch.setattr(settings, "public_base_url", "https://scribe.example/")

    created = _create(client, transcript.id).json()

    assert created["share_url"] == "https://scribe.example/share/" + created["token"]
    assert created["share_url_md"] == "https://scribe.example/share/" + created["token"] + ".md"


def test_get_ai_transcript_plain_text(client, ai_link):
    _, created = ai_link

    response = client.get(_path(created))

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    header, body = response.text.split("\n\n---\n\n")
    lines = header.splitlines()
    assert lines[0] == "Title: Interview With A Guest"
    assert "Source: https://youtu.be/aishare1111" in lines
    assert "Author: Jane Doe (@janedoe)" in lines
    assert "Duration: 1:02:03" in lines
    assert "Timestamps: none" in lines
    assert lines[-1].startswith("Note: This is a raw automatic speech-to-text transcript")
    assert body == "Hello and welcome. Today we talk about systems.\n"
    for leaked in ("Source audio", "## Metadata", "Language probability", "vast", "0.0123", "user_a"):
        assert leaked not in response.text
    _assert_share_headers(response)


def test_suffix_variants_share_body_with_their_content_types(client, ai_link):
    _, created = ai_link

    plain = client.get(_path(created))
    markdown = client.get(_path(created) + ".md")
    text = client.get(_path(created) + ".txt")

    assert markdown.status_code == 200
    assert text.status_code == 200
    assert markdown.headers["content-type"] == "text/markdown; charset=utf-8"
    assert text.headers["content-type"] == "text/plain; charset=utf-8"
    assert plain.text == markdown.text == text.text


@pytest.mark.parametrize("kind", ["page", "summary_markdown", "transcript_markdown"])
def test_suffixes_404_for_other_kinds(client, db_session, kind):
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(db_session)
    created = _create(client, transcript.id, kind=kind).json()

    assert client.get(_path(created)).status_code == 200
    for suffix in (".md", ".txt"):
        response = client.get(_path(created) + suffix)
        assert response.status_code == 404
        _assert_share_headers(response)


def test_other_kinds_ignore_part(client, db_session):
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(db_session)
    created = _create(client, transcript.id, kind="transcript_markdown").json()

    response = client.get(_path(created) + "?part=7")

    assert response.status_code == 200
    assert response.text == TRANSCRIPT_MD


def test_share_headers_on_every_status(client, db_session, ai_link):
    transcript, created = ai_link
    revoked = _create(client, transcript.id).json()
    client.post(f"/api/share-links/{revoked['id']}/revoke", headers=_auth_headers("user_a"))
    expired = _create(
        client,
        transcript.id,
        expires_at=(dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1)).isoformat(),
    ).json()

    cases = [
        (_path(created), 200),
        ("/share/unknown-token", 404),
        (_path(revoked), 410),
        (_path(expired), 410),
        (_path(created) + "?part=9", 404),
        (_path(created) + "?part=abc", 422),
    ]
    for path, status in cases:
        response = client.get(path)
        assert response.status_code == status, path
        _assert_share_headers(response)


def test_options_preflight_on_share(client, ai_link):
    _, created = ai_link
    response = client.options(_path(created) + ".md", headers={"Origin": "https://chat.example"})

    assert response.status_code == 204
    _assert_share_headers(response)
    assert "HEAD" in response.headers["access-control-allow-methods"]


def test_share_headers_not_applied_outside_share(client):
    response = client.get("/healthz")
    assert "x-robots-tag" not in response.headers


def test_head_matches_get_without_body(client, db_session, ai_link):
    transcript, created = ai_link
    revoked = _create(client, transcript.id).json()
    client.post(f"/api/share-links/{revoked['id']}/revoke", headers=_auth_headers("user_a"))

    for path in (
        _path(created),
        _path(created) + ".md",
        _path(created) + ".txt",
        _path(created) + "?part=1",
        _path(created) + "?part=5",
        "/share/unknown-token",
        "/share/unknown-token.md",
        _path(revoked),
        _path(revoked) + ".txt",
    ):
        get = client.get(path)
        head = client.head(path)
        assert head.status_code == get.status_code, path
        assert head.status_code != 405
        assert head.content == b""
        assert head.headers["content-type"] == get.headers["content-type"], path
        assert head.headers["content-length"] == str(len(get.content)), path
        _assert_share_headers(head)

    link = db_session.get(TranscriptShareLink, created["id"])
    db_session.refresh(link)
    # Only the four successful GETs above counted (plain, .md, .txt, part=1);
    # HEAD never does.
    assert link.access_count == 4


@pytest.mark.parametrize("variant", ["", ".md", ".txt", "?part=1", ".md?part=1"])
def test_revoked_expired_unknown_across_variants(client, db_session, ai_link, variant):
    transcript, _ = ai_link
    revoked = _create(client, transcript.id).json()
    client.post(f"/api/share-links/{revoked['id']}/revoke", headers=_auth_headers("user_a"))
    expired = _create(
        client,
        transcript.id,
        expires_at=(dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)).isoformat(),
    ).json()

    revoked_resp = client.get(_path(revoked) + variant)
    expired_resp = client.get(_path(expired) + variant)
    unknown_resp = client.get("/share/doesnotexist" + variant)

    assert revoked_resp.status_code == 410
    assert expired_resp.status_code == 410
    assert unknown_resp.status_code == 404
    for response in (revoked_resp, expired_resp, unknown_resp):
        assert "Hello and welcome" not in response.text


def test_upload_transcript_hides_raw_url(client, db_session):
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(
        db_session,
        video_id="upload:0123456789abcdef",
        url="upload:quarterly-board-meeting.mp4",
        title="Quarterly review",
        source_platform="upload",
        transcript_md=TRANSCRIPT_MD.replace(
            "https://youtu.be/aishare1111", "upload:quarterly-board-meeting.mp4"
        ),
    )
    created = _create(client, transcript.id).json()

    response = client.get(_path(created) + ".md")

    assert response.status_code == 200
    assert "Source: uploaded file" in response.text.splitlines()
    assert "upload:" not in response.text
    assert "quarterly-board-meeting" not in response.text
    assert not any(line.startswith("Platform:") for line in response.text.splitlines())


def _long_transcript_md(sentences: int) -> str:
    body = " ".join(f"Sentence number {i} talks about topic {i % 7}." for i in range(sentences))
    return f"# Long\n\n## Metadata\n- Source audio: /tmp/x.m4a\n\n## Transcript\n\n{body}\n"


def _split_document(text: str) -> tuple[dict[str, str], str]:
    header, body = text.split("\n\n---\n\n", 1)
    fields = dict(line.split(": ", 1) for line in header.splitlines())
    return fields, body.removesuffix("\n")


def test_long_transcript_parts_with_small_limit(client, db_session, monkeypatch):
    monkeypatch.setattr(ai_share, "AI_PART_CHARS", 500)
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(db_session, transcript_md=_long_transcript_md(60))
    created = _create(client, transcript.id).json()
    full_body = ai_share.ai_transcript_body(transcript.transcript_md)
    expected_parts = len(ai_share.chunk_ai_body(full_body))
    assert expected_parts > 2

    whole = client.get(_path(created) + ".md")
    fields, body = _split_document(whole.text)
    assert body == full_body
    assert fields["Parts available"] == str(expected_parts)
    assert fields["Length"].startswith(f"{len(full_body):,} characters")
    assert "Part" not in fields
    for index in range(1, expected_parts + 1):
        url = fields[f"Part {index} of {expected_parts}"]
        assert url.endswith(f"/share/{created['token']}.md?part={index}")

    plain_fields, _ = _split_document(client.get(_path(created)).text)
    assert plain_fields[f"Part 1 of {expected_parts}"].endswith(f"/share/{created['token']}?part=1")

    served_words: list[str] = []
    for index in range(1, expected_parts + 1):
        response = client.get(_path(created) + f".md?part={index}")
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        part_fields, part_body = _split_document(response.text)
        assert len(part_body) <= 500
        assert part_body.endswith(".")
        assert part_fields["Part"] == f"{index} of {expected_parts}"
        assert part_fields["Length"].startswith(f"{len(part_body):,} characters")
        assert "Parts available" not in part_fields
        if index < expected_parts:
            assert part_fields["Next part"].endswith(f"/share/{created['token']}.md?part={index + 1}")
        else:
            assert "Next part" not in part_fields
        served_words.extend(part_body.split())
    assert served_words == full_body.split()

    assert client.get(_path(created) + f"?part={expected_parts + 1}").status_code == 404
    assert client.get(_path(created) + "?part=0").status_code == 404
    assert client.get(_path(created) + "?part=-1").status_code == 404


def test_long_transcript_at_real_limit(client, db_session):
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(db_session, transcript_md=_long_transcript_md(6000))
    full_body = ai_share.ai_transcript_body(transcript.transcript_md)
    assert len(full_body) > 2 * ai_share.AI_PART_CHARS
    created = _create(client, transcript.id).json()

    fields, body = _split_document(client.get(_path(created)).text)
    assert body == full_body
    parts = int(fields["Parts available"])
    assert parts == 3

    for index in range(1, parts + 1):
        _, part_body = _split_document(client.get(_path(created) + f"?part={index}").text)
        assert len(part_body) <= ai_share.AI_PART_CHARS
    assert client.get(_path(created) + f"?part={parts + 1}").status_code == 404


def test_short_transcript_has_no_part_lines(client, ai_link):
    _, created = ai_link
    fields, _ = _split_document(client.get(_path(created)).text)
    assert not any(key.startswith("Part") for key in fields)
    one = client.get(_path(created) + "?part=1")
    assert one.status_code == 200
    assert _split_document(one.text)[0]["Part"] == "1 of 1"


def test_partial_transcript_can_create_and_serve(client, db_session):
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(db_session, summary_md=None)

    created = _create(client, transcript.id)
    assert created.status_code == 201, created.text
    response = client.get(_path(created.json()))

    assert response.status_code == 200
    assert "Hello and welcome" in response.text


def test_access_count_tracks_successful_gets(client, db_session, ai_link):
    transcript, created = ai_link

    for _ in range(3):
        assert client.get(_path(created)).status_code == 200
    assert client.get(_path(created) + "?part=2").status_code == 404
    client.head(_path(created))

    listed = client.get(f"/api/transcripts/{transcript.id}/share-links", headers=_auth_headers("user_a"))
    assert listed.status_code == 200
    view = next(row for row in listed.json() if row["id"] == created["id"])
    assert view["access_count"] == 3
    assert view["last_accessed_at"] is not None
    assert dt.datetime.fromisoformat(view["last_accessed_at"]) <= dt.datetime.now(dt.UTC) + dt.timedelta(minutes=1)


def test_access_count_counts_other_kinds(client, db_session):
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(db_session)
    created = _create(client, transcript.id, kind="summary_markdown").json()

    client.get(_path(created))
    client.get(_path(created) + ".md")  # 404, not counted

    link = db_session.get(TranscriptShareLink, created["id"])
    db_session.refresh(link)
    assert link.access_count == 1


def test_owner_scoping_for_ai_links(client, db_session, ai_link):
    transcript, created = ai_link
    _seed_user(db_session, "user_b")

    other_create = _create(client, transcript.id, subject="user_b")
    other_list = client.get(f"/api/transcripts/{transcript.id}/share-links", headers=_auth_headers("user_b"))
    other_revoke = client.post(f"/api/share-links/{created['id']}/revoke", headers=_auth_headers("user_b"))

    assert other_create.status_code == 404
    assert other_list.status_code == 404
    assert other_revoke.status_code == 404
    assert client.get(_path(created)).status_code == 200


def test_machine_bearer_can_create_ai_link(client, db_session, monkeypatch):
    monkeypatch.setattr(settings, "machine_bearer_token", "machine-token")
    transcript = _seed_transcript(db_session, owner_subject="someone")

    created = client.post(
        f"/api/transcripts/{transcript.id}/share-links",
        headers={"Authorization": "Bearer machine-token"},
        json={"target_kind": "ai_transcript", "label": "agent:test-bot"},
    )

    assert created.status_code == 201, created.text
    body = created.json()
    assert body["label"] == "agent:test-bot"
    md_path = body["share_url_md"].split("://", 1)[1].split("/", 1)[1]
    assert client.get("/" + md_path).status_code == 200


def test_revoke_through_api_then_410(client, ai_link):
    _, created = ai_link
    assert client.get(_path(created)).status_code == 200

    revoked = client.post(f"/api/share-links/{created['id']}/revoke", headers=_auth_headers("user_a"))

    assert revoked.status_code == 200
    assert revoked.json()["revoked_at"] is not None
    assert client.get(_path(created)).status_code == 410
    assert client.get(_path(created) + ".md").status_code == 410


def test_robots_txt_does_not_disallow_share(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /share" not in response.text
    assert "Disallow: /\n" not in response.text


# --- post caption + empty transcript (2026-11-12, transcript 722) -----------

CAPTION = (
    "9.9K views · 61 reactions | 4 ноября Notion дала агентам общий мозг. "
    "ChatGPT, Claude и Bot сохраняют то, что узнали, в одну память в Notion. | Jane Doe"
)
EMPTY_MD = "# Reel\n\n## Metadata\n- Source audio: Vast remote yt-dlp/ffmpeg pipeline\n\n## Transcript\n\n\n"


def _reel(session, **kw):
    transcript = _seed_transcript(
        session, video_id="facebook:1", title="Notion как общая память",
        transcript_md=kw.pop("transcript_md", EMPTY_MD), source_platform="facebook", **kw,
    )
    transcript.original_title = CAPTION
    session.commit()
    return transcript


def test_reel_serves_post_caption_and_marks_empty_transcript(client, db_session):
    _seed_user(db_session, "user_a")
    created = _create(client, _reel(db_session).id).json()

    text = client.get(_path(created) + ".md").text
    _, body = text.split("\n\n---\n\n", 1)

    assert body.startswith("## Post text\n\n4 ноября Notion дала агентам общий мозг.")
    assert "views" not in body and "| Jane Doe" not in body
    assert "## Transcript\n\n" + ai_share.EMPTY_TRANSCRIPT in body


def test_reel_with_speech_keeps_both_sections(client, db_session):
    _seed_user(db_session, "user_a")
    created = _create(client, _reel(db_session, transcript_md=TRANSCRIPT_MD).id).json()

    body = client.get(_path(created)).text.split("\n\n---\n\n", 1)[1]

    assert body.index("## Post text") < body.index("## Transcript")
    assert ai_share.EMPTY_TRANSCRIPT not in body


def test_empty_transcript_without_caption_is_explicit(client, db_session):
    _seed_user(db_session, "user_a")
    transcript = _seed_transcript(db_session, transcript_md=EMPTY_MD)
    created = _create(client, transcript.id).json()

    body = client.get(_path(created)).text.split("\n\n---\n\n", 1)[1]

    assert body.strip() == ai_share.EMPTY_TRANSCRIPT
    assert "## Post text" not in body


def test_caption_that_only_restates_title_is_not_served():
    class T:
        title = "The full-length interview with Elon Musk"
        original_title = "The full-length interview with Elon Musk | The Economist"
        author_name = "The Economist"
    assert ai_share.ai_post_text(T()) is None
