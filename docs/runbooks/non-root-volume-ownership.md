# Non-root volume ownership (#348)

The scribe image runs as a fixed non-root user `scribe` (UID/GID 1001); see
`Dockerfile`. Any host path bind-mounted into the container, and any named
volume that pre-existed from before #348, must be owned by UID 1001 on the
host — otherwise the container cannot read/write it and crashes or 401s at
boot. This runbook lists every mounted path and the ownership it needs.

The devbox user `sus` is UID 1001, so a bind prepared on the devbox as `sus`
already satisfies the constraint. Volumes created before the non-root switch
were root-owned and had to be `chown`ed by hand — record that here so the fix
is reproducible instead of tribal knowledge.

## Mounted paths and required ownership

| Path (in container)         | Type            | Source                       | Required owner (host)        |
|-----------------------------|-----------------|------------------------------|------------------------------|
| `/home/scribe/.codex`       | bind-mount (rw) | `./codex`                    | UID 1001 (`sus`)             |
| `/data/tmp`                 | named volume    | `scribe-tmp`                 | UID 1001 (chown on first use)|
| `/data/prompts`             | bind-mount (ro) | `./prompts`                  | any (mounted read-only)      |
| `/backups`                  | named volume    | `scribe-backups`             | UID 1001 (chown on first use)|
| `/secrets`                  | named volume    | `scribe-secrets`             | rendered 0600 by the agent  |

The image's `Dockerfile` `chown -R scribe:scribe /home/scribe /data/tmp` sets
ownership on first creation of the in-container paths and of newly created
named volumes. The fix-ups below are only needed for paths that existed
*before* the non-root switch (#348) and were left root-owned.

## Fix a root-owned named volume (`scribe-tmp`, `scribe-backups`)

Volumes created while the image still ran as root are owned by `root`. The
non-root container can't write to them. One-shot fix from the host (no
container restart needed beyond the recreate):

```sh
# Run a throwaway root container that chowns the volume to UID 1001.
docker run --rm -v scribe_scribe-tmp:/v alpine chown -R 1001:1001 /v
docker run --rm -v scribe_scribe-backups:/v alpine chown -R 1001:1001 /v
```

(The project name prefix `scribe_` is the compose project name from
`name: scribe` in `compose.yaml`.) Reconcile the stack via Dockhand afterwards.

## Prepare the codex auth bind-mount

`./codex` must hold `auth.json` + `config.toml` from a host that's already
logged in to ChatGPT, and be owned by UID 1001 so codex can refresh OAuth
tokens:

```sh
# On the devbox, as the UID 1001 user (sus):
install -d -m 700 /opt/stacks/scribe/codex
# copy auth.json + config.toml from a logged-in host, then:
chown -R 1001:1001 /opt/stacks/scribe/codex
chmod 600 /opt/stacks/scribe/codex/auth.json /opt/stacks/scribe/codex/config.toml
```

The legacy `/root/.codex` target is gone after the non-root switch; mounting
there silently fails because the container can no longer write to `/root`.

## Verification

```sh
# Container runs as the non-root user:
docker exec scribe id
# -> uid=1001(scribe) gid=1001(scribe) groups=1001(scribe)

# scribe can write its scratch dir:
docker exec scribe sh -c 'touch /data/tmp/.probe && rm /data/tmp/.probe && echo ok'

# codex can read+refresh its auth:
docker exec scribe sh -c 'test -r /home/scribe/.codex/auth.json && echo ok'
```

## Reconcile compose from the repo

The repo `compose.yaml` is the source of truth for this stack. To apply a fix
that landed in the repo to the devbox stack, diff the repo file against
`/opt/stacks/scribe/compose.yaml`, apply the delta there by hand (paths are
stack-relative on the host), then reconcile via Dockhand — do **not** copy the
repo file over the prod stack blind (see
[`release-rollback.md`](./release-rollback.md) for the Dockhand flow).