#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET="${ROOT}/web/spa/src"

# Prototype-only iPhone device chrome (status bar, dynamic island,
# home indicator, outer device frame) ships verbatim in the mobile
# design source (Scribe iOS.html) but must NEVER reach production —
# it represents the iOS phone simulator, not the product UI.
# This guard fails the build if any of these prototype-only selectors
# leak into web/spa/src.
#
# /* ... */ block comments and // line comments are stripped before
# matching so prose mentions of these names inside comments do not
# trip the guard.

pattern='(\.statusbar\b|\.island\b|\.home-indicator\b|\.device\b|\bsb-time\b)'

hits="$(
	find "${TARGET}" -type f \
		\( -name '*.ts' -o -name '*.tsx' -o -name '*.jsx' -o -name '*.js' \
		   -o -name '*.css' -o -name '*.html' \) \
		-print0 \
	| python3 -c "
import re, sys, pathlib
data = sys.stdin.buffer.read().split(b'\0')
out = []
for raw in data:
    if not raw:
        continue
    p = pathlib.Path(raw.decode())
    src = p.read_text(encoding='utf-8', errors='replace')
    src = re.sub(r'/\*.*?\*/', '', src, flags=re.DOTALL)
    src = re.sub(r'(^|\s)//[^\n]*', r'\1', src)
    for i, line in enumerate(src.splitlines(), start=1):
        out.append(f'{p}:{i}:{line}')
sys.stdout.write('\n'.join(out))
" \
	| grep -E "${pattern}" || true
)"

if [ -n "${hits}" ]; then
	echo "${hits}" >&2
	echo "Device chrome check failed: prototype-only iOS device chrome must not ship." >&2
	echo "Replace status-bar / home-indicator offsets with env(safe-area-inset-*)." >&2
	exit 1
fi

echo "Device chrome check passed."
