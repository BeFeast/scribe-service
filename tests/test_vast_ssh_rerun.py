"""A ready market instance gets at most one replacement after SSH failure."""
from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from scribe.config import settings
from scribe.pipeline import transcribe_providers as providers
from scribe.pipeline import whisper_client as wc


@pytest.fixture
def market(monkeypatch, tmp_path):
    now = [0.0]
    history = []
    offers = [
        {"id": 1, "host_id": 100, "dph_total": 0.30},
        {"id": 2, "host_id": 100, "dph_total": 0.30},
        {"id": 3, "host_id": 200, "dph_total": 0.60},
        {"id": 4, "host_id": 300, "dph_total": 0.60},
    ]
    monkeypatch.setattr(settings, "vast_api_key", "test-key")
    monkeypatch.setattr(settings, "pinned_vast_id", 0)
    monkeypatch.setattr(settings, "transcribe_timeout_secs", 1800)
    monkeypatch.setattr(settings, "vast_max_job_cost", 0.25)
    monkeypatch.setattr(providers, "_breakers", {})
    monkeypatch.setattr(wc, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(wc, "_sleep", lambda seconds: now.__setitem__(0, now[0] + seconds))
    monkeypatch.setattr(wc, "_ensure_local_ssh_key", lambda: (tmp_path / "key", "public"))
    monkeypatch.setattr(wc, "_ensure_vast_ssh_key", lambda *_args: None)
    monkeypatch.setattr(wc, "_select_offers", lambda *_args, **_kwargs: offers)

    def create(_key, offer, _public_key, **_kwargs):
        history.append(("rent", offer["id"]))
        return 1000 + offer["id"]

    def destroy(_key, instance_id):
        history.append(("destroy", instance_id))

    monkeypatch.setattr(wc, "_create_instance", create)
    monkeypatch.setattr(wc, "_destroy_instance", destroy)
    monkeypatch.setattr(wc, "_wait_for_ssh", lambda _, iid, *_a, **_k: (f"gpu-{iid}", 22))
    monkeypatch.setattr(wc, "_wait_remote_ready", lambda *_args, **_kwargs: None)

    # Mock the actual subprocess boundary so exit-code/stderr/timeout handling
    # is exercised alongside rent, host exclusion, teardown and accounting.
    def set_remote(remote):
        def run(cmd, *, text, capture_output, timeout):
            if cmd[0] == "ssh" and "remote_transcribe.py" in cmd[-1]:
                return remote(cmd, timeout)
            if cmd[0] == "scp" and cmd[-2].startswith("root@"):
                content = "{}" if cmd[-2].endswith("result.json") else "# transcript"
                Path(cmd[-1]).write_text(content, encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(wc.subprocess, "run", run)

    wav = tmp_path / "input.wav"
    wav.write_bytes(b"audio")
    audit = []

    def record(attempt):
        if attempt.finished_at is None:
            return
        history.append(("audit", attempt.instance_id))
        audit.append(attempt)

    def reserve_retry(*_args):
        history.append(("reserve", 2))
        return True

    return SimpleNamespace(
        now=now, history=history, audit=audit, set_remote=set_remote,
        attempt_store=SimpleNamespace(load=wc.VastLedger, record=record, reserve_retry=reserve_retry),
        request=providers.TranscribeRequest(wav, "clip", "https://example.com/clip"),
    )


@pytest.mark.parametrize("second_fails", [False, True])
def test_mid_run_ssh_drop_reruns_once_on_a_different_host(market, second_fails):
    timeouts = []

    def remote(cmd, timeout):
        timeouts.append(timeout)
        market.now[0] += 120
        if "root@gpu-1001" in cmd:
            return subprocess.CompletedProcess(cmd, 255, "", "Connection closed by remote host")
        if second_fails:
            return subprocess.CompletedProcess(cmd, 255, "", "Timeout, server not responding.")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    market.set_remote(remote)
    provider = providers.VastProvider(attempt_store=market.attempt_store)
    if second_fails:
        with pytest.raises(providers.TranscribeProviderUnavailableError, match="server not responding"):
            provider.transcribe(market.request)
    else:
        result = provider.transcribe(market.request)
        assert result.transcript_md == "# transcript"
        assert result.vast_instance_id == 1003
        assert result.vast_cost == pytest.approx(0.03)

    assert market.history == [
        ("rent", 1), ("destroy", 1001), ("audit", 1001),
        ("reserve", 2),
        ("rent", 3), ("destroy", 1003), ("audit", 1003),
    ]
    assert timeouts == pytest.approx([1800, 1440])
    first, second = market.audit
    assert (first.number, second.number) == (1, 2)
    assert (first.host_id, second.host_id) == (100, 200)
    assert first.outcome == "ssh_transport_error"
    assert second.outcome == ("ssh_transport_error" if second_fails else "success")
    assert first.destroyed and second.destroyed
    assert [first.estimated_cost_usd, second.estimated_cost_usd] == pytest.approx([0.01, 0.02])


@pytest.mark.parametrize("guard", ["budget", "time", "cleanup", "breaker", "media", "auth", "http"])
def test_vast_rerun_requires_transient_failure_and_available_budget(monkeypatch, market, guard):
    breaker = providers.get_breaker("vast")
    if guard == "time":
        monkeypatch.setattr(settings, "transcribe_timeout_secs", 650)
    if guard == "budget":
        monkeypatch.setattr(settings, "vast_max_job_cost", 0.10)
    if guard == "cleanup":
        def destroy(_api_key, instance_id):
            market.history.append(("destroy", instance_id))
            raise wc.WhisperError("Vast destroy unconfirmed")

        monkeypatch.setattr(wc, "_destroy_instance", destroy)

    def remote(cmd, _timeout):
        if guard == "media":
            return subprocess.CompletedProcess(cmd, 1, "", "invalid audio stream")
        if guard == "auth":
            return subprocess.CompletedProcess(cmd, 255, "", "Permission denied (publickey)")
        if guard == "http":
            return subprocess.CompletedProcess(cmd, 1, "", "HTTP 403 Forbidden")
        if guard == "breaker":
            for _ in range(breaker.threshold):
                breaker.record("unavailable", mode="allow")
        market.now[0] += 300 if guard == "budget" else 100
        raise subprocess.TimeoutExpired(cmd, _timeout)

    market.set_remote(remote)
    provider = providers.VastProvider(attempt_store=market.attempt_store)
    with pytest.raises(providers.TranscribeChainError):
        providers.transcribe_with_chain([provider], market.request)

    assert market.history == [("rent", 1), ("destroy", 1001), ("audit", 1001)]
    assert market.audit[0].destroyed == (guard != "cleanup")


def test_scp_connection_closed_has_distinct_error(monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: subprocess.CompletedProcess(
        ["scp"], 1, "", "scp: Connection closed",
    ))
    with pytest.raises(wc.SSHTransportError, match="Connection closed"):
        wc._run(["scp"])


def test_failed_startup_is_logged_and_consumes_the_rerun_budget(monkeypatch, market):
    monkeypatch.setattr(settings, "vast_max_job_cost", 0.15)

    def ready(_, instance_id, *_a, **_k):
        if instance_id == 1001:
            market.now[0] += 480
            raise wc.VastInstanceFailedError("instance failed to start")
        return f"gpu-{instance_id}", 22

    def remote(cmd, _timeout):
        market.now[0] += 120
        return subprocess.CompletedProcess(cmd, 255, "", "Connection closed by remote host")

    monkeypatch.setattr(wc, "_wait_for_ssh", ready)
    market.set_remote(remote)
    with pytest.raises(providers.TranscribeProviderUnavailableError, match="remaining time or spend budget"):
        providers.VastProvider(attempt_store=market.attempt_store).transcribe(market.request)
    assert market.history == [
        ("rent", 1), ("destroy", 1001), ("audit", 1001),
        ("rent", 3), ("destroy", 1003), ("audit", 1003),
    ]
    assert [attempt.outcome for attempt in market.audit] == ["startup_failed", "ssh_transport_error"]
    assert [attempt.estimated_cost_usd for attempt in market.audit] == pytest.approx([0.04, 0.02])


@pytest.fixture
def worker_market(monkeypatch, market):
    from scribe.pipeline.downloader import DownloadResult
    from scribe.worker import loop

    monkeypatch.setattr(settings, "transcribe_providers", ["vast"])
    monkeypatch.setattr(settings, "vast_monthly_cap_usd", 0)
    monkeypatch.setattr(loop.downloader, "download_audio", lambda url, *_a, **_k: DownloadResult(
        audio_path=market.request.wav, title=market.request.title, video_id=url.rsplit("/", 1)[-1],
        duration_seconds=30,
    ))
    monkeypatch.setattr(loop.ffmpeg, "to_wav_16k_mono", lambda *_a, **_k: market.request.wav)

    def remote(cmd, _timeout):
        market.now[0] += 120
        return subprocess.CompletedProcess(cmd, 255, "", "Connection closed by remote host")

    market.set_remote(remote)
    return market


def test_worker_persists_both_costs_on_terminal_failure(db_session, worker_market):
    from scribe.db.models import Job, JobStatus
    from scribe.worker import loop

    job = Job(
        url=worker_market.request.source_url, video_id="clip",
        status=JobStatus.downloading, summarize=False, notify=False,
    )
    db_session.add(job)
    db_session.commit()
    loop.process_job(db_session, job)

    db_session.refresh(job)
    assert job.status == JobStatus.failed
    assert "chain_exhausted" in job.error
    assert job.vast_instance_id == 1003
    assert job.destroy_failed_at is None
    attempts = sorted(
        [event.details for event in job.stage_events if event.stage.startswith("vast_attempt_")],
        key=lambda details: details["number"],
    )
    assert [event["instance_id"] for event in attempts] == [1001, 1003]
    assert [event["estimated_cost_usd"] for event in attempts] == pytest.approx([0.01, 0.02])
    assert [event["outcome"] for event in attempts] == ["ssh_transport_error", "ssh_transport_error"]


@pytest.mark.parametrize("reserved", [False, True])
def test_worker_restart_never_renews_the_rerun_budget(db_session, monkeypatch, worker_market, reserved):
    from sqlalchemy.orm import sessionmaker

    from scribe.db.models import Job, JobStatus
    from scribe.worker import loop
    from scribe.worker.vast_attempts import JobVastAttempts

    video_id = f"vast-restart-{reserved}"
    job = Job(
        url=f"https://example.com/{video_id}", video_id=video_id,
        status=JobStatus.transcribing, summarize=False, notify=False,
    )
    db_session.add(job)
    db_session.commit()
    store = JobVastAttempts(job.id, sessionmaker(bind=db_session.get_bind(), autoflush=False))
    now = datetime.now(UTC)
    if reserved:
        store.record(wc.VastAttempt(
            number=1, instance_id=1001, offer_id=1, host_id=100, hourly_usd=0.30,
            started_at=now - timedelta(seconds=120), finished_at=now,
            outcome="ssh_transport_error", destroyed=True, exclude_host=True, estimated_cost_usd=0.01,
        ))
        assert store.reserve_retry("test-rerun", 3, 200, 0.60)
        assert not store.reserve_retry("second-rerun", 4, 300, 0.60)
        store.record(wc.VastAttempt(
            number=2, instance_id=1003, offer_id=3, host_id=200,
            started_at=now - timedelta(seconds=45), hourly_usd=0.60,
        ))
    else:
        record = JobVastAttempts.record

        def crash_after_destroy(self, attempt):
            if attempt.finished_at is not None and attempt.outcome == "ssh_transport_error":
                raise SystemExit("worker died after destroy")
            record(self, attempt)

        with monkeypatch.context() as patch, pytest.raises(SystemExit):
            patch.setattr(JobVastAttempts, "record", crash_after_destroy)
            loop.process_job(db_session, job)
        assert worker_market.history == [("rent", 1), ("destroy", 1001)]
        assert store.load().attempts[0].outcome == "ssh_transport_error"
        worker_market.history.clear()
    original_spend = store.load().spent_usd

    # Startup recovery destroys the persisted live rental before requeuing.
    # A freshly started worker reads the instance ID committed by the store.
    db_session.expire_all()
    loop.recover_interrupted_jobs(db_session)
    db_session.commit()
    db_session.refresh(job)
    assert job.status == JobStatus.queued
    loop.process_job(db_session, job)
    db_session.refresh(job)
    assert job.status == JobStatus.failed

    rentals = [offer_id for action, offer_id in worker_market.history if action == "rent"]
    ledger = store.load()
    assert ledger.retry_reserved
    if reserved:
        assert rentals == []
        assert ("destroy", 1003) in worker_market.history
        assert "already been reserved" in job.error
        replacement = next(attempt for attempt in ledger.attempts if attempt.number == 2)
        assert replacement.outcome == "interrupted"
        assert replacement.destroyed
        assert replacement.estimated_cost_usd >= 0.0075
    else:
        assert rentals == [3]
        assert ledger.spent_usd == pytest.approx(original_spend + 0.02)


@pytest.mark.parametrize("crash", [False, True])
def test_untracked_rerun_is_reconciled_by_its_durable_label(db_session, monkeypatch, worker_market, crash):
    from scribe.db.models import Job, JobStatus
    from scribe.worker import loop
    from scribe.worker.vast_attempts import JobVastAttempts

    inventory = [{"label": "some-other-workload", "id": 9999}]

    def create(_key, offer, _public_key, *, label=None):
        if label is None:
            return 1001
        inventory.append({"label": label, "id": 1003})
        if crash:
            raise SystemExit("worker died after the PUT")
        raise wc.WhisperError("Vast response lost")

    monkeypatch.setattr(wc, "_create_instance", create)
    monkeypatch.setattr(loop.vast_budget, "fetch_instances", lambda *_a, **_k: inventory)
    video_id = f"vast-untracked-{crash}"
    job = Job(url=f"https://example.com/{video_id}", video_id=video_id, status=JobStatus.downloading)
    db_session.add(job)
    db_session.commit()
    if crash:
        with pytest.raises(SystemExit):
            loop.process_job(db_session, job)
        db_session.expire_all()
        loop.recover_interrupted_jobs(db_session)
    else:
        class Fallback:
            name = "local-whisper"

            def transcribe(self, _request):
                db_session.refresh(job)
                assert job.status == JobStatus.transcribing
                assert ("destroy", 1003) in worker_market.history
                raise providers.TranscribeProviderUnavailableError(reason="fallback_test")

        monkeypatch.setitem(providers.PROVIDER_REGISTRY, "local-whisper", lambda _settings: Fallback())
        monkeypatch.setattr(settings, "transcribe_providers", ["vast", "local-whisper"])
        loop.process_job(db_session, job)
        assert "fallback_test" in job.error

    assert ("destroy", 1003) in worker_market.history
    assert ("destroy", 9999) not in worker_market.history
    ledger = JobVastAttempts(job.id, lambda: db_session).load()
    assert ledger.retry_reserved
    adopted = next(attempt for attempt in ledger.attempts if attempt.number == 2)
    assert adopted.instance_id == 1003 and adopted.destroyed
    assert adopted.estimated_cost_usd > 0
