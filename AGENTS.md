## Servers

- Use SSH aliases from `~/.ssh/config` directly for host access.
- TrueNAS access: `ssh truenas` (no password), with passwordless sudo inside.
- Workshop machine access: `ssh workshop` (no password), with passwordless sudo inside.
- Development box / runtime host access: `ssh devbox` (no password), with passwordless sudo inside.
- Do not reach devbox by nesting through workshop unless explicitly requested.

## scribe-service Runtime

- Devbox stack path: `/opt/stacks/scribe`
- Runtime source checkout: `/opt/stacks/scribe/src`
- Deploy command from devbox:
  ```bash
  cd /opt/stacks/scribe/src
  git pull --ff-only origin main
  cd /opt/stacks/scribe
  docker compose build
  docker compose up -d
  curl -sf http://127.0.0.1:13120/healthz
  ```
- Public healthcheck: `http://10.10.0.13:13120/healthz`
- Workshop Maestro checkout: `/mnt/storage/src/scribe`
- Workshop Maestro project config: `/home/god/.maestro/maestro.d/scribe-service.yaml`
- Workshop Maestro dashboard: `http://10.10.0.18:8790/`

## Runtime Tools

- Use `uv` for all Python operations instead of pip/python:
  - `uv run` instead of `python`
  - `uv pip install` instead of `pip install`
  - `uvx` for running CLI tools
  - `uv init` for new projects

- Use `bun` for all Node.js operations instead of npm/node:
  - `bun run` instead of `node`
  - `bun install` instead of `npm install`
  - `bunx` instead of `npx`
  - `bun init` for new projects

## Credential Lookup Policy

When you need any access credentials (passwords, API keys, tokens, etc.):
1. First, look up in Infisical.
2. If not found, look up in Vaultwarden via `bw` CLI.
3. Never hardcode credentials in dotfiles, shell profiles, or source code.
4. Never store session tokens persistently; derive them at runtime.

## Preferences

- Language: Russian for communication, English for code/comments.
- Timezone: Israel.
- Be direct and validate live system state before reporting completion.
