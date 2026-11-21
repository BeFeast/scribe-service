# AGENTS.md — scribe-service

## Purpose and layout

Links and media files become transcripts and summaries: a FastAPI service with a Postgres-backed worker queue, a React SPA and a whisper GPU image. Behaviour, API and operations: `README.md`.

- `src/scribe/` — `api/` routes, `db/` SQLAlchemy models, `pipeline/` (downloader, ffmpeg, whisper client, summarizer, shortlinks), `worker/` queue loop, `prompts/` versioned summary prompts, `config.py` settings.
- `web/spa/` — the SPA (Bun); `design/scribe-redesign-2026-05-24/` — the design baseline.
- `migrations/` — Alembic; the image runs `uv run alembic upgrade head` before serving.
- `docker/vast/` — whisper-only GPU image; `extension/` — browser extension.
- `scripts/release-deploy.sh` — build, retag, restart, health + canary check, auto-rollback on the runtime host.

## Commands

Use `uv` for Python (`uv sync`, `uv run`, `uvx`) and `bun` for Node (`bun install`, `bun run`, `bunx`); not pip/npm/npx.

| Step | Command | Time |
|---|---|---|
| Setup | `uv sync --frozen --all-extras --all-groups` | not measured locally |
| Lint | `uv run ruff check src tests` | not measured locally |
| Tests | `uv run pytest -q` | not measured locally |
| Design checks | `bash scripts/check-design-tokens.sh`, `check-design-recipes.sh`, `check-design-nesting.sh`, `check-forbidden-primitives.sh`, `check-no-device-chrome.sh` | not measured locally |
| SPA | `cd web/spa && bun install --frozen-lockfile && bun test tests && bun run build` | not measured locally |
| Full CI | `.github/workflows/ci.yml` (all of the above, Postgres service) | 1.3–1.7 min (Forgejo runs 2026-11-12..20) |

## Merge gate

- Required check: `ci / test (pull_request)` green on the PR head (not enforced by branch protection; do not merge red). Dependabot PRs also run `dependabot-auto-merge`.
- One async review by another model family before merge for migrations and data deletion, auth and share-link access, and the release/deploy script.
- PR bodies use non-closing references (`Refs #123`) unless every acceptance criterion, runtime and deploy included, is proven.

## Release policy

- SemVer; `version` in `pyproject.toml` is the single source of truth.
- Every PR carries exactly one `semver:*` label: `semver:major` (breaking), `semver:minor` (backward-compatible feature), `semver:patch` (fix/docs/chore). Unlabeled = patch.
- Each merge to `main` produces one version bump, one `vX.Y.Z` tag and one release, with a dated `CHANGELOG.md` section (`uv run python scripts/generate_changelog.py vX.Y.Z --bump patch`). See README «Releases».

## Hard rules

- **Redesign contract.** The Claude Design export is the exact UI/UX source, not inspiration: port its DOM, class model, recipes, SVG/icon geometry and state layout, then wire real data, routing and actions. Replace old components; never repaint old TSX with CSS tweaks. Fake design data only explains shape.
- Appearance controls live in Settings → Appearance; never ship a floating Tweaks/debug panel in production routes.
- The appearance matrix stays in scope: variants paper/terminal/console/field, themes light/dark, densities compact/cozy/comfy, layouts table/feed/cards. Default: field/light/compact/feed. The default is not the whole scope.
- Source-parity work needs screenshots from the worker branch build (Vite preview) with real data, every variant switchable in Settings and no `.tweaks-panel`; health-only, empty or 404 screenshots do not count.
- A green PR or a healthy `/healthz` is not a shipped product: check the deployed HTML/JS/CSS and the browser.

## Public-repo hygiene

The repository is public (Forgejo, mirrored to GitHub). Commit no secrets, `.env` values, personal paths, internal hostnames or addresses; settings read secrets at runtime from the secret manager.

## Process

Process, hosts, runtime and deploy steps live in the private project area `Dev/Areas/scribe-service/AGENTS.md` of the management vault; it wins on process. Code, comments, commits and PR text in English. `CLAUDE.md` only imports this file.
