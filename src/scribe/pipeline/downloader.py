"""yt-dlp downloader — runs locally (residential IP). TODO(task#3).

Port from run_vast_video_summary.py remote_shell_script:
  client-fallback chain mweb,web_safari,android_vr,web_embedded + format selector
  + run_ytdlp retry. No bgutil (android_vr is token-free on residential IP).
"""
from pathlib import Path


def download_audio(url: str, dest_dir: Path) -> Path:
    """Download the audio track, return path to the audio file."""
    raise NotImplementedError("task#3")
