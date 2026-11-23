"""Vast.ai whisper client — GPU transcription only.

Ported from run_vast_video_summary.py. In the scribe architecture Vast does
*only* faster-whisper: download + ffmpeg run locally on a residential IP, and
the 16 kHz mono wav is shipped here for GPU transcription.

Adds a cuda_max_good >= 12.4 offer filter so we never land on a host whose
NVIDIA driver cannot run the CUDA 12.4 image (the "CUDA failed: unsupported
display driver / cuda driver combination" failure seen on 2026-05-14).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import queue
import random
import shlex
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import uuid4

from scribe.config import settings

VAST_API = "https://console.vast.ai/api/v0"
VAST_IMAGE = "ghcr.io/befeast/scribe-service-vast:cuda12.4-whisper"
# Hard upper bound on a single instance's wall-clock budget; the per-job cost
# guard (settings.vast_max_job_cost) usually trips well before this.
MAX_INSTANCE_SECONDS = 1800
_MIN_RERUN_SECONDS = 600
# Vast instance status fields (actual_status / cur_state / intended_status)
# that mean the container will not become ready — fail fast instead of
# polling for the full ready_timeout window.
_VAST_FAILED_STATES = frozenset({"exited", "failed", "crashed", "offline", "error", "stopped"})

REMOTE_TRANSCRIBE_SCRIPT = '#!/usr/bin/env -S uv run\n# /// script\n# requires-python = ">=3.10"\n# dependencies = [\n#   "faster-whisper>=1.1.1",\n# ]\n# ///\n\nimport argparse\nimport json\nimport re\nfrom datetime import datetime, timezone\nfrom pathlib import Path\n\nfrom faster_whisper import WhisperModel\n\n\ndef slugify(value: str) -> str:\n    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")\n    return slug or "transcript"\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--audio-file", required=True)\n    parser.add_argument("--title", required=True)\n    parser.add_argument("--source-url", required=True)\n    parser.add_argument("--model-size", default="large-v3-turbo")\n    parser.add_argument("--compute-type", default="float16")\n    parser.add_argument("--language", default="auto")\n    parser.add_argument("--beam-size", type=int, default=5)\n    parser.add_argument("--output-json", required=True)\n    parser.add_argument("--output-markdown", required=True)\n    args = parser.parse_args()\n\n    language = None if args.language == "auto" else args.language\n    model = WhisperModel(args.model_size, device="cuda", compute_type=args.compute_type)\n    segments, info = model.transcribe(args.audio_file, language=language, beam_size=args.beam_size, vad_filter=True)\n    collected = list(segments)\n    transcript_text = " ".join(segment.text.strip() for segment in collected if segment.text.strip()).strip()\n    duration = max((segment.end for segment in collected), default=None)\n    detected_language = getattr(info, "language", None) or "unknown"\n    language_probability = getattr(info, "language_probability", None)\n    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")\n    backend = f"faster-whisper ({args.model_size}, {args.compute_type}, cuda)"\n    probability_text = "unknown" if language_probability is None else f"{language_probability:.3f}"\n    duration_text = "unknown" if duration is None else f"{duration:.2f}s"\n\n    markdown = (\n        f"# {args.title}\\n\\n"\n        "## Metadata\\n"\n        f"- Source URL: {args.source_url}\\n"\n        "- Source audio: Vast remote yt-dlp/ffmpeg pipeline\\n"\n        f"- Transcription model: {backend}\\n"\n        f"- Detected language: {detected_language}\\n"\n        f"- Language probability: {probability_text}\\n"\n        f"- Duration: {duration_text}\\n"\n        f"- Generated at: {generated_at}\\n\\n"\n        "## Transcript\\n\\n"\n        f"{transcript_text}\\n"\n    )\n    Path(args.output_markdown).write_text(markdown, encoding="utf-8")\n    Path(args.output_json).write_text(\n        json.dumps(\n            {\n                "title": args.title,\n                "detected_language": detected_language,\n                "language_probability": language_probability,\n                "duration_seconds": duration,\n                "backend": backend,\n                "transcript_characters": len(transcript_text),\n            },\n            ensure_ascii=False,\n        ),\n        encoding="utf-8",\n    )\n    print(f"TITLE:{args.title}")\n    print(f"DETECTED_LANGUAGE:{detected_language}")\n    print(f"TRANSCRIBE_BACKEND:{backend}")\n    print(f"TRANSCRIPT_CHARACTERS:{len(transcript_text)}")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'


class WhisperError(RuntimeError):
    pass


class TranscribeTimeoutError(WhisperError):
    pass


class VastInstanceFailedError(WhisperError):
    """Vast container reached a terminal-failure state during startup."""


class VastReadyTimeoutError(WhisperError):
    """Vast container did not become ready within the per-attempt ready budget."""


class SSHTransportError(WhisperError):
    """SSH or SCP lost its connection or exceeded its command timeout."""


class VastRateLimitedError(WhisperError):
    """Vast API kept returning HTTP 429 after the client's bounded retries.

    Distinct type (rather than substring matching on the message) so the
    provider layer can classify sustained rate-limit pressure as usage_limit
    without misreading aggregated errors that merely embed "HTTP 429"."""


class PinnedGpuBusy(WhisperError):
    """Always-on meeting GPU is held by a meeting or another scribe job."""


class PinnedGpuUnavailable(WhisperError):
    """Pinned GPU SSH/job failed; caller should fall back to market rent."""


class VastBudgetExceededError(WhisperError):
    """Per-attempt cost budget exhausted (distinct from a ready-timeout).

    Raised by `_ensure_budget` when the deadline fired for cost reasons
    rather than the ready_timeout window. Keeping this separate from
    `VastReadyTimeoutError` lets the offer loop decide whether the host
    itself is bad (ready-timeout / container-failed → blacklist) or the
    job simply ran out of money (cost-cap → do not blacklist a healthy
    host's sibling offers).
    """


@dataclass
class TranscribeResult:
    transcript_md: str
    detected_language: str
    duration_seconds: float | None
    backend: str
    vast_instance_id: int
    vast_cost: float
    # Which transcription provider served this result (see
    # scribe.pipeline.transcribe_providers). Defaults to "vast" so the Vast
    # GPU path — the historical and only producer of this dataclass — keeps
    # the right label without touching every construction site. `vast_cost`
    # carries the provider's estimated USD spend regardless of provider; only
    # the Vast path persists it to transcripts.vast_cost (daily-cap input).
    provider: str = "vast"


@dataclass(frozen=True)
class VastAttempt:
    number: int
    instance_id: int
    offer_id: int
    host_id: int | None
    started_at: datetime
    hourly_usd: float
    finished_at: datetime | None = None
    outcome: str = "running"
    estimated_cost_usd: float = 0.0
    destroyed: bool = False
    exclude_host: bool = False


@dataclass
class VastLedger:
    attempts: list[VastAttempt] = field(default_factory=list)
    retry_reserved: bool = False

    @property
    def spent_usd(self) -> float:
        return sum(attempt.estimated_cost_usd for attempt in self.attempts)

    @property
    def excluded_hosts(self) -> set[int]:
        return {
            attempt.host_id for attempt in self.attempts
            if attempt.exclude_host and attempt.host_id is not None
        }


class VastAttemptStore(Protocol):
    def load(self) -> VastLedger: ...

    def reserve_retry(self, label: str, offer_id: int, host_id: int | None, hourly_usd: float) -> bool: ...

    def reconcile_retry(self) -> None: ...

    def record(self, attempt: VastAttempt) -> None: ...


def _noop_instance_created(_instance_id: int) -> None:
    return None


class _TranscribeRunContext:
    def __init__(
        self,
        *,
        on_destroy_failed: Callable[[int], None] | None = None,
        on_destroy_succeeded: Callable[[int], None] | None = None,
    ) -> None:
        self._lock = threading.Lock()
        self._api_key = ""
        self._instance_id: int | None = None
        self._destroyed_instance_id: int | None = None
        self._cancelled = False
        self.deadline = time.monotonic() + settings.transcribe_timeout_secs
        self._on_destroy_failed = on_destroy_failed
        self._on_destroy_succeeded = on_destroy_succeeded

    def set_api_key(self, api_key: str) -> None:
        with self._lock:
            self._api_key = api_key

    def set_instance(self, instance_id: int) -> None:
        with self._lock:
            self._instance_id = instance_id

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def raise_if_cancelled(self) -> None:
        with self._lock:
            cancelled = self._cancelled
        if cancelled or time.monotonic() >= self.deadline:
            raise TranscribeTimeoutError(
                f"transcribe timed out after {settings.transcribe_timeout_secs}s"
            )

    def destroy_instance(self) -> bool:
        with self._lock:
            api_key = self._api_key
            instance_id = self._instance_id
            self._instance_id = None
        if api_key and instance_id is not None:
            try:
                _destroy_instance(api_key, instance_id)
            except Exception:
                if self._on_destroy_failed is not None:
                    self._on_destroy_failed(instance_id)
                raise
            with self._lock:
                self._destroyed_instance_id = instance_id
            if self._on_destroy_succeeded is not None:
                self._on_destroy_succeeded(instance_id)
            return True
        return False

    def was_destroyed(self, instance_id: int) -> bool:
        with self._lock:
            return self._destroyed_instance_id == instance_id


# --- subprocess + http helpers ------------------------------------------
def _run(cmd: list[str], *, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    is_ssh = cmd[0] in {"ssh", "scp"}
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if check:
            error = SSHTransportError if is_ssh else WhisperError
            raise error(f"command timed out after {timeout}s: {' '.join(cmd)}") from exc
        return subprocess.CompletedProcess(cmd, 124, stdout=exc.stdout or "", stderr=f"timeout after {timeout}s")
    if check and proc.returncode != 0:
        error = WhisperError
        if is_ssh:
            stderr = (proc.stderr or "").lower()
            permanent = ("permission denied", "host key verification failed", "host identification has changed")
            transport = (
                "connection closed", "connection reset", "connection timed out", "operation timed out",
                "lost connection", "broken pipe", "connection refused", "no route to host",
                "network is unreachable", "timeout, server",
            )
            if not any(marker in stderr for marker in permanent) and (
                (cmd[0] == "ssh" and proc.returncode == 255)
                or (cmd[0] == "scp" and any(marker in stderr for marker in transport))
            ):
                error = SSHTransportError
        raise error(
            f"command failed ({proc.returncode}): {' '.join(cmd)}\n"
            f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        )
    return proc


# Vast rate-limits per endpoint + per identity as a minimum interval between
# requests (documented endpoint thresholds are ~1.0-4.5s) and sends no
# Retry-After header, so clients must bring their own backoff — both official
# Vast clients retry 429 out of the box. The base delay starts above the
# largest documented threshold so the first retry can already clear the
# closed interval; the 429 JSON body sometimes carries a `retry_after` hint.
_VAST_RETRY_HTTP_CODES = frozenset({408, 429, 502, 503, 504})
_VAST_RETRY_ATTEMPTS = 4
_VAST_RETRY_BASE_SECONDS = 2.5
_VAST_RETRY_MAX_SLEEP_SECONDS = 30.0
# Hard cap on cumulative backoff per call so server hints cannot stretch one
# API call into minutes while an instance is billing.
_VAST_RETRY_MAX_TOTAL_SLEEP_SECONDS = 45.0
# Indirection so tests can stub pacing/transport/jitter deterministically
# without patching the process-global stdlib modules.
_sleep = time.sleep
_urlopen = urllib.request.urlopen


def _jitter() -> float:
    return random.uniform(0.0, 1.0)


def _retry_hint_seconds(headers, detail: str) -> float | None:
    """Server-suggested wait: Retry-After header (documented absent, honored
    if present and numeric) falling back to the `retry_after` JSON-body field
    (observed live: 10-17s)."""
    candidates: list[object] = []
    if headers is not None:
        candidates.append(headers.get("Retry-After"))
    if detail:
        try:
            parsed = json.loads(detail)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            candidates.append(parsed.get("retry_after"))
    for value in candidates:
        try:
            seconds = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if seconds >= 0:
            return seconds
    return None


def vast_api_request(
    api_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
    *,
    timeout: int = 45,
    error_factory: Callable[[str], Exception] = WhisperError,
    retry_codes: frozenset[int] = _VAST_RETRY_HTTP_CODES,
    retry_network: bool = True,
    attempts: int = _VAST_RETRY_ATTEMPTS,
) -> dict:
    """One Vast API call with bounded retry on 429/transient failures.

    Shared by the whisper client, the orphan reaper, and the budget monitor
    (each with its own `error_factory`). Non-idempotent calls (instance
    create) must pass `retry_codes=frozenset({429})` and
    `retry_network=False`: a 429 is rejected before processing, while
    5xx/timeout/network failures are ambiguous — a blind resend could
    double-create a billing instance.
    """
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    attempts = max(1, attempts)
    slept = 0.0
    message = f"Vast API {method} {path}: retries exhausted"

    def _pause(delay: float) -> None:
        nonlocal slept
        remaining = _VAST_RETRY_MAX_TOTAL_SLEEP_SECONDS - slept
        wait = min(delay + _jitter(), remaining)
        if wait > 0:
            _sleep(wait)
            slept += wait

    for attempt in range(attempts):
        req = urllib.request.Request(f"{VAST_API}{path}", data=data, method=method, headers=headers)
        delay = min(_VAST_RETRY_BASE_SECONDS * (2**attempt), _VAST_RETRY_MAX_SLEEP_SECONDS)
        try:
            with _urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            message = f"Vast API {method} {path}: HTTP {exc.code}: {detail}"
            if exc.code not in retry_codes or attempt + 1 >= attempts:
                if exc.code == 429 and error_factory is WhisperError:
                    raise VastRateLimitedError(message) from exc
                raise error_factory(message) from exc
            hint = _retry_hint_seconds(exc.headers, detail)
            if hint is not None:
                delay = min(max(delay, hint), _VAST_RETRY_MAX_SLEEP_SECONDS)
            _pause(delay)
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
            message = f"Vast API {method} {path}: {exc}"
            if not retry_network or attempt + 1 >= attempts:
                raise error_factory(message) from exc
            _pause(delay)
        else:
            return json.loads(body) if body.strip() else {}
    # Unreachable while attempts >= 1 (every branch above returns or raises);
    # kept as a guard against a misconfigured attempts constant.
    raise error_factory(message)


def _vast(
    api_key: str,
    method: str,
    path: str,
    payload: dict | None = None,
    timeout: int = 45,
    **retry_kwargs,
) -> dict:
    return vast_api_request(api_key, method, path, payload, timeout=timeout, **retry_kwargs)


# --- ssh key ------------------------------------------------------------
def _ensure_local_ssh_key() -> tuple[Path, str]:
    key = Path.home() / ".ssh" / "id_ed25519"
    pub = key.with_suffix(".pub")
    if not key.is_file() or not pub.is_file():
        key.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        _run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key), "-C", "scribe-vast"])
    return key, pub.read_text(encoding="utf-8").strip()


def _ensure_vast_ssh_key(api_key: str, public_key: str) -> None:
    try:
        if public_key in json.dumps(_vast(api_key, "GET", "/ssh/")):
            return
    except Exception:
        pass
    try:
        _vast(api_key, "POST", "/ssh/", {"ssh_key": public_key})
    except WhisperError as exc:
        if "already exists" not in str(exc):
            raise


# --- offers -------------------------------------------------------------
def _select_offers(
    api_key: str,
    *,
    max_price: float,
    gpu_regex: str,
    min_cuda: float,
    excluded_hosts: set[int] | None = None,
) -> list[dict]:
    import re

    excluded = excluded_hosts or set()
    query = {
        "limit": 400, "type": "on-demand",
        "rentable": {"eq": True}, "rented": {"eq": False},
        "gpu_ram": {"gte": 16000}, "num_gpus": {"eq": 1},
        # Fractional slices still advertise full gpu_ram; require a whole GPU
        # so large-v3-turbo does not OOM-kill the container mid-SSH (#421).
        "gpu_frac": {"eq": 1.0},
        # Do not require Vast `verified`: that badge ∩ gpu_frac=1.0 is often a
        # 0–1 offer market, so jobs fail in seconds with empty pool / no_such_ask.
        # Keep gpu_frac=1.0 plus client-side reliability>=0.90.
    }
    offers = _vast(api_key, "POST", "/bundles/", query, timeout=60).get("offers", [])
    pattern = re.compile(gpu_regex, re.IGNORECASE)
    candidates = []
    for offer in offers:
        price = float(offer.get("dph_total") or 999)
        cuda = float(offer.get("cuda_max_good") or 0)
        reliability = float(offer.get("reliability") or offer.get("reliability2") or 0)
        # Missing gpu_frac → treat as dedicated (back-compat with fixtures /
        # older market payloads). Anything < 1.0 is a slice and is rejected.
        try:
            gpu_frac = float(offer["gpu_frac"]) if offer.get("gpu_frac") is not None else 1.0
        except (TypeError, ValueError):
            gpu_frac = 0.0
        host_id_raw = offer.get("host_id")
        try:
            host_id = int(host_id_raw) if host_id_raw is not None else None
        except (TypeError, ValueError):
            host_id = None
        if host_id is not None and host_id in excluded:
            continue
        if (price <= max_price and cuda >= min_cuda and reliability >= 0.90
                and gpu_frac >= 0.999
                and pattern.search(str(offer.get("gpu_name") or ""))):
            candidates.append(offer)
    if not candidates:
        raise WhisperError(
            f"no Vast offer matched (max_price={max_price}, cuda_max_good>={min_cuda}, "
            f"gpu_frac>=1.0, gpu_regex, reliability>=0.90)"
        )
    # Cheapest first; prefer high reliability and a fast network on ties so the
    # CUDA image pull does not eat the ready-timeout budget.
    return sorted(
        candidates,
        key=lambda o: (
            float(o.get("dph_total") or 999),
            -float(o.get("reliability") or o.get("reliability2") or 0),
            -float(o.get("inet_down") or 0),
        ),
    )


def _is_no_such_ask(exc: BaseException) -> bool:
    """Detect the offer→ask race: Vast returns HTTP 400 with 'no_such_ask' or
    'not available' when the offer was rented by another tenant between
    `_select_offers` and our `PUT /asks/{id}`. We can immediately try the next
    candidate without spending the ready-timeout budget."""
    text = str(exc)
    if "HTTP 400" not in text:
        return False
    lowered = text.lower()
    return "no_such_ask" in lowered or "not available" in lowered


# --- instance lifecycle -------------------------------------------------
def _create_instance(api_key: str, offer: dict, public_key: str, *, label: str | None = None) -> int:
    label = label or f"{socket.gethostname()}-scribe-whisper-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    onstart = (
        "set -eu; "
        'export PATH="/usr/local/bin:/root/.local/bin:/opt/conda/bin:$PATH"; '
        "if ! command -v ffmpeg >/dev/null 2>&1; then "
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y ffmpeg ca-certificates curl openssh-client; "
        "fi; "
        "if ! command -v uv >/dev/null 2>&1; then curl -LsSf https://astral.sh/uv/install.sh | sh; fi; "
        "echo ready >/root/video-summary-ready"
    )
    payload = {
        "client_id": "me", "image": VAST_IMAGE, "env": {}, "price": None,
        "disk": 30, "label": label, "extra": None, "onstart": onstart,
        "image_login": None, "python_utf8": False, "lang_utf8": False,
        "use_jupyter_lab": False, "jupyter_dir": None, "force": False,
        "cancel_unavail": True, "template_hash_id": None, "user": None,
        "runtype": "ssh_direc ssh_proxy",
    }
    # Create is non-idempotent and starts billing server-side: only a 429
    # (rejected before processing) is safe to resend. An ambiguous 5xx or
    # network failure must fail this offer — a blind resend could create a
    # second, untracked billing instance.
    resp = _vast(
        api_key,
        "PUT",
        f"/asks/{offer['id']}/",
        payload,
        timeout=60,
        retry_codes=frozenset({429}),
        retry_network=False,
    )
    iid = resp.get("new_contract") or resp.get("id") or resp.get("instance_id")
    if not iid:
        raise WhisperError(f"Vast create response missing instance id: {resp}")
    try:
        _vast(api_key, "POST", f"/instances/{iid}/ssh/", {"ssh_key": public_key})
    except Exception:
        pass
    return int(iid)


# Documented min-interval for GET /instances/{id}/ is ~2s; give the confirm
# read its own window instead of bursting it right behind the DELETE (the
# exact burst that tripped `endpoint threshold=2.0*1.0` on job 491).
_DESTROY_CONFIRM_DELAY_SECONDS = 2.0
# Teardown gets a reduced retry budget: it is called from the worker claim
# loop and job finalize, and it already has its own retry layers (destroy
# sweep + reaper), so blocking a worker thread for minutes buys nothing.
_VAST_DESTROY_ATTEMPTS = 2


def _is_gone_error(exc: BaseException) -> bool:
    text = str(exc)
    return "HTTP 404" in text or "HTTP 410" in text


def _is_pinned_instance(instance_id: int) -> bool:
    pinned = int(getattr(settings, "pinned_vast_id", 0) or 0)
    return pinned > 0 and int(instance_id) == pinned


def _pinned_configured() -> bool:
    return bool(
        int(getattr(settings, "pinned_vast_id", 0) or 0)
        and str(getattr(settings, "pinned_ssh_host", "") or "").strip()
        and str(getattr(settings, "pinned_ssh_key", "") or "").strip()
    )


def _destroy_instance(api_key: str, instance_id: int) -> None:
    if _is_pinned_instance(instance_id):
        print(
            f"Notice: refusing to destroy pinned meeting GPU {instance_id}",
            file=sys.stderr,
        )
        return
    try:
        _vast(
            api_key, "DELETE", f"/instances/{instance_id}/", {},
            timeout=45, attempts=_VAST_DESTROY_ATTEMPTS,
        )
    except WhisperError as exc:
        # Likely already gone (a prior DELETE landed but its confirm failed) —
        # that must read as success or the destroy sweep re-fires this DELETE
        # forever. Still fall through to the confirm below instead of trusting
        # the DELETE-404 alone.
        if not _is_gone_error(exc):
            raise
    _sleep(_DESTROY_CONFIRM_DELAY_SECONDS)
    try:
        confirm = _vast(
            api_key, "GET", f"/instances/{instance_id}/",
            timeout=45, attempts=_VAST_DESTROY_ATTEMPTS,
        )
    except WhisperError as exc:
        if _is_gone_error(exc):
            return
        raise
    if confirm.get("instances") is None:
        return
    raise WhisperError(f"Vast instance {instance_id} still present after destroy: {confirm}")


def _get_instance(api_key: str, instance_id: int) -> dict:
    for inst in _vast(api_key, "GET", "/instances/", timeout=45).get("instances", []):
        if int(inst.get("id") or 0) == instance_id:
            return inst
    return {}


# --- ssh/scp ------------------------------------------------------------
def _ssh_base(host: str, port: int, key_path: Path) -> list[str]:
    return [
        "ssh", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
        "-p", str(port), f"root@{host}",
    ]


def _ssh_endpoints(instance: dict) -> list[tuple[str, int, str]]:
    endpoints: list[tuple[str, int, str]] = []
    public_ip = str(instance.get("public_ipaddr") or "").strip()
    ports = instance.get("ports") or {}
    ssh_ports = ports.get("22/tcp") if isinstance(ports, dict) else None
    if public_ip and isinstance(ssh_ports, list):
        for item in ssh_ports:
            if isinstance(item, dict) and item.get("HostPort") and str(item.get("HostIp") or "") != "::":
                endpoints.append((public_ip, int(item["HostPort"]), "direct"))
    if instance.get("ssh_host") and instance.get("ssh_port"):
        endpoints.append((str(instance["ssh_host"]), int(instance["ssh_port"]), "proxy"))
    seen: set[tuple[str, int]] = set()
    unique: list[tuple[str, int, str]] = []
    for host, port, kind in endpoints:
        if (host, port) not in seen:
            seen.add((host, port))
            unique.append((host, port, kind))
    return unique


def _scp_base(host: str, port: int, key_path: Path) -> list[str]:
    return [
        "scp", "-q", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=accept-new", "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=30", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=4",
        "-P", str(port),
    ]


def _scp_to(host: str, port: int, key_path: Path, src: Path, target: str, *, timeout: float = 600) -> None:
    _run([*_scp_base(host, port, key_path), str(src), f"root@{host}:{target}"], timeout=timeout)


def _scp_from(host: str, port: int, key_path: Path, src: str, target: Path, *, timeout: float = 120) -> None:
    _run([*_scp_base(host, port, key_path), f"root@{host}:{src}", str(target)], timeout=timeout)


def _transfer_with_retry(
    context: _TranscribeRunContext,
    operation: Callable[[str, int, float], None],
    endpoints: list[tuple[str, int]],
    *, deadline: float, max_seconds: float, label: str,
) -> tuple[str, int]:
    """Retry idempotent SCP on the same instance without extending its budget.

    Preserve the existing per-attempt limit for healthy large uploads while
    bounding all attempts by the job's absolute time/cost deadline. Reuse the
    ready instance's alternate direct/proxy address, never rent another instance.
    """
    transfer_deadline = deadline
    for attempt in range(3):
        context.raise_if_cancelled()
        remaining = transfer_deadline - time.monotonic()
        if remaining <= 0:
            raise VastBudgetExceededError(f"Vast transfer deadline exceeded: {label}")
        host, port = endpoints[attempt % len(endpoints)]
        try:
            operation(host, port, min(max_seconds, remaining))
        except SSHTransportError as exc:
            context.raise_if_cancelled()
            if attempt == 2:
                raise
            print(
                f"Warning: Vast {label} transfer attempt {attempt + 1}/3 failed "
                f"on {host}:{port}; retrying on the same instance: {exc}",
                file=sys.stderr,
            )
            # Pacing is included in the same absolute transfer/job deadline.
            _sleep(min(2.0, max(0.0, transfer_deadline - time.monotonic())))
            continue
        context.raise_if_cancelled()
        if time.monotonic() >= transfer_deadline:
            raise VastBudgetExceededError(f"Vast transfer deadline exceeded: {label}")
        # Prefer the successful endpoint for subsequent files and execution.
        endpoints.remove((host, port))
        endpoints.insert(0, (host, port))
        return host, port
    raise AssertionError("transfer retry loop exhausted without an outcome")


# --- budget + waits -----------------------------------------------------
def _budget_deadline(started: float, price: float, max_cost: float, max_seconds: int) -> float:
    by_cost = max_cost / price * 3600 if price > 0 else max_seconds
    return started + min(max_seconds, by_cost)


def _ensure_budget(
    started: float,
    deadline: float,
    price: float,
    max_cost: float,
    *,
    ready_timeout: float | None = None,
    ready_started: float | None = None,
    label: str = "",
) -> None:
    """Raise when the per-attempt deadline is exceeded.

    The deadline is the min of cost-budget and ready-timeout (set in the
    main loop). When `ready_timeout` *and* `ready_started` are provided we
    classify the failure by the **per-attempt** elapsed time
    (`time.monotonic() - ready_started`), not the cumulative job elapsed
    time (`started`). Otherwise a multi-offer run where earlier attempts
    already burned most of `ready_timeout` would mislabel a cost-cap as a
    ready-timeout. Both messages include the offer/host label for triage.
    """
    if time.monotonic() <= deadline:
        return
    suffix = f" ({label})" if label else ""
    if ready_timeout is not None and ready_started is not None:
        ready_elapsed = time.monotonic() - ready_started
        if ready_elapsed >= ready_timeout:
            raise VastReadyTimeoutError(
                f"Vast ready_timeout exceeded after {ready_elapsed:.0f}s "
                f"(cap {ready_timeout:.0f}s){suffix}"
            )
    elapsed = time.monotonic() - started
    raise VastBudgetExceededError(
        f"Vast budget guard tripped after {elapsed:.0f}s "
        f"(~${price * elapsed / 3600:.4f}, cap ${max_cost}){suffix}"
    )


def _vast_failure_state(info: dict) -> str | None:
    """Return a non-empty state name if the container is in a terminal-failure
    state (per `_VAST_FAILED_STATES`). Caller fast-fails the offer."""
    actual = str(info.get("actual_status") or "").lower()
    cur = str(info.get("cur_state") or "").lower()
    intended = str(info.get("intended_status") or "").lower()
    for value in (actual, cur, intended):
        if value and value in _VAST_FAILED_STATES:
            return value
    return None


def _format_failure_detail(info: dict, failure_state: str) -> str:
    actual = str(info.get("actual_status") or "").lower()
    cur = str(info.get("cur_state") or "").lower()
    msg = str(info.get("status_msg") or "").strip()
    parts = [f"failure_state={failure_state}", f"actual_status={actual or '?'}", f"cur_state={cur or '?'}"]
    if msg:
        # Keep status_msg short — Vast sometimes returns multi-line container logs.
        snippet = msg.replace("\n", " ").strip()[:240]
        parts.append(f"status_msg={snippet!r}")
    return ", ".join(parts)


def _wait_for_ssh(
    api_key, instance_id, key_path, started, deadline, price, max_cost,
    *, ready_timeout: float, ready_started: float | None = None, label: str = "",
    endpoints: list[tuple[str, int]] | None = None,
) -> tuple[str, int]:
    while True:
        _ensure_budget(
            started, deadline, price, max_cost,
            ready_timeout=ready_timeout, ready_started=ready_started, label=label,
        )
        info = _get_instance(api_key, instance_id)
        failure = _vast_failure_state(info)
        if failure is not None:
            raise VastInstanceFailedError(
                f"Vast container failed to start: {_format_failure_detail(info, failure)}"
            )
        states = {str(info.get("actual_status") or "").lower(), str(info.get("cur_state") or "").lower()}
        if "running" in states:
            for host, port, kind in _ssh_endpoints(info):
                if _run([*_ssh_base(host, port, key_path), "true"], check=False, timeout=45).returncode == 0:
                    print(f"Using Vast {kind} SSH endpoint {host}:{port}", file=sys.stderr)
                    if endpoints is not None:
                        endpoints[:] = [(host, port)] + [
                            (h, p) for h, p, _kind in _ssh_endpoints(info) if (h, p) != (host, port)
                        ]
                    return host, port
        time.sleep(10)


def _wait_remote_ready(
    api_key, instance_id, host, port, key_path, started, deadline, price, max_cost,
    *, ready_timeout: float, ready_started: float | None = None, label: str = "",
) -> None:
    check = "test -f /root/video-summary-ready && command -v uv >/dev/null && nvidia-smi -L"
    while True:
        _ensure_budget(
            started, deadline, price, max_cost,
            ready_timeout=ready_timeout, ready_started=ready_started, label=label,
        )
        info = _get_instance(api_key, instance_id)
        failure = _vast_failure_state(info)
        if failure is not None:
            raise VastInstanceFailedError(
                f"Vast container failed mid-startup: {_format_failure_detail(info, failure)}"
            )
        if _run([*_ssh_base(host, port, key_path), check], check=False, timeout=45).returncode == 0:
            return
        time.sleep(10)


def _pinned_ssh_base(host: str, port: int, key_path: Path) -> list[str]:
    return [
        "ssh", "-q", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "UserKnownHostsFile=/tmp/scribe-gpu-known_hosts",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=4",
        "-p", str(port), f"{settings.pinned_ssh_user}@{host}",
    ]


def _pinned_scp_base(host: str, port: int, key_path: Path) -> list[str]:
    return [
        "scp", "-q", "-i", str(key_path),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes",
        "-o", "UserKnownHostsFile=/tmp/scribe-gpu-known_hosts",
        "-o", "GlobalKnownHostsFile=/dev/null",
        "-P", str(port),
    ]


def _pinned_run(cmd: list[str], *, timeout: float, step: str) -> subprocess.CompletedProcess:
    """Run one SSH/SCP step against the pinned GPU.

    A transport failure (host offline, connection refused, timeout) must reach
    `_transcribe_impl` as PinnedGpuUnavailable so the market rent path runs;
    `_run`'s generic WhisperError would otherwise escape as a hard Vast failure.
    """
    try:
        return _run(cmd, timeout=timeout)
    except WhisperError as exc:
        # _run formats "command failed (<rc>): <cmd>\nstdout:\n...\nstderr:\n<err>"
        # or "command timed out after <t>s: <cmd>"; keep the status and the stderr tail.
        message = str(exc)
        status = message.split(": ", 1)[0]
        _, sep, stderr = message.rpartition("stderr:\n")
        detail = (stderr.strip() if sep else "")[-500:] or "no stderr"
        raise PinnedGpuUnavailable(f"pinned GPU {step} {status}: {detail}") from exc


def _transcribe_pinned(
    wav: Path, *, title: str, source_url: str,
    model_size: str, compute_type: str, language: str, beam_size: int,
) -> TranscribeResult:
    key_path = Path(settings.pinned_ssh_key)
    if not key_path.is_file():
        raise PinnedGpuUnavailable(f"pinned SSH key missing: {key_path}")
    host = str(settings.pinned_ssh_host).strip()
    port = int(settings.pinned_ssh_port)
    user = str(settings.pinned_ssh_user or "root")
    digest = hashlib.sha256(f"{wav}:{wav.stat().st_mtime_ns}:{wav.stat().st_size}".encode()).hexdigest()[:16]
    remote_dir = f"/workspace/scribe-jobs/{digest}"
    remote_audio = f"{remote_dir}/input-16k.wav"
    remote_json = f"{remote_dir}/result.json"
    remote_md = f"{remote_dir}/transcript.md"
    ssh = _pinned_ssh_base(host, port, key_path)
    scp = _pinned_scp_base(host, port, key_path)
    started = time.monotonic()
    _pinned_run([*ssh, f"mkdir -p {shlex.quote(remote_dir)}"], timeout=45, step="mkdir")
    _pinned_run([*scp, str(wav), f"{user}@{host}:{remote_audio}"], timeout=600, step="audio upload")
    remote_cmd = (
        "cd /workspace && "
        "env LD_LIBRARY_PATH=/opt/conda/lib/python3.11/site-packages/nvidia/cudnn/lib"
        ":/opt/conda/lib/python3.11/site-packages/ctranslate2.libs "
        "HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 PYTHONPATH=/workspace "
        "python -m lane1.scribe_job "
        f"--audio {shlex.quote(remote_audio)} "
        f"--model-root /workspace/models "
        f"--title {shlex.quote(title)} "
        f"--source-url {shlex.quote(source_url)} "
        f"--job-id {shlex.quote(digest)} "
        f"--output-json {shlex.quote(remote_json)} "
        f"--output-markdown {shlex.quote(remote_md)} "
        f"--language {shlex.quote(language)} "
        f"--beam-size {int(beam_size)} "
        f"--compute-type {shlex.quote(compute_type)}"
    )
    completed = subprocess.run(
        [*ssh, remote_cmd],
        text=True,
        capture_output=True,
        timeout=max(120, int(settings.transcribe_timeout_secs)),
    )
    if completed.returncode == 75:
        raise PinnedGpuBusy((completed.stderr or completed.stdout or "GPU busy").strip()[-500:])
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-1500:]
        raise PinnedGpuUnavailable(
            f"pinned GPU job failed ({completed.returncode}): {detail}"
        )
    with tempfile.TemporaryDirectory(prefix="scribe-pinned-") as tmp:
        local_json = Path(tmp) / "result.json"
        local_md = Path(tmp) / "transcript.md"
        _pinned_run([*scp, f"{user}@{host}:{remote_json}", str(local_json)], timeout=120, step="result.json download")
        _pinned_run([*scp, f"{user}@{host}:{remote_md}", str(local_md)], timeout=120, step="transcript.md download")
        payload = json.loads(local_json.read_text(encoding="utf-8"))
        elapsed = time.monotonic() - started
        hourly = float(getattr(settings, "pinned_hourly_usd", 0.0) or 0.0)
        return TranscribeResult(
            transcript_md=local_md.read_text(encoding="utf-8"),
            detected_language=str(payload.get("detected_language") or "unknown"),
            duration_seconds=payload.get("duration_seconds"),
            backend=str(payload.get("backend") or "pinned-gpu"),
            vast_instance_id=int(settings.pinned_vast_id),
            vast_cost=hourly * elapsed / 3600 if hourly else 0.0,
        )


# --- market rentals -----------------------------------------------------
@dataclass(frozen=True)
class _ReadyInstance:
    attempt: VastAttempt
    rental_started: float
    deadline: float
    max_cost: float
    endpoints: list[tuple[str, int]]


def _finish_market_rental(
    context: _TranscribeRunContext, ledger: VastLedger, attempt_store: VastAttemptStore | None,
    attempt: VastAttempt, rental_started: float, *, outcome: str, exclude_host: bool,
    require_destroy: bool = False,
) -> bool:
    destroyed = False
    attempt = replace(attempt, outcome=outcome, exclude_host=exclude_host)
    try:
        # Recovery must know a replacement is owed even if we exit just
        # after the destroy callback and before finalizing the event.
        if outcome == "ssh_transport_error" and attempt_store is not None:
            attempt_store.record(attempt)
    finally:
        try:
            destroyed = context.destroy_instance() or context.was_destroyed(attempt.instance_id)
        except Exception as exc:
            if require_destroy:
                raise
            # The sweep retries teardown. A finished transcription is still usable.
            print(f"Warning: Vast destroy after transcription failed (sweep will retry): {exc}", file=sys.stderr)
        finally:
            finished = replace(
                attempt, finished_at=datetime.now(UTC), destroyed=destroyed,
                estimated_cost_usd=attempt.hourly_usd * (time.monotonic() - rental_started) / 3600,
            )
            if attempt_store is not None:
                attempt_store.record(finished)
            ledger.attempts.append(finished)
    return destroyed


def _rent_ready_instance(
    context: _TranscribeRunContext, api_key: str, key_path: Path, public_key: str,
    *, max_job_cost: float, market_deadline: float, ledger: VastLedger,
    attempt_store: VastAttemptStore | None, on_instance_created: Callable[[int], None],
    before_retry_rent: Callable[[], None] | None,
) -> _ReadyInstance:
    ready_timeout = int(settings.vast_instance_ready_timeout)
    offer_attempts = max(1, int(settings.vast_offer_attempts))
    offers = _select_offers(
        api_key,
        max_price=float(settings.vast_max_price_per_hour),
        gpu_regex=settings.vast_gpu_regex,
        min_cuda=float(settings.vast_min_cuda),
    )
    last_err: Exception | None = None
    attempts = 0
    vanished = 0
    for offer in offers:
        if attempts >= offer_attempts:
            break
        offer_id = int(offer["id"])
        if any(attempt.offer_id == offer_id for attempt in ledger.attempts):
            continue
        raw_host_id = offer.get("host_id")
        try:
            offer_host_id = int(raw_host_id) if raw_host_id is not None else None
        except (TypeError, ValueError):
            offer_host_id = None
        if offer_host_id is not None and offer_host_id in ledger.excluded_hosts:
            continue

        context.raise_if_cancelled()
        offer_label = f"offer_id={offer_id} host_id={offer_host_id}"
        price = float(offer.get("dph_total") or 0)
        remaining_cost = max_job_cost - ledger.spent_usd
        rental_started = time.monotonic()
        deadline = min(market_deadline, _budget_deadline(rental_started, price, remaining_cost, MAX_INSTANCE_SECONDS))
        if deadline <= rental_started or (before_retry_rent is not None and deadline - rental_started < _MIN_RERUN_SECONDS):
            last_err = VastBudgetExceededError("remaining time or spend budget is too small for this Vast offer")
            continue
        rented_at = datetime.now(UTC)
        retry_label = None
        if before_retry_rent is not None:
            before_retry_rent()
            retry_label = f"rerun-scribe-whisper-{rented_at:%Y%m%dT%H%M%SZ}-{uuid4().hex}"
            if attempt_store is not None and not attempt_store.reserve_retry(retry_label, offer_id, offer_host_id, price):
                raise WhisperError("Vast SSH rerun has already been reserved for this job")
            # The durable reservation happens before the non-idempotent PUT.
            # If it consumed the remaining retry window, leave the slot spent.
            before_retry_rent()
            if deadline - time.monotonic() < _MIN_RERUN_SECONDS:
                raise VastBudgetExceededError("remaining time or spend budget is too small for this Vast offer")

        try:
            if retry_label is not None:
                instance_id = _create_instance(api_key, offer, public_key, label=retry_label)
            else:
                instance_id = _create_instance(api_key, offer, public_key)
        except Exception as exc:
            if retry_label is not None:
                if attempt_store is not None:
                    try:
                        attempt_store.reconcile_retry()
                    except Exception:
                        print("Warning: could not reconcile Vast rerun; reaper will retry", file=sys.stderr)
                raise
            if not isinstance(exc, (WhisperError, TimeoutError)):
                raise
            last_err = exc
            if _is_no_such_ask(exc):
                # A vanished offer created no rental. Bound churn and pace PUTs.
                print(f"Notice: Vast offer {offer_id} vanished (no_such_ask); trying next", file=sys.stderr)
                vanished += 1
                if vanished >= offer_attempts * 3:
                    print("Notice: too many vanished Vast offers; stopping this round", file=sys.stderr)
                    break
                _sleep(1.0)
                continue
            attempts += 1
            print(f"Warning: Vast offer {offer_id} unusable: {exc}", file=sys.stderr)
            continue

        attempts += 1
        attempt = VastAttempt(
            number=2 if before_retry_rent is not None else 1,
            instance_id=instance_id, offer_id=offer_id, host_id=offer_host_id,
            started_at=rented_at, hourly_usd=price,
        )
        endpoints: list[tuple[str, int]] = []
        try:
            context.set_instance(instance_id)
            if attempt_store is not None:
                attempt_store.record(attempt)
            else:
                on_instance_created(instance_id)
            context.raise_if_cancelled()
            ready_started = time.monotonic()
            startup_deadline = min(deadline, ready_started + ready_timeout)
            host, port = _wait_for_ssh(
                api_key, instance_id, key_path, rental_started, startup_deadline, price, remaining_cost,
                ready_timeout=ready_timeout, ready_started=ready_started, label=offer_label,
                endpoints=endpoints,
            )
            _wait_remote_ready(
                api_key, instance_id, host, port, key_path, rental_started, startup_deadline, price, remaining_cost,
                ready_timeout=ready_timeout, ready_started=ready_started, label=offer_label,
            )
            return _ReadyInstance(attempt, rental_started, deadline, remaining_cost, endpoints or [(host, port)])
        except Exception as exc:
            last_err = exc
            print(f"Warning: Vast offer {offer_id} unusable: {exc} ({offer_label})", file=sys.stderr)
            # An expired job budget says nothing about another offer from this host.
            _finish_market_rental(
                context, ledger, attempt_store, attempt, rental_started,
                outcome="startup_failed", require_destroy=True,
                exclude_host=isinstance(exc, (WhisperError, TimeoutError))
                and not isinstance(exc, (VastBudgetExceededError, TranscribeTimeoutError)),
            )
            if retry_label is not None or isinstance(exc, TranscribeTimeoutError) or not isinstance(exc, (WhisperError, TimeoutError)):
                raise

    context.raise_if_cancelled()
    raise WhisperError(
        f"no Vast instance became ready; last error: {last_err}; "
        f"blacklisted host_ids={sorted(ledger.excluded_hosts)}"
    )


def _transcribe_on_instance(
    context: _TranscribeRunContext, rental: _ReadyInstance, key_path: Path,
    wav: Path, *, title: str, source_url: str, model_size: str, compute_type: str,
    language: str, beam_size: int,
) -> TranscribeResult:
    endpoints = rental.endpoints
    host, port = endpoints[0]
    started, deadline, price = rental.rental_started, rental.deadline, rental.attempt.hourly_usd
    max_job_cost = rental.max_cost
    with tempfile.TemporaryDirectory(prefix="scribe-whisper-") as tmp:
        tmpdir = Path(tmp)
        remote_script = tmpdir / "remote_transcribe.py"
        remote_script.write_text(REMOTE_TRANSCRIBE_SCRIPT, encoding="utf-8")
        local_json = tmpdir / "result.json"
        local_md = tmpdir / "transcript.md"

        _ensure_budget(started, deadline, price, max_job_cost)
        context.raise_if_cancelled()
        _run(
            [*_ssh_base(host, port, key_path), "mkdir -p /root/work /root/out"],
            timeout=min(45, max(0.001, deadline - time.monotonic())),
        )
        for local_file, remote_file in (
            (remote_script, "/root/remote_transcribe.py"), (wav, "/root/work/input-16k.wav"),
        ):
            host, port = _transfer_with_retry(
                context,
                lambda h, p, timeout, src=local_file, dst=remote_file: _scp_to(
                    h, p, key_path, src, dst, timeout=timeout,
                ),
                endpoints, deadline=deadline, max_seconds=600, label=remote_file,
            )

        context.raise_if_cancelled()
        cmd = (
            "cd /root && /opt/video-summary-venv/bin/python remote_transcribe.py "
            f"--audio-file work/input-16k.wav "
            f"--title {shlex.quote(title)} "
            f"--source-url {shlex.quote(source_url)} "
            f"--model-size {shlex.quote(model_size)} "
            f"--compute-type {shlex.quote(compute_type)} "
            f"--language {shlex.quote(language)} "
            f"--beam-size {int(beam_size)} "
            "--output-json out/result.json --output-markdown out/transcript.md"
        )
        _ensure_budget(started, deadline, price, max_job_cost)
        remote_timeout = max(0.001, deadline - time.monotonic())
        _run([*_ssh_base(host, port, key_path), cmd], timeout=remote_timeout)
        context.raise_if_cancelled()
        for remote_file, local_file in (
            ("/root/out/result.json", local_json), ("/root/out/transcript.md", local_md),
        ):
            host, port = _transfer_with_retry(
                context,
                lambda h, p, timeout, src=remote_file, dst=local_file: _scp_from(
                    h, p, key_path, src, dst, timeout=timeout,
                ),
                endpoints, deadline=deadline, max_seconds=120, label=remote_file,
            )

        result = json.loads(local_json.read_text(encoding="utf-8"))
        return TranscribeResult(
            transcript_md=local_md.read_text(encoding="utf-8"),
            detected_language=str(result.get("detected_language") or "unknown"),
            duration_seconds=result.get("duration_seconds"),
            backend=str(result.get("backend") or ""),
            vast_instance_id=rental.attempt.instance_id,
            vast_cost=0.0,
        )


# --- public API ---------------------------------------------------------
def _transcribe_impl(
    context: _TranscribeRunContext,
    wav: Path, *, title: str, source_url: str,
    model_size: str = "large-v3-turbo", compute_type: str = "float16",
    language: str = "auto", beam_size: int = 5,
    on_instance_created: Callable[[int], None] = _noop_instance_created,
    check_monthly_cap: Callable[[], None] | None = None,
    attempt_store: VastAttemptStore | None = None,
    can_retry: Callable[[], bool] | None = None,
) -> TranscribeResult:
    """Transcribe a 16 kHz mono wav on a fresh Vast.ai GPU instance."""
    api_key = settings.vast_api_key.strip()
    if not api_key:
        raise WhisperError("SCRIBE_VAST_API_KEY is not set")
    context.set_api_key(api_key)
    context.raise_if_cancelled()
    if check_monthly_cap is not None:
        check_monthly_cap()
    if _pinned_configured():
        try:
            return _transcribe_pinned(
                wav,
                title=title,
                source_url=source_url,
                model_size=model_size,
                compute_type=compute_type,
                language=language,
                beam_size=beam_size,
            )
        except PinnedGpuBusy as exc:
            print(
                f"Notice: pinned GPU busy ({exc}); falling back to market Vast",
                file=sys.stderr,
            )
        except PinnedGpuUnavailable as exc:
            print(
                f"Notice: pinned GPU unavailable ({exc}); falling back to market Vast",
                file=sys.stderr,
            )
    ledger = attempt_store.load() if attempt_store is not None else VastLedger()
    if ledger.retry_reserved:
        raise WhisperError("Vast SSH rerun has already been reserved for this job")
    if any(not attempt.destroyed for attempt in ledger.attempts):
        raise WhisperError("Vast cleanup is still pending for this job")
    key_path, public_key = _ensure_local_ssh_key()
    _ensure_vast_ssh_key(api_key, public_key)
    market_seconds = min(settings.transcribe_timeout_secs, MAX_INSTANCE_SECONDS)
    if ledger.attempts:
        original_start = min(attempt.started_at for attempt in ledger.attempts)
        market_seconds -= max(0.0, (datetime.now(UTC) - original_start).total_seconds())
    market_deadline = min(context.deadline, time.monotonic() + market_seconds)
    max_job_cost = float(settings.vast_max_job_cost)
    first_failure: SSHTransportError | None = None
    if any(attempt.outcome == "ssh_transport_error" for attempt in ledger.attempts):
        # The first drop was committed before shutdown, but there is no
        # reservation yet. The next rental is still the single replacement.
        first_failure = SSHTransportError("Vast SSH transport failed in the previous worker run")

    def before_retry_rent() -> None:
        context.raise_if_cancelled()
        if first_failure is not None and (
            market_deadline - time.monotonic() < _MIN_RERUN_SECONDS
            or (can_retry is not None and not can_retry())
        ):
            raise first_failure

    attempt_numbers = (2,) if first_failure is not None else (1, 2)
    for number in attempt_numbers:
        if number == 2:
            before_retry_rent()
            if check_monthly_cap is not None:
                check_monthly_cap()
        rental = _rent_ready_instance(
            context, api_key, key_path, public_key,
            max_job_cost=max_job_cost, market_deadline=market_deadline, ledger=ledger,
            attempt_store=attempt_store, on_instance_created=on_instance_created,
            before_retry_rent=before_retry_rent if number == 2 else None,
        )

        outcome = "failed"
        try:
            result = _transcribe_on_instance(
                context, rental, key_path, wav, title=title, source_url=source_url,
                model_size=model_size, compute_type=compute_type, language=language,
                beam_size=beam_size,
            )
            outcome = "success"
        except SSHTransportError as exc:
            outcome = "ssh_transport_error"
            if number == 2:
                raise
            first_failure = exc
        finally:
            destroyed = _finish_market_rental(
                context, ledger, attempt_store, rental.attempt, rental.rental_started,
                outcome=outcome, exclude_host=outcome == "ssh_transport_error",
            )
        if outcome == "success":
            result.vast_cost = ledger.spent_usd
            return result

        # The job's sole instance ID still belongs to any unconfirmed rental.
        if not destroyed or ledger.spent_usd >= max_job_cost:
            assert first_failure is not None
            raise first_failure

    raise AssertionError("Vast transcription attempts exhausted without an outcome")


def transcribe(
    wav: Path, *, title: str, source_url: str,
    model_size: str = "large-v3-turbo", compute_type: str = "float16",
    language: str = "auto", beam_size: int = 5,
    on_instance_created: Callable[[int], None] | None = None,
    on_destroy_failed: Callable[[int], None] | None = None,
    on_destroy_succeeded: Callable[[int], None] | None = None,
    check_monthly_cap: Callable[[], None] | None = None,
    attempt_store: VastAttemptStore | None = None,
    can_retry: Callable[[], bool] | None = None,
) -> TranscribeResult:
    """Transcribe a 16 kHz mono wav on a fresh Vast.ai GPU instance."""
    timeout_secs = settings.transcribe_timeout_secs
    if timeout_secs <= 0:
        raise WhisperError("SCRIBE_TRANSCRIBE_TIMEOUT_SECS must be greater than 0")

    context = _TranscribeRunContext(
        on_destroy_failed=on_destroy_failed,
        on_destroy_succeeded=on_destroy_succeeded,
    )
    notify_instance_created = on_instance_created or (lambda _instance_id: None)
    results: queue.Queue[TranscribeResult | BaseException] = queue.Queue(maxsize=1)

    def run() -> None:
        try:
            result = _transcribe_impl(
                context,
                wav,
                title=title,
                source_url=source_url,
                model_size=model_size,
                compute_type=compute_type,
                language=language,
                beam_size=beam_size,
                on_instance_created=notify_instance_created,
                check_monthly_cap=check_monthly_cap,
                attempt_store=attempt_store,
                can_retry=can_retry,
            )
        except BaseException as exc:
            result = exc
        try:
            results.put_nowait(result)
        except queue.Full:
            pass

    thread = threading.Thread(target=run, name="scribe-transcribe-wallclock", daemon=True)
    thread.start()
    try:
        result = results.get(timeout=timeout_secs)
    except queue.Empty as exc:
        context.cancel()
        try:
            context.destroy_instance()
        except Exception as destroy_exc:
            # Keep the timeout as the job's error; cleanup is the sweep's job.
            print(
                f"Warning: Vast destroy on timeout failed (sweep will retry): {destroy_exc}",
                file=sys.stderr,
            )
        raise TranscribeTimeoutError(f"transcribe timed out after {timeout_secs}s") from exc
    if isinstance(result, BaseException):
        # A subprocess can reach the absolute deadline before Queue.get's
        # timer fires. Preserve the whole-job hard stop in that race instead
        # of classifying its generic command error as provider unavailability.
        context.raise_if_cancelled()
        raise result
    return result
