# Design-archive preflight & source of truth (#392)

The staged Claude design archive (`Scribe.redesign.zip`, SHA-256
`3253d4d262b00a25bdb07bf4ff3c7112998b9b8ee917211438aa220bcdd9719a`) is the
exact source for the scribe redesign. Two unrelated pieces of tooling verify it,
and confusing them caused a fleet-wide outage. This runbook records which is
which, where each one lives, and how to keep a relocated asset from taking down
deploys again.

## Source of truth: the Maestro preflight is host-only

| Consumer | Lives at | In this repo? | Path it checks | Failure mode |
|----------|----------|---------------|----------------|--------------|
| Maestro unit preflight `scribe-redesign-preflight.sh` | `/home/god/.maestro/bin/` on the Loki host | **No** — host-only Maestro tooling | `/home/god/src/Scribe.redesign.zip` | **Hard** (`ExecStartPre` of `maestro-scribe-service.service`) |
| `scripts/check-design-source-parity.sh` | this repo | Yes | `${SCRIBE_DESIGN_ARCHIVE:-/mnt/storage/src/Scribe.redesign.zip}` (workshop) | Soft — verifies repo export vs staged source when the archive is absent |
| `tests/test_spa_app_shell_source.py` | this repo | Yes | `${SCRIBE_DESIGN_ARCHIVE:-/mnt/storage/src/Scribe.redesign.zip}` (workshop) | Soft — `pytest.skip` when the archive is absent |

The `ExecStartPre` script `scribe-redesign-preflight.sh` **is not generated from
this repository** and has no source here — `grep -r scribe-redesign-preflight`
in the repo returns nothing. It is host-only Maestro fleet tooling. Fixes to it
must be made on the Loki host, not in this repo. The repo's two consumers run on
the **workshop** (`/mnt/storage/src/...`), a different machine and path from the
host preflight (`/home/god/src/...`); they only share the SHA-256, not the file.

## What broke (#392)

The host asset was reorganized to `/home/god/src/_assets/Scribe.redesign.zip`.
The preflight still hardcoded the old path:

```sh
zip_path="/home/god/src/Scribe.redesign.zip"
```

so it hard-failed `FAIL design zip missing`, and `maestro-scribe-service.service`
dropped into an `ExecStartPre` restart loop.

**Blast radius beyond scribe-service:** the Maestro fleet's centralized
self-deploy restarts every fleet unit and treats "a unit did not come back
active" as a deploy failure → it rolls the binary back. This one scribe-service
preflight failure therefore blocked self-deploy **fleet-wide** on the Loki host;
unrelated deploys failed and rolled back at 2026-06-25 18:50.

## Immediate recovery (bridge, already applied host-side)

A symlink points the old path at the relocated asset; SHA-256 matches, so the
preflight passes again:

```
/home/god/src/Scribe.redesign.zip -> /home/god/src/_assets/Scribe.redesign.zip
```

This is a **bridge, not the fix** — it re-couples the preflight to a name the
asset reorg deliberately retired.

## Proper host-side fix (Maestro, not this repo)

1. Point the preflight at the real asset, or make it configurable instead of a
   hardcode — e.g. `zip_path="${SCRIBE_DESIGN_ARCHIVE:-/home/god/src/_assets/Scribe.redesign.zip}"`,
   matching the env-override convention the repo consumers already use. Then drop
   the bridging symlink.
2. **Stop hard-failing unit start on the design asset.** The design zip is a
   worker-staging / source-parity input; it is **not** a runtime dependency of
   `maestro-scribe-service`. A missing or relocated asset should not be able to
   take down the unit and cascade into a fleet-wide self-deploy rollback. Demote
   the design-zip existence/checksum/readable checks from `bad` (hard, sets
   `fail=1`) to `warn` (soft signal), exactly as that same script already treats
   `deploy-pending` and devbox-checkout state. The hard gates should stay limited
   to what workers genuinely need: required tools, a clean/synced `main`, SSH,
   and Docker.

## Why the repo consumers are already safe

The two in-repo consumers do not have this failure mode, and #392 hardened the
last hardcode:

- `scripts/check-design-source-parity.sh` honors `SCRIBE_DESIGN_ARCHIVE` and, when
  the archive is absent, verifies the repo export against the staged source
  instead of failing.
- `tests/test_spa_app_shell_source.py` now resolves the path through
  `design_archive_path()` (honoring `SCRIBE_DESIGN_ARCHIVE`) and `pytest.skip`s
  when the archive is absent, so CI and local runs never hard-fail on a relocated
  or missing asset.

To point either at a relocated asset, export the override before running:

```sh
SCRIBE_DESIGN_ARCHIVE=/path/to/Scribe.redesign.zip bash scripts/check-design-source-parity.sh
SCRIBE_DESIGN_ARCHIVE=/path/to/Scribe.redesign.zip uv run pytest tests/test_spa_app_shell_source.py
```
