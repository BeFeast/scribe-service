#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ROOT}/web/spa/src"

# Destructive or blocking UX must go through SPA primitives such as
# ConfirmDialog. Browser-native dialogs cannot be themed or audited.
pattern='(^|[^A-Za-z0-9_$.])((window\.)?(alert|confirm|prompt))[[:space:]]*\('

if rg -n --glob '*.{ts,tsx}' -e "${pattern}" "${TARGET}"; then
	echo "Forbidden primitive check failed: use SPA modal/confirm primitives instead of browser-native dialogs." >&2
	exit 1
fi

echo "Forbidden primitive check passed."
