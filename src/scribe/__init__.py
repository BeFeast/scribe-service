"""scribe — self-hosted YouTube video-summary service.

Design: HomeLab/Projects/video-summary-service-design-2026-05-14.md (Obsidian vault).
Obsidian-agnostic: scribe owns URL -> transcript -> summary -> DB + API + web-UI.
Consumers (the shtrudel openclaw skill) handle Telegram delivery and Obsidian writes.
"""

__version__ = "0.1.0"
