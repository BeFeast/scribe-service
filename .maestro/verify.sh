#!/bin/bash
# Verification for issue #417 — telegram-ingestion: support large media references.
#
# A large Telegram media reference (opaque `tg:<file_id>`) is submitted through
# the normal POST /jobs contract and resolved by the secure worker adapter
# (scribe.pipeline.telegram) via the Bot API getFile + download, with the bot
# token kept out of every job record, API payload, and log line. This script
# checks the machine-verifiable deliverables: the contract doc exists, lint is
# clean, and the adapter/routing/redaction tests pass.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo '=== Deliverables present ==='
test -f docs/telegram-media-ingestion.md
test -f src/scribe/pipeline/telegram.py
test -f tests/test_telegram.py
echo 'ok: contract doc + adapter + tests present'

echo '=== Lint (ruff) ==='
uv run ruff check src tests

echo '=== Tests: Telegram adapter + routing + secret redaction ==='
uv run pytest -q \
  tests/test_telegram.py \
  tests/test_downloader.py \
  tests/test_log_redaction.py

echo '=== Full test suite ==='
uv run pytest -q

echo 'All verifications passed.'
