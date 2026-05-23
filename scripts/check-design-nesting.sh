#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ROOT}/web/spa/src"

# Same-tier recipe surfaces must not be nested by default. If a route needs a
# second tier, give it an explicit recipe class such as .detail-section or
# .share-sheet so the hierarchy is reviewable.
if rg -n --glob '*.{tsx,html}' -P -U -e '(?s)<(?:section|article|div)[^>]*className="(?:[^"]*\s)?(pane|card|metric)(?:\s[^"]*)?"[^>]*>\s*<(?:section|article|div)[^>]*className="(?:[^"]*\s)?\1(?:\s[^"]*)?"' "${TARGET}"; then
	echo "Design nesting check failed: nested same-tier pane/card/metric recipe detected." >&2
	exit 1
fi

echo "Design nesting check passed."
