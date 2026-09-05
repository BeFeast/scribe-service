"""Vast.ai whisper client — GPU transcription only.

Ported from run_vast_video_summary.py. In the scribe architecture Vast does
*only* faster-whisper: download + ffmpeg run locally on a residential IP, and
the 16 kHz mono wav is shipped here for GPU transcription.

Adds a cuda_max_good >= 12.4 offer filter so we never land on a host whose
NVIDIA driver cannot run the CUDA 12.4 image (the "CUDA failed: unsupported
display driver / cuda driver combination" failure seen on 2026-05-14).
"""
from __future__ import annotations

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
from dataclasses import dataclass
from pathlib import Path

from scribe.config import settings

VAST_API = "https://console.vast.ai/api/v0"
VAST_IMAGE = "ghcr.io/befeast/scribe-service-vast:cuda12.4-whisper"
# Hard upper bound on a single instance's wall-clock budget; the per-job cost
# guard (settings.vast_max_job_cost) usually trips well before this.
MAX_INSTANCE_SECONDS = 1800
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


class VastRateLimitedError(WhisperError):
    """Vast API kept returning HTTP 429 after the client's bounded retries.

    Distinct type (rather than substring matching on the message) so the
    provider layer can classify sustained rate-limit pressure as usage_limit
    without misreading aggregated errors that merely embed "HTTP 429"."""


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

    def destroy_instance(self) -> None:
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
            if self._on_destroy_succeeded is not None:
                self._on_destroy_succeeded(instance_id)


