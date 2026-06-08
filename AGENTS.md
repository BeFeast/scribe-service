## Coordination
- Communicate with Oleg in Russian; keep code/comments in English.
- Treat local Mac checkouts as reference/read surfaces only unless explicitly asked otherwise.
- Do implementation, builds, tests, commits, and runtime verification on workshop:/mnt/storage/src/scribe.

## Scribe Redesign Contract
- Claude Design export source is the exact UI/UX implementation source, not inspiration.
- Replace old visual components with design-derived DOM/layout/styles; do not repaint old TSX with CSS tweaks.
- Product appearance controls live in Settings -> Appearance; do not ship floating Tweaks/debug panels.
- Supported appearance matrix stays in scope: variants paper/terminal/console/field, themes light/dark, densities compact/cozy/comfy, layouts table/feed/cards. Default is field/light/compact/feed.

## Shell And SSH Hygiene
- Do not send large multiline scripts, generated source, markdown bodies, JSON, YAML, or patches through inline ssh heredocs.
- For remote execution, create the script or payload as a real file first, copy it to the remote host with scp or rsync, then run that remote file explicitly.
- Small one-line ssh probes are fine. Generated code and broad rewrites must use real files to avoid local-shell interpolation and nested quote failures.

## Runtime Tools
- Use bun for Node/frontend commands: bun install, bun run, bunx.
- Use uv for Python commands: uv sync, uv run, uvx.

## Release Policy
- Versioning follows SemVer (MAJOR.MINOR.PATCH). The `version` field in `pyproject.toml` is the single source of truth.
- Every PR carries exactly one `semver:*` label declaring the bump on merge: `semver:major` (breaking change), `semver:minor` (backward-compatible feature), `semver:patch` (fix/docs/chore).
- Default is `semver:patch`: an unlabeled PR is treated as a patch bump.
- Cadence is per-merge and continuous: each merge to `main` triggers a version bump in `pyproject.toml`, a `vX.Y.Z` git tag, and a release. No manual or batched release step.
- See README "Releases" for the full label taxonomy table.
