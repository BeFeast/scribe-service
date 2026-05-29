"""Vast.ai whisper client — GPU transcription only.

Ported from run_vast_video_summary.py. In the scribe architecture Vast does
*only* faster-whisper: download + ffmpeg run locally on a residential IP, and
the 16 kHz mono wav is shipped here for GPU transcription.

Adds a cuda_max_good >= 12.4 offer filter so we never land on a host whose
NVIDIA driver cannot run the CUDA 12.4 image (the "CUDA failed: unsupported
display driver / cuda driver combination" failure seen on 2026-05-14).
"""
from __future__ import annotations

import json
import queue
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

REMOTE_TRANSCRIBE_SCRIPT = '#!/usr/bin/env -S uv run\n# /// script\n# requires-python = ">=3.10"\n# dependencies = [\n#   "faster-whisper>=1.1.1",\n# ]\n# ///\n\nimport argparse\nimport json\nimport re\nfrom datetime import datetime, timezone\nfrom pathlib import Path\n\nfrom faster_whisper import WhisperModel\n\n\ndef slugify(value: str) -> str:\n    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")\n    return slug or "transcript"\n\n\ndef main() -> int:\n    parser = argparse.ArgumentParser()\n    parser.add_argument("--audio-file", required=True)\n    parser.add_argument("--title", required=True)\n    parser.add_argument("--source-url", required=True)\n    parser.add_argument("--model-size", default="large-v3-turbo")\n    parser.add_argument("--compute-type", default="float16")\n    parser.add_argument("--language", default="auto")\n    parser.add_argument("--beam-size", type=int, default=5)\n    parser.add_argument("--output-json", required=True)\n    parser.add_argument("--output-markdown", required=True)\n    args = parser.parse_args()\n\n    language = None if args.language == "auto" else args.language\n    model = WhisperModel(args.model_size, device="cuda", compute_type=args.compute_type)\n    segments, info = model.transcribe(args.audio_file, language=language, beam_size=args.beam_size, vad_filter=True)\n    collected = list(segments)\n    transcript_text = " ".join(segment.text.strip() for segment in collected if segment.text.strip()).strip()\n    duration = max((segment.end for segment in collected), default=None)\n    detected_language = getattr(info, "language", None) or "unknown"\n    language_probability = getattr(info, "language_probability", None)\n    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")\n    backend = f"faster-whisper ({args.model_size}, {args.compute_type}, cuda)"\n    probability_text = "unknown" if language_probability is None else f"{language_probability:.3f}"\n    duration_text = "unknown" if duration is None else f"{duration:.2f}s"\n\n    markdown = (\n        f"# {args.title}\\n\\n"\n        "## Metadata\\n"\n        f"- Source URL: {args.source_url}\\n"\n        "- Source audio: Vast remote yt-dlp/ffmpeg pipeline\\n"\n        f"- Transcription model: {backend}\\n"\n        f"- Detected language: {detected_language}\\n"\n        f"- Language probability: {probability_text}\\n"\n        f"- Duration: {duration_text}\\n"\n        f"- Generated at: {generated_at}\\n\\n"\n        "## Transcript\\n\\n"\n        f"{transcript_text}\\n"\n    )\n    Path(args.output_markdown).write_text(markdown, encoding="utf-8")\n    Path(args.output_json).write_text(\n        json.dumps(\n            {\n                "title": args.title,\n                "detected_language": detected_language,\n                "language_probability": language_probability,\n                "duration_seconds": duration,\n                "backend": backend,\n                "transcript_characters": len(transcript_text),\n            },\n            ensure_ascii=False,\n        ),\n        encoding="utf-8",\n    )\n    print(f"TITLE:{args.title}")\n    print(f"DETECTED_LANGUAGE:{detected_language}")\n    print(f"TRANSCRIBE_BACKEND:{backend}")\n    print(f"TRANSCRIPT_CHARACTERS:{len(transcript_text)}")\n    return 0\n\n\nif __name__ == "__main__":\n    raise SystemExit(main())\n'


class WhisperError(RuntimeError):
    pass


class TranscribeTimeoutError(WhisperError):
    pass


@dataclass
class TranscribeResult:
    transcript_md: str
    detected_language: str
    duration_seconds: float | None
    backend: str
    vast_instance_id: int
    vast_cost: float


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
        if cancelled:
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
def _run(cmd: list[str], *, check: bool = True, timeout: int | None = None) -> subprocess.CompletedProcess:
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


