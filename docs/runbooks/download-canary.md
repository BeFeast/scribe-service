# Runbook: yt-dlp download canary went red

The download canary exercises the real yt-dlp path (metadata + audio stream)
against a known-stable public video on a fixed cadence (default: every 24 h,
600 s after worker start). When it goes red the operator gets a Telegram alert
that links to this page.

Signal:

- **Telegram**: `scribe yt-dlp download canary RED` (admin channel).
- **Prometheus**: `scribe_download_canary_status == 0`, or
  `scribe_download_canary_last_success_timestamp_seconds` older than expected.
- **Counter**: `scribe_download_canary_runs_total{outcome="fail"}` ticking.

Pin lives in [`pyproject.toml`](../../pyproject.toml) (`yt-dlp==<version>`).
There is intentionally no upper bound and no float — bumping it is a single,
deliberate edit in this file and `uv.lock`.

## Triage

1. Pull the canary error from the worker log:

   ```bash
   docker logs scribe 2>&1 | grep -A 3 "download canary RED" | tail -20
   ```

2. Reproduce locally inside the container against the same URL:

   ```bash
   docker exec -it scribe uv run python -c \
     "from scribe.worker.download_canary import run_download_canary; print(run_download_canary())"
   ```

3. Classify:
   - **yt-dlp version regression** (a recent bump broke against YouTube).
   - **YouTube-side change** (extractor needs new flag / player client / EJS).
   - **Network / DNS / IP-reputation** (bot wall from a datacenter IP).
   - **bgutil sidecar drift** (PO-token provider out of date; only relevant if
     the sidecar is wired into the chain).

If the error is `Sign in to confirm you're not a bot` from a known-clean IP,
it's most likely category 2; otherwise check the upstream yt-dlp release notes
for the period since the last known-green pin.

## Rollback (revert the pin)

Use this when a recent bump went red.

1. Pick the last-known-green version from git history:

   ```bash
   git log -p -- pyproject.toml | grep -E "yt-dlp==" | head -10
   ```

2. Update the pin in `pyproject.toml`:

   ```diff
   -    "yt-dlp==2026.X.Y",
   +    "yt-dlp==2026.A.B",
   ```

3. Re-lock and rebuild:

   ```bash
   uv lock
   uv sync --frozen --all-extras --all-groups
   uv run ruff check src tests
   uv run pytest -q
   ```

4. Open a PR, merge, redeploy:

   ```bash
   ssh god@10.10.0.13 'cd /opt/stacks/scribe/src && git pull && docker compose -f compose.yaml build scribe && docker compose -f compose.yaml up -d scribe'
   ```

5. Confirm green:

   ```bash
   curl -s http://10.10.0.13:13120/metrics | grep -E "scribe_download_canary_(status|last_success)"
   ```

   `scribe_download_canary_status` should flip to `1` within
   `SCRIBE_DOWNLOAD_CANARY_INITIAL_DELAY_SECONDS` (default 600 s) of restart.

## Bump (move the pin forward)

Same flow, opposite direction. Bumps should be deliberate, not implicit:

1. Check the upstream release notes for the target version.
2. Edit the pin in `pyproject.toml`, then `uv lock`.
3. Run `uv run pytest -q` and the local canary smoke (`run_download_canary()`).
4. Deploy.
5. Watch `scribe_download_canary_status` for one full canary cycle before
   walking away.

## Toggles

- `SCRIBE_DOWNLOAD_CANARY_ENABLED=false` — disable the loop entirely.
- `SCRIBE_DOWNLOAD_CANARY_URL` — override the target video (the default is the
  first YouTube upload, "Me at the zoo", chosen because it's effectively
  immortal).
- `SCRIBE_DOWNLOAD_CANARY_INTERVAL_SECONDS` — cadence; default `86400`.
- `SCRIBE_DOWNLOAD_CANARY_INITIAL_DELAY_SECONDS` — wait after process start
  before the first run; default `600`.
- `SCRIBE_DOWNLOAD_CANARY_RUNBOOK_URL` — the link included in alert text;
  defaults to this file on `main`.
