"""Prompt-template storage for transcript summaries."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
PROMPT_NAME = "transcript-summary"
VALID_VERSIONS = ("v1", "v2", "v3")
DEFAULT_ACTIVE_VERSION = "v3"
MAX_TEMPLATE_CHARS = 16 * 1024

_HEADER_RE = re.compile(r"^##\s+\S+", re.MULTILINE)


class PromptError(RuntimeError):
    pass


class UnknownPromptVersionError(PromptError):
    pass


class PromptValidationError(PromptError):
    pass


@dataclass(frozen=True)
class PromptTemplateInfo:
    id: str
    len_chars: int
    len_tokens_est: int
    first_line: str
    is_active: bool = False


def _template_path(version: str) -> Path:
    return PROMPTS_DIR / f"{PROMPT_NAME}.{version}.md"


def _active_path() -> Path:
    return PROMPTS_DIR / f"{PROMPT_NAME}.active"


def require_version(version: str) -> str:
    if version not in VALID_VERSIONS:
        raise UnknownPromptVersionError(f"unknown prompt version: {version}")
    return version


def _atomic_write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(body, encoding="utf-8")
    os.replace(tmp_path, path)


def validate_template_body(body: str) -> None:
    if len(body) > MAX_TEMPLATE_CHARS:
        raise PromptValidationError(f"prompt template must be <= {MAX_TEMPLATE_CHARS} chars")
    if "## TL;DR" not in body:
        raise PromptValidationError("prompt template must contain a '## TL;DR' section")
    if _HEADER_RE.search(body) is None:
        raise PromptValidationError("prompt template must contain markdown '## ' headers")


def read_template(version: str) -> str:
    require_version(version)
    path = _template_path(version)
    if not path.is_file():
        raise UnknownPromptVersionError(f"prompt template missing: {path}")
    return path.read_text(encoding="utf-8")


def write_template(version: str, body: str) -> PromptTemplateInfo:
    require_version(version)
    validate_template_body(body)
    _atomic_write(_template_path(version), body)
    return template_info(version, active_version=read_active_version())


def read_active_version() -> str:
    path = _active_path()
    if not path.is_file():
        return DEFAULT_ACTIVE_VERSION
    active = path.read_text(encoding="utf-8").strip()
    require_version(active)
    return active


def write_active_version(version: str) -> str:
    require_version(version)
    read_template(version)
    _atomic_write(_active_path(), f"{version}\n")
    return version


def template_info(version: str, *, active_version: str | None = None) -> PromptTemplateInfo:
    body = read_template(version)
    first_line = next((line.strip() for line in body.splitlines() if line.strip()), "")
    active = active_version if active_version is not None else read_active_version()
    return PromptTemplateInfo(
        id=version,
        len_chars=len(body),
        len_tokens_est=max(1, len(body) // 4),
        first_line=first_line,
        is_active=version == active,
    )


def list_templates() -> tuple[str, list[PromptTemplateInfo]]:
    active = read_active_version()
    return active, [template_info(version, active_version=active) for version in VALID_VERSIONS]
