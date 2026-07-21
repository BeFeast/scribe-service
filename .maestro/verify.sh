#!/bin/bash
# Verification for issue #415 — harden Infisical runtime-config load so a
# transient Infisical outage at container boot cannot silently leave the process
# on env fallback (empty provider credentials) for its whole lifetime.
#
# The runtime behaviour (fail-fast exit under Docker restart, bounded boot retry,
# degraded metric/log) is unit-tested in tests/test_runtime_config.py. The live
# proof — a simulated Infisical outage restarting the real container — is a
# manual devbox step documented in docs/runbooks/infisical-boot-fallback.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo '=== Deliverables present ==='
test -f docs/runbooks/infisical-boot-fallback.md
echo 'ok: boot-fallback runbook present'

echo '=== Lint (ruff) ==='
uv run ruff check src tests

echo '=== Tests: runtime-config boot hardening ==='
uv run pytest -q tests/test_runtime_config.py

echo '=== Manual (not gated here) ==='
echo 'Simulated Infisical unreachability against the live container is a devbox'
echo 'step: with SCRIBE_INFISICAL_ENABLED=true and Infisical blocked, scribe must'
echo 'either recover once Infisical returns or exit non-zero and be restarted by'
echo 'compose. See docs/runbooks/infisical-boot-fallback.md.'

echo 'All verifications passed.'
