#!/bin/bash
# Verification for issue #413 — spike: client-side media acquisition.
#
# This is a de-risking SPIKE: the primary deliverables are the findings write-up
# (docs/spike-client-side-capture.md) and a PoC scaffold
# (extension/chrome-client-capture-poc/) whose end-to-end run on real videos is a
# manual browser step (see the doc's "Verification status"). What is machine-
# verifiable is checked here: the deliverables exist, the PoC's static contract
# holds, and the existing upload path (#408, the server side of spike Q3) passes.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo '=== Deliverables present ==='
test -f docs/spike-client-side-capture.md
test -f extension/chrome-client-capture-poc/manifest.json
test -f extension/chrome-client-capture-poc/README.md
echo 'ok: findings doc + PoC scaffold present'

echo '=== Lint (ruff) ==='
uv run ruff check src tests

echo '=== Tests: PoC static contract + upload path + extension guard ==='
uv run pytest -q \
  tests/test_client_capture_poc.py \
  tests/test_jobs_upload.py \
  tests/test_uploads_staging.py \
  tests/test_chrome_extension.py

echo '=== Manual (not gated here) ==='
echo 'End-to-end PoC on 3 real videos (incl. one >1h) requires a signed-in'
echo 'Chrome profile + a live Scribe with R2 configured. See'
echo 'docs/spike-client-side-capture.md sections 9-11.'

echo 'All verifications passed.'
