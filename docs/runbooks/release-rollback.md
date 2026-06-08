# Release rollback

How releases are tagged on the devbox, and how to roll back to a previous
version in one command. Part of the release pipeline (#327); pairs with
`release-deploy.sh`.

## Image tagging

`release-deploy.sh` builds the app image once and tags it twice:

- `scribe:<version>` — an immutable, per-release tag (e.g. `scribe:1.4.2`).
  This is the durable artifact you roll back *to*.
- `scribe:current` — a moving alias re-pointed to the version being deployed.

The deploy `compose.yaml` app service pins `image: scribe:current` (it does
**not** carry `build:`), so `docker compose up` never rebuilds — it only runs
whatever image `scribe:current` currently points at. A deploy is therefore
"re-tag `scribe:current` → recreate the container"; a rollback is the same
operation aimed at an older `scribe:<version>` tag.

## Retention (keep-last-5)

`release-deploy.sh` keeps the **5 most recent** `scribe:<version>` tags and
prunes older ones after a successful deploy. This bounds disk use while
guaranteeing the last few releases are always available to roll back to.

List the release tags currently retained (newest first):

```sh
docker images scribe --format '{{.Tag}}\t{{.CreatedAt}}' \
  | grep -vE '^(current|local)\b' | sort -r
```

Roll back only to a tag that still appears in that list. Tags pruned by the
keep-last-5 policy must be rebuilt from the matching git ref before they can
be deployed again.

## Roll back in one command

Re-point `scribe:current` at the target release and recreate the container —
**no rebuild**:

```sh
VERSION=1.4.1 \
  docker tag "scribe:${VERSION}" scribe:current \
  && docker compose -f /opt/stacks/scribe/compose.yaml up -d scribe
```

`compose up -d scribe` recreates only the app container against the freshly
re-pointed `scribe:current`; the `scribe-pot` sidecar and other services are
left running.

### Dockhand-adopted stack

The devbox `/opt/stacks/scribe` stack is adopted by Dockhand; prefer
reconciling through it rather than a raw `docker compose up` (see
[`bgutil-pot-provider.md`](./bgutil-pot-provider.md)). The rollback is still a
two-step "re-tag then reconcile":

```sh
docker tag scribe:1.4.1 scribe:current
```

Then Apply the `scribe` stack in Dockhand
(http://10.10.0.13:13090/stacks → scribe → Apply), which recreates the app
container against the re-pointed `scribe:current` without a rebuild.

## Verify

```sh
# Which image is the running container actually on?
docker inspect --format '{{.Config.Image}}' scribe

# App is up.
curl -fsS http://10.10.0.13:13120/healthz
```

Confirm the reported version (UI footer / `/healthz`) matches the rollback
target before declaring the rollback done.

## Roll forward

Re-pointing `scribe:current` back at the newer `scribe:<version>` tag and
recreating restores the previous release — the forward image is still present
as long as it is within the keep-last-5 window.
