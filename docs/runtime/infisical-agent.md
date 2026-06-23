# Resilient Infisical secret delivery (#261)

`scribe` consumes its boot-time secrets (`SCRIBE_TRUSTED_CIDRS`,
`SCRIBE_MACHINE_BEARER_TOKEN`, etc.) as real environment variables. Prior
to #261, those values were fetched in-process from Infisical during
`from scribe.config import settings`. Any boot-time race against
Infisical left scribe running with the loopback-only `trusted_cidrs`
default, locking every LAN client out with `401 authentication required`.

The durable fix moves secret delivery out of the app's boot path into an
**Infisical Agent sidecar** with a last-known-good cache on a shared
local volume. The scribe entrypoint sources the rendered env-file
before launching uvicorn, and refuses to start if the required secrets
are missing (fail-loud).

## Components committed in this repo

- `docker/entrypoint.sh` — sources `${SCRIBE_INFISICAL_ENV_FILE:-/secrets/scribe.env}` then runs the boot-secret guard. Missing `SCRIBE_TRUSTED_CIDRS` or `SCRIBE_MACHINE_BEARER_TOKEN` after the source step exits 1 so docker's `restart: unless-stopped` kicks in.
- `docker/infisical-agent/agent.yaml` — sidecar config: universal-auth via mounted client-id/client-secret, polls Infisical via container-DNS (`http://infisical-app:8080`), writes the rendered template to `/secrets/scribe.env`.
- `docker/infisical-agent/scribe.env.tpl` — explicit secret-by-secret template for the `SCRIBE_*` envs scribe expects.
- `src/scribe/config.py` — `build_settings()` drops empty values from the Infisical overlay so a transient or missing secret cannot clobber a valid env value.

## Deploy snippet (devbox `/opt/stacks/scribe/compose.yaml`)

Not committed; wire this on devbox at deploy time.

```yaml
services:
  scribe:
    # ...existing keys...
    env_file: .env
    volumes:
      - ./codex:/home/scribe/.codex          # codex auth (host-owned UID 1001, #348)
      - scribe-tmp:/data/tmp                  # named volume (not the dead NFS bind)
      - scribe-secrets:/secrets:ro            # rendered env-file (read-only)
    depends_on:
      infisical-agent:
        condition: service_started
    networks:
      - default
      - db-dev-net

  infisical-agent:
    image: infisical/cli:latest
    command: ["agent", "--config", "/etc/infisical/agent.yaml"]
    restart: unless-stopped
    volumes:
      - ./infisical-agent/agent.yaml:/etc/infisical/agent.yaml:ro
      - ./infisical-agent/scribe.env.tpl:/etc/infisical/templates/scribe.env.tpl:ro
      - ./infisical-agent/identity/client-id:/etc/infisical/identity/client-id:ro
      - ./infisical-agent/identity/client-secret:/etc/infisical/identity/client-secret:ro
      - scribe-secrets:/secrets
    networks:
      - db-dev-net

volumes:
  scribe-secrets:
    driver: local

networks:
  db-dev-net:
    external: true
```

Notes:

- `scribe-secrets` is a **local-driver** named volume. Do not back it
  with NFS — secrets must not traverse the LAN at boot time.
- The agent and scribe sit on the shared external `db-dev-net` network
  so the agent reaches `infisical-app:8080` over container-DNS, not the
  public `secrets.oklabs.uk` hairpin. Scribe stays on `default` for its
  current ports/listeners.
- The machine-identity `client-id`/`client-secret` files live outside
  the repo at `./infisical-agent/identity/` with `chmod 0600` and the
  containing directory `chmod 0700`. They are mounted read-only.
- The rendered env-file is read-only inside scribe. The Infisical Agent
  writes it with restricted permissions.

## Fail-modes (verified at runtime)

| Scenario | Expected behaviour |
|----------|--------------------|
| Cold start, agent has never rendered, Infisical unreachable | Entrypoint logs `FATAL: missing required boot secrets`, exits 1. Docker restarts unless-stopped. **Not** serving with loopback-only trust. |
| Warm restart, last-known-good cache populated, Infisical unreachable | Agent keeps the existing `/secrets/scribe.env`. Scribe sources it and serves normally (`/api/library` → 200 from LAN). |
| Agent renders empty value for a key | `build_settings()` drops empty overlay entries; env value or pydantic default is used. |
| Container running, secret rotated in Infisical | Agent re-renders on the next polling tick. Restart scribe to pick the new values (Settings are loaded at import time). |

## Local development

Local dev with `compose up` does not need the sidecar. Set
`SCRIBE_BOOT_REQUIRE_SECRETS=0` in `.env` (or supply real values in
`.env` + leave `SCRIBE_INFISICAL_ENV_FILE` pointing at a path that does
not exist) to skip the fail-loud guard. CI runs unit tests directly,
bypassing the docker entrypoint entirely.

## Migration from in-process Infisical fetch

The in-process fetch in `scribe.runtime_config.load_infisical_settings`
still runs and can populate any `SCRIBE_*` field the sidecar did not
render. It is now redundant and non-fatal: empty values are filtered
out so they cannot override a valid env value, and a network failure
continues to log `infisical runtime config unavailable; using env
fallback` and returns `{}`. With the sidecar in place, the env-file
sourced by the entrypoint is the source of truth.

Operators self-hosting Infisical may still want to point the in-process
fetch at container-DNS as a belt-and-braces measure:

```bash
SCRIBE_INFISICAL_API_URL=http://infisical-app:8080
```
