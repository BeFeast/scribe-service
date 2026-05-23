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

echo "Design token check passed."
