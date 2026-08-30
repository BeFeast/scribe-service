#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ROOT}/web/spa/src"

if ! command -v rg >/dev/null 2>&1; then
	echo "Design recipe check requires ripgrep (rg); install ripgrep and retry." >&2
	exit 127
fi

"${ROOT}/scripts/check-design-source-parity.sh"

# Consumers should use exported recipe classes (.pane, .card, .metric, .btn,
# etc.) instead of rebuilding those recipes from utility-class fragments.
recipe_utilities='(rounded|shadow|p-[0-9]|px-[0-9]|py-[0-9]|border-[a-z]|bg-[a-z]|grid-cols-|gap-[0-9])'

if rg -n --glob '*.{ts,tsx}' -e "className=\\{?[\`\"'][^\`\"']*${recipe_utilities}[^\`\"']*[\`\"']" "${TARGET}"; then
	echo "Design recipe check failed: use shared design primitive classes instead of ad-hoc utility recipes." >&2
	exit 1
fi

echo "Design recipe check passed."
