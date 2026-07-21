# Runbook: scribe running on env fallback (Infisical unreachable at boot)

`build_settings()` (`src/scribe/config.py`) fetches an in-process settings
overlay from Infisical at startup. When Infisical is **enabled** but
**unreachable** at boot, that fetch used to silently return an empty overlay and
the process ran for its whole lifetime without provider credentials
(`SCRIBE_OLLAMA_BASE_URL`, `SCRIBE_FREELLMAPI_API_KEY`, `SCRIBE_SUMMARY_PROVIDERS`,
…). Every summarization then failed (`ollama-cloud_no_base_url`,
`freellmapi_no_api_key`) producing "Partial transcript — summary failed" for all
jobs until a manual restart. See #415 and the 2026-07-19/20 outage.

## Failure signature

- **Boot log**: `infisical runtime config unavailable; using env fallback`
  (WARNING, logger `scribe.runtime_config`) at container start, and — when
  fail-fast is disabled — the follow-up
  `running on env fallback (DEGRADED): infisical enabled but unreachable at boot`
  (ERROR).
- **Prometheus**: `scribe_runtime_config_load_state{state="degraded"} == 1`.
  The three labels are mutually exclusive; exactly one carries `1.0`:
  - `infisical` — overlay fetched successfully (healthy);
  - `disabled` — Infisical not enabled/configured, env is the intended source
    (normal for local/dev);
  - `degraded` — Infisical enabled but unreachable at boot (**this failure**).
- **Downstream**: `scribe_summary_provider_calls_total{result="unavailable"}`
  climbing and `scribe.summary.provider_fallback` with
  `reason=ollama-cloud_no_base_url` / `freellmapi_no_api_key`; user-visible
  "Partial transcript — summary failed" on every job.

## What the service does now (#415)

On a degraded boot, `build_settings()`:

1. **Retries with bounded exponential backoff** before giving up
   (`SCRIBE_INFISICAL_BOOT_MAX_SECONDS`, default 300 s). A transient DNS/network
   blip that clears within the budget recovers automatically — the process
   comes up healthy with the real overlay, no restart needed.
2. **Fails fast** if still unreachable after the retry budget: it raises and the
   process exits non-zero, so Docker's `restart` policy relaunches it and keeps
   converging until Infisical is back. This is the default
   (`SCRIBE_INFISICAL_FAIL_FAST=true`) — a crash-looping container is a loud,
   self-healing signal, not a silent long-lived degradation.
3. **Or runs degraded on env fallback** only if an operator has explicitly set
   `SCRIBE_INFISICAL_FAIL_FAST=false`. In that mode the ERROR log line and the
   `degraded` metric are the signal that provider credentials may be missing.

## Triage

1. Confirm the state from the running process:

   ```bash
   curl -s http://10.10.0.13:13120/metrics | grep scribe_runtime_config_load_state
   ```

   `state="degraded"` == 1 means Infisical was unreachable at boot.

2. Pull the boot line from the log:

   ```bash
   docker logs scribe 2>&1 | grep -E "infisical runtime config unavailable|running on env fallback" | tail -20
   ```

3. Check Infisical reachability from the container:

   ```bash
   docker exec -it scribe curl -sS -o /dev/null -w '%{http_code}\n' \
     "${SCRIBE_INFISICAL_API_URL:-https://us.infisical.com}/api/status"
   ```

   Classify: DNS failure, network/firewall, Infisical outage, or bad
   credentials (`SCRIBE_INFISICAL_CLIENT_ID` / `_CLIENT_SECRET`).

## Recovery

- **Transient outage, fail-fast on (default)**: nothing to do — once Infisical
  is reachable, the next restart of the crash-looping container boots healthy.
  Verify with the metric above flipping to `state="infisical" == 1`.
- **Transient outage, fail-fast off**: restart the container once Infisical is
  back so it re-fetches the overlay:

  ```bash
  ssh god@10.10.0.13 'cd /opt/stacks/scribe/src && docker compose -f compose.yaml restart scribe'
  ```

- **Credentials wrong**: fix `SCRIBE_INFISICAL_CLIENT_ID` /
  `SCRIBE_INFISICAL_CLIENT_SECRET` in the stack env, then restart.

## Toggles

- `SCRIBE_INFISICAL_FAIL_FAST` — exit non-zero (let Docker restart) when
  Infisical is enabled but unreachable after retries; default `true`. Set
  `false` to run degraded on env fallback instead.
- `SCRIBE_INFISICAL_BOOT_RETRY_ENABLED` — retry a degraded boot load with
  backoff; default `true`.
- `SCRIBE_INFISICAL_BOOT_MAX_SECONDS` — cumulative backoff budget before giving
  up; default `300` (5 min).
- `SCRIBE_INFISICAL_BOOT_INITIAL_DELAY_SECONDS` — first backoff delay; default
  `2`.
- `SCRIBE_INFISICAL_BOOT_MAX_DELAY_SECONDS` — cap on the per-attempt backoff;
  default `30`.

Under pytest the retry + fail-fast hardening is disabled automatically so tests
never sleep or exit on a stubbed-degraded load.