# --- subprocess + http helpers ------------------------------------------
def _run(cmd: list[str], *, check: bool = True, timeout: float | None = None) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        if check:
            raise WhisperError(f"command timed out after {timeout}s: {' '.join(cmd)}") from exc
        return subprocess.CompletedProcess(cmd, 124, stdout=exc.stdout or "", stderr=f"timeout after {timeout}s")
    if check and proc.returncode != 0:
        raise WhisperError(
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
        "rentable": {"eq": True}, "rented": {"eq": False}, "verified": {"eq": True},
        "gpu_ram": {"gte": 16000}, "num_gpus": {"eq": 1},
        # Fractional slices still advertise full gpu_ram; require a whole GPU
        # so large-v3-turbo does not OOM-kill the container mid-SSH (#421).
        "gpu_frac": {"eq": 1.0},
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
def _create_instance(api_key: str, offer: dict, public_key: str) -> int:
    label = f"{socket.gethostname()}-scribe-whisper-" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
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


def _destroy_instance(api_key: str, instance_id: int) -> None:
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
        "ssh", "-q", "-i", str(key_path),
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
        except (WhisperError, TimeoutError) as exc:
            context.raise_if_cancelled()
            if isinstance(exc, (TranscribeTimeoutError, VastBudgetExceededError)):
                raise
            message = str(exc).lower()
            recoverable = isinstance(exc, TimeoutError) or any(marker in message for marker in (
                "timed out", "connection", "broken pipe", "network is unreachable",
                "no route to host", "command failed (255)",
            ))
            if not recoverable or attempt == 2:
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


# --- public API ---------------------------------------------------------
def _transcribe_impl(
    context: _TranscribeRunContext,
    wav: Path, *, title: str, source_url: str,
    model_size: str = "large-v3-turbo", compute_type: str = "float16",
    language: str = "auto", beam_size: int = 5,
    on_instance_created: Callable[[int], None] = _noop_instance_created,
    check_monthly_cap: Callable[[], None] | None = None,
) -> TranscribeResult:
    """Transcribe a 16 kHz mono wav on a fresh Vast.ai GPU instance."""
    api_key = settings.vast_api_key.strip()
    if not api_key:
        raise WhisperError("SCRIBE_VAST_API_KEY is not set")
    context.set_api_key(api_key)
    context.raise_if_cancelled()
    if check_monthly_cap is not None:
        check_monthly_cap()
    max_price = float(settings.vast_max_price_per_hour)
    min_cuda = float(settings.vast_min_cuda)
    max_job_cost = float(settings.vast_max_job_cost)
    ready_timeout = int(settings.vast_instance_ready_timeout)
    offer_attempts = max(1, int(settings.vast_offer_attempts))
    key_path, public_key = _ensure_local_ssh_key()
    _ensure_vast_ssh_key(api_key, public_key)
    offers = _select_offers(
        api_key,
        max_price=max_price,
        gpu_regex=settings.vast_gpu_regex,
        min_cuda=min_cuda,
    )
    context.raise_if_cancelled()

    started = time.monotonic()
    instance_id: int | None = None
    host = port = None
    endpoints: list[tuple[str, int]] = []
    price = 0.0
    deadline = started + MAX_INSTANCE_SECONDS
    last_err: Exception | None = None
    attempts = 0
    vanished = 0
    # Per-job host blacklist: hosts whose offers failed to start in this run
    # are skipped on subsequent attempts so we don't pick a sibling offer
    # from the same broken physical box (e.g. NVIDIA driver mismatch).
    excluded_hosts: set[int] = set()
    for offer in offers:
        if attempts >= offer_attempts:
            break
        host_id_raw = offer.get("host_id")
        try:
            offer_host_id: int | None = int(host_id_raw) if host_id_raw is not None else None
        except (TypeError, ValueError):
            offer_host_id = None
        if offer_host_id is not None and offer_host_id in excluded_hosts:
            print(
                f"Notice: Vast offer {offer.get('id')} skipped (host_id {offer_host_id} blacklisted in this job)",
                file=sys.stderr,
            )
            continue
        offer_label = f"offer_id={offer.get('id')} host_id={offer_host_id}"
        price = float(offer.get("dph_total") or 0)
        deadline = min(context.deadline, _budget_deadline(started, price, max_job_cost, MAX_INSTANCE_SECONDS))
        try:
            context.raise_if_cancelled()
            instance_id = _create_instance(api_key, offer, public_key)
        except (WhisperError, TimeoutError) as exc:
            last_err = exc
            if _is_no_such_ask(exc):
                # Offer→ask race: another tenant rented this offer between
                # _select_offers and PUT /asks/{id}/. Don't burn an attempt
                # slot or the ready-timeout budget — try the next candidate.
                print(
                    f"Notice: Vast offer {offer.get('id')} vanished (no_such_ask); trying next",
                    file=sys.stderr,
                )
                instance_id = None
                vanished += 1
                if vanished >= offer_attempts * 3:
                    print(
                        "Notice: too many vanished Vast offers; stopping this round",
                        file=sys.stderr,
                    )
                    break
                # Pace the create endpoint: back-to-back PUT /asks/ across a
                # contended market is a self-inflicted rate-limit burst.
                _sleep(1.0)
                continue
            attempts += 1
            print(f"Warning: Vast offer {offer.get('id')} unusable: {exc}", file=sys.stderr)
            instance_id = None
            continue
        attempts += 1
        try:
            on_instance_created(instance_id)
            context.set_instance(instance_id)
            context.raise_if_cancelled()
            # ready_timeout bounds a *single* offer's startup window, so
            # measure it from this attempt, not the cumulative job start.
            attempt_started = time.monotonic()
            startup_deadline = min(deadline, attempt_started + ready_timeout)
            host, port = _wait_for_ssh(
                api_key, instance_id, key_path, started, startup_deadline, price, max_job_cost,
                ready_timeout=ready_timeout, ready_started=attempt_started, label=offer_label,
                endpoints=endpoints,
            )
            _wait_remote_ready(
                api_key, instance_id, host, port, key_path, started, startup_deadline, price, max_job_cost,
                ready_timeout=ready_timeout, ready_started=attempt_started, label=offer_label,
            )
            break
        except (WhisperError, TimeoutError, TranscribeTimeoutError) as exc:
            last_err = exc
            print(f"Warning: Vast offer {offer.get('id')} unusable: {exc} ({offer_label})", file=sys.stderr)
            # Blacklist the host only for host-side failures: a container
            # that failed to start, a ready_timeout, or a create-time API
            # error. A cost-cap (VastBudgetExceededError) is a job-budget
            # condition, not a bad host — don't skip a healthy host's
            # sibling offers. Vanished offers (no_such_ask) already
            # `continue`d above. TranscribeTimeoutError is the wall-clock
            # guard and aborts the whole job.
            if offer_host_id is not None and not isinstance(
                exc, (TranscribeTimeoutError, VastBudgetExceededError)
            ):
                excluded_hosts.add(offer_host_id)
            if instance_id is not None:
                # Deliberately NOT wrapped: the job has a single bookkeeping
                # slot (vast_instance_id/destroy_failed_at), so if this
                # destroy fails and we moved on to another offer, the next
                # on_instance_created would overwrite this instance's record
                # and orphan it silently. Failing the job keeps the sweep +
                # reaper pointed at the right instance; with retries in the
                # client a failure here already signals a persistent outage.
                context.destroy_instance()
                instance_id = None
            host = port = None
            if isinstance(exc, TranscribeTimeoutError):
                raise
    if instance_id is None or host is None:
        raise WhisperError(
            f"no Vast instance became ready; last error: {last_err}; "
            f"blacklisted host_ids={sorted(excluded_hosts) if excluded_hosts else '[]'}"
        )

    try:
        with tempfile.TemporaryDirectory(prefix="scribe-whisper-") as tmp:
            tmpdir = Path(tmp)
            remote_script = tmpdir / "remote_transcribe.py"
            remote_script.write_text(REMOTE_TRANSCRIBE_SCRIPT, encoding="utf-8")
            local_json = tmpdir / "result.json"
            local_md = tmpdir / "transcript.md"

            if not endpoints:
                endpoints = [(host, port)]
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
            elapsed = time.monotonic() - started
            return TranscribeResult(
                transcript_md=local_md.read_text(encoding="utf-8"),
                detected_language=str(result.get("detected_language") or "unknown"),
                duration_seconds=result.get("duration_seconds"),
                backend=str(result.get("backend") or ""),
                vast_instance_id=instance_id,
                vast_cost=price * elapsed / 3600 if price else 0.0,
            )
    finally:
        try:
            context.destroy_instance()
        except Exception as destroy_exc:
            # A rate-limited teardown must not discard a finished (paid)
            # transcription; the destroy sweep + reaper own the retry.
            print(
                f"Warning: Vast destroy after transcription failed (sweep will retry): {destroy_exc}",
                file=sys.stderr,
            )


def transcribe(
    wav: Path, *, title: str, source_url: str,
    model_size: str = "large-v3-turbo", compute_type: str = "float16",
    language: str = "auto", beam_size: int = 5,
    on_instance_created: Callable[[int], None] | None = None,
    on_destroy_failed: Callable[[int], None] | None = None,
    on_destroy_succeeded: Callable[[int], None] | None = None,
    check_monthly_cap: Callable[[], None] | None = None,
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