def _vast(api_key: str, method: str, path: str, payload: dict | None = None, timeout: int = 45) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Authorization": f"Bearer {api_key}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(f"{VAST_API}{path}", data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise WhisperError(f"Vast API {method} {path}: HTTP {exc.code}: {detail}") from exc
    return json.loads(body) if body.strip() else {}


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
) -> list[dict]:
    import re

    query = {
        "limit": 400, "type": "on-demand",
        "rentable": {"eq": True}, "rented": {"eq": False}, "verified": {"eq": True},
        "gpu_ram": {"gte": 16000}, "num_gpus": {"eq": 1},
    }
    offers = _vast(api_key, "POST", "/bundles/", query, timeout=60).get("offers", [])
    pattern = re.compile(gpu_regex, re.IGNORECASE)
    candidates = []
    for offer in offers:
        price = float(offer.get("dph_total") or 999)
        cuda = float(offer.get("cuda_max_good") or 0)
        reliability = float(offer.get("reliability") or offer.get("reliability2") or 0)
        if (price <= max_price and cuda >= min_cuda and reliability >= 0.90
                and pattern.search(str(offer.get("gpu_name") or ""))):
            candidates.append(offer)
    if not candidates:
        raise WhisperError(
            f"no Vast offer matched (max_price={max_price}, cuda_max_good>={min_cuda}, gpu_regex, reliability>=0.90)"
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
    resp = _vast(api_key, "PUT", f"/asks/{offer['id']}/", payload, timeout=60)
    iid = resp.get("new_contract") or resp.get("id") or resp.get("instance_id")
    if not iid:
        raise WhisperError(f"Vast create response missing instance id: {resp}")
    try:
        _vast(api_key, "POST", f"/instances/{iid}/ssh/", {"ssh_key": public_key})
    except Exception:
        pass
    return int(iid)


def _destroy_instance(api_key: str, instance_id: int) -> None:
    _vast(api_key, "DELETE", f"/instances/{instance_id}/", {}, timeout=45)
    try:
        confirm = _vast(api_key, "GET", f"/instances/{instance_id}/", timeout=45)
    except WhisperError as exc:
        if "HTTP 404" in str(exc):
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


def _scp_to(host: str, port: int, key_path: Path, src: Path, target: str) -> None:
    _run(["scp", "-q", "-i", str(key_path), "-o", "StrictHostKeyChecking=accept-new",
          "-P", str(port), str(src), f"root@{host}:{target}"], timeout=600)


def _scp_from(host: str, port: int, key_path: Path, src: str, target: Path) -> None:
    _run(["scp", "-q", "-i", str(key_path), "-o", "StrictHostKeyChecking=accept-new",
          "-P", str(port), f"root@{host}:{src}", str(target)], timeout=120)


# --- budget + waits -----------------------------------------------------
def _budget_deadline(started: float, price: float, max_cost: float, max_seconds: int) -> float:
    by_cost = max_cost / price * 3600 if price > 0 else max_seconds
    return started + min(max_seconds, by_cost)


def _ensure_budget(started: float, deadline: float, price: float, max_cost: float) -> None:
    if time.monotonic() <= deadline:
        return
    elapsed = time.monotonic() - started
    raise WhisperError(
        f"Vast budget guard tripped after {elapsed:.0f}s (~${price * elapsed / 3600:.4f}, cap ${max_cost})"
    )


def _wait_for_ssh(api_key, instance_id, key_path, started, deadline, price, max_cost) -> tuple[str, int]:
    while True:
        _ensure_budget(started, deadline, price, max_cost)
        info = _get_instance(api_key, instance_id)
        states = {str(info.get("actual_status") or "").lower(), str(info.get("cur_state") or "").lower()}
        if "running" in states:
            for host, port, kind in _ssh_endpoints(info):
                if _run([*_ssh_base(host, port, key_path), "true"], check=False, timeout=45).returncode == 0:
                    print(f"Using Vast {kind} SSH endpoint {host}:{port}", file=sys.stderr)
                    return host, port
        time.sleep(10)


def _wait_remote_ready(host, port, key_path, started, deadline, price, max_cost) -> None:
    check = "test -f /root/video-summary-ready && command -v uv >/dev/null && nvidia-smi -L"
    while True:
        _ensure_budget(started, deadline, price, max_cost)
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
    price = 0.0
    deadline = started + MAX_INSTANCE_SECONDS
    last_err: Exception | None = None
    attempts = 0
    for offer in offers:
        if attempts >= offer_attempts:
            break
        price = float(offer.get("dph_total") or 0)
        deadline = _budget_deadline(started, price, max_job_cost, MAX_INSTANCE_SECONDS)
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
            startup_deadline = min(deadline, time.monotonic() + ready_timeout)
            host, port = _wait_for_ssh(api_key, instance_id, key_path, started, startup_deadline, price, max_job_cost)
            _wait_remote_ready(host, port, key_path, started, startup_deadline, price, max_job_cost)
            break
        except (WhisperError, TimeoutError, TranscribeTimeoutError) as exc:
            last_err = exc
            print(f"Warning: Vast offer {offer.get('id')} unusable: {exc}", file=sys.stderr)
            if instance_id is not None:
                context.destroy_instance()
                instance_id = None
            host = port = None
            if isinstance(exc, TranscribeTimeoutError):
                raise
    if instance_id is None or host is None:
        raise WhisperError(f"no Vast instance became ready; last error: {last_err}")

    try:
        with tempfile.TemporaryDirectory(prefix="scribe-whisper-") as tmp:
            tmpdir = Path(tmp)
            remote_script = tmpdir / "remote_transcribe.py"
            remote_script.write_text(REMOTE_TRANSCRIBE_SCRIPT, encoding="utf-8")
            local_json = tmpdir / "result.json"
            local_md = tmpdir / "transcript.md"

            _run([*_ssh_base(host, port, key_path), "mkdir -p /root/work /root/out"], timeout=45)
            _scp_to(host, port, key_path, remote_script, "/root/remote_transcribe.py")
            _scp_to(host, port, key_path, wav, "/root/work/input-16k.wav")

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
            remote_timeout = max(120, int(deadline - time.monotonic()))
            _run([*_ssh_base(host, port, key_path), cmd], timeout=remote_timeout)
            context.raise_if_cancelled()
            _scp_from(host, port, key_path, "/root/out/result.json", local_json)
            _scp_from(host, port, key_path, "/root/out/transcript.md", local_md)

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
        context.destroy_instance()


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
        context.destroy_instance()
        raise TranscribeTimeoutError(f"transcribe timed out after {timeout_secs}s") from exc
    if isinstance(result, BaseException):
        raise result
    return result
