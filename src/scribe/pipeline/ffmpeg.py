"""ffmpeg audio normalization. TODO(task#4)."""
from pathlib import Path


def to_wav_16k_mono(src: Path, dest: Path) -> Path:
    """Resample to 16 kHz mono wav (-ar 16000 -ac 1)."""
    raise NotImplementedError("task#4")
