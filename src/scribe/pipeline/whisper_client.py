"""Vast.ai whisper client — GPU transcription only. TODO(task#5).

Port Vast plumbing from run_vast_video_summary.py: load_vast_api_key,
select_offers (+ cuda_max_good>=12.4 filter), create_instance, wait_for_ssh,
wait_remote_ready, scp_to/from, destroy_instance, budget guards.
Flow: create -> ship wav -> remote_transcribe.py -> fetch transcript -> destroy.
"""
from pathlib import Path


def transcribe(wav: Path) -> dict:
    """Return {transcript_md, lang, duration, ...}."""
    raise NotImplementedError("task#5")
