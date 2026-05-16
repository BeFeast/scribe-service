"""Versioned summarizer prompt templates stored on disk."""
from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

from scribe.config import settings

PROMPT_NAME = "transcript-summary"
PROMPT_VERSIONS = ("v1", "v2", "v3")
MAX_PROMPT_CHARS = 16 * 1024
REQUIRED_PLACEHOLDERS = ("{date}", "{transcript_slug}")

_BUNDLED_PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


class PromptError(ValueError):
    pass


@dataclass(frozen=True)
class PromptVersion:
    id: str
    body: str
    is_active: bool = False

    @property
    def len_chars(self) -> int:
        return len(self.body)

    @property
    def len_tokens_est(self) -> int:
        return max(1, (len(self.body) + 3) // 4)

    @property
    def first_line(self) -> str:
        for line in self.body.splitlines():
            if line.strip():
                return line.strip()
        return ""


def prompt_dir() -> Path:
    configured = settings.prompt_dir.strip()
    return Path(configured) if configured else _BUNDLED_PROMPT_DIR


def _version_path(version: str) -> Path:
    validate_version(version)
    return prompt_dir() / f"{PROMPT_NAME}.{version}.md"


def _active_path() -> Path:
    return prompt_dir() / f"{PROMPT_NAME}.active"


def validate_version(version: str) -> None:
    if version not in PROMPT_VERSIONS:
        raise PromptError(f"unknown prompt version {version!r}; expected one of {', '.join(PROMPT_VERSIONS)}")


def active_version() -> str:
    try:
        version = _active_path().read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise PromptError(f"active prompt selector missing: {_active_path()}") from exc
    validate_version(version)
    return version


def read_prompt(version: str) -> str:
    path = _version_path(version)
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise PromptError(f"prompt template missing: {path}") from exc


def read_active_prompt() -> tuple[str, str]:
    version = active_version()
    return version, read_prompt(version)


def list_prompts() -> tuple[str, list[PromptVersion]]:
    active = active_version()
    return active, [
        PromptVersion(id=version, body=read_prompt(version), is_active=version == active)
        for version in PROMPT_VERSIONS
    ]


def validate_prompt_body(body: str) -> None:
    if len(body) > MAX_PROMPT_CHARS:
        raise PromptError(f"prompt body must be <= {MAX_PROMPT_CHARS} characters")
    if "## TL;DR" not in body:
        raise PromptError("prompt body must contain a '## TL;DR' section")
    headers = [line for line in body.splitlines() if line.startswith("## ")]
    if len(headers) < 2:
        raise PromptError("prompt body must contain at least two level-2 markdown headers")
    missing = [placeholder for placeholder in REQUIRED_PLACEHOLDERS if placeholder not in body]
    if missing:
        raise PromptError(f"prompt body must contain placeholders: {', '.join(missing)}")


def _atomic_write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp_path.write_text(body, encoding="utf-8")
        os.replace(tmp_path, path)
    finally:
        tmp_path.unlink(missing_ok=True)


def write_prompt(version: str, body: str) -> None:
    validate_version(version)
    validate_prompt_body(body)
    _atomic_write(_version_path(version), body)


def set_active_version(version: str) -> None:
    validate_version(version)
    if not _version_path(version).is_file():
        raise PromptError(f"prompt template missing: {_version_path(version)}")
    _atomic_write(_active_path(), f"{version}\n")
