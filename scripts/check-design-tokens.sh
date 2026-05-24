#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ROOT}/web/spa/src"

# Production SPA sources should use design tokens, literal approved hex values,
# or semantic classes. Raw framework color families make later route ports drift.
pattern='(bg|text|border|ring|from|to|via|stroke|fill)-(slate|gray|zinc|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose)-[0-9]{2,3}'

if rg -n --glob '*.{ts,tsx,css,html}' -e "${pattern}" "${TARGET}"; then
	echo "Design token check failed: raw framework color utility classes are not allowed." >&2
	exit 1
fi

uv run --no-project python - "${ROOT}/design/scribe-redesign-2026-05-24/app/styles.css" "${ROOT}/web/spa/src/styles.css" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])

source_css = re.sub(r"/\*.*?\*/", "", source_path.read_text(encoding="utf-8"), flags=re.S)
target_css = re.sub(r"/\*.*?\*/", "", target_path.read_text(encoding="utf-8"), flags=re.S)

selectors = (
    '[data-density="compact"]',
    '[data-density="cozy"]',
    '[data-density="comfy"]',
    '[data-variant="paper"]',
    '[data-variant="paper"][data-theme="dark"]',
    '[data-variant="terminal"]',
    '[data-variant="terminal"][data-theme="light"]',
    '[data-variant="console"]',
    '[data-variant="console"][data-theme="dark"]',
    '[data-variant="field"]',
    '[data-variant="field"][data-theme="dark"]',
)


def block_for(css: str, selector: str) -> str:
    match = re.search(rf"{re.escape(selector)}\s*\{{", css)
    if match is None:
        raise AssertionError(f"missing selector {selector}")
    start = match.end()
    end = css.find("}", start)
    if end == -1:
        raise AssertionError(f"unterminated selector {selector}")
    return css[start:end]


def normalize_value(value: str) -> str:
    normalized = re.sub(r"\s+", " ", value.strip())
    return re.sub(r"\s*,\s*", ",", normalized)


def vars_for(css: str, selector: str) -> dict[str, str]:
    block = block_for(css, selector)
    return {
        name: normalize_value(value)
        for name, value in re.findall(r"(--[A-Za-z0-9_-]+)\s*:\s*([^;]+);", block)
    }


drift: list[str] = []
for selector in selectors:
    expected = vars_for(source_css, selector)
    actual = vars_for(target_css, selector)
    for name, value in expected.items():
        if actual.get(name) != value:
            drift.append(
                f"{selector} {name}: expected {value!r}, got {actual.get(name)!r}"
            )

if drift:
    print("Design token parity failed:", file=sys.stderr)
    print("\n".join(drift), file=sys.stderr)
    raise SystemExit(1)
PY

echo "Design token check passed."
