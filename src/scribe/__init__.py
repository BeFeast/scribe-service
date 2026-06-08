"""scribe — self-hosted video-summary service.

Design: HomeLab/Projects/video-summary-service-design-2026-05-14.md (Obsidian vault).
Obsidian-agnostic: scribe owns URL -> transcript -> summary -> DB + API + web-UI.
Consumers (the shtrudel openclaw skill) handle Telegram delivery and Obsidian writes.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Deploy-truth: track the installed package version (driven by pyproject.toml)
    # so /healthz reports the released tag without any source edit.
    __version__ = _pkg_version("scribe")
except PackageNotFoundError:  # pragma: no cover - editable/uninstalled fallback
    __version__ = "0.0.0+unknown"
