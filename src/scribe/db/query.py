"""Small SQL query helpers shared by API and web views."""
from __future__ import annotations


def escape_like(value: str) -> str:
    """Escape SQL LIKE wildcards so user-supplied `%` / `_` match literally."""
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
