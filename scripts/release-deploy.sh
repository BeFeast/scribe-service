#!/usr/bin/env bash
# Versioned build + deploy + verify + auto-rollback for the scribe stack.
#
# Part of the release pipeline (#327, see PRD). Sequenced after the dynamic
# /healthz version work (#326): the verify step asserts that the running
# service reports the exact version we just deployed.
#
# Usage:
#   scripts/release-deploy.sh <X.Y.Z>            # build + deploy + verify
#   scripts/release-deploy.sh --rollback <X.Y.Z> # re-point to a prior tag only
#
# The script is idempotent (re-running for the already-deployed version is a
# no-op) and single-instance (flock). No host paths or hostnames are baked in;
# the stack directory, build context, and health URL come from args/env:
#
#   SCRIBE_STACK_DIR    dir holding compose.yaml (where `docker compose` runs)
#   SCRIBE_SRC_DIR      git checkout used as the build context (default: stack dir)
#   SCRIBE_HEALTH_URL   health endpoint returning JSON `.version` (e.g.
#                       http://host:13120/healthz)
#   SCRIBE_IMAGE        image repository (default: scribe)
#   SCRIBE_KEEP_IMAGES  versioned image tags to retain after prune (default: 5)
#   SCRIBE_VERIFY_TIMEOUT  seconds to wait for health to report the version (default: 120)
#   SCRIBE_SKIP_CANARY  set to 1 to skip the in-container download canary check
#   SCRIBE_LOCK_FILE    flock path (default: $TMPDIR/scribe-release-deploy.lock)
set -euo pipefail

IMAGE="${SCRIBE_IMAGE:-scribe}"
KEEP_IMAGES="${SCRIBE_KEEP_IMAGES:-5}"
VERIFY_TIMEOUT="${SCRIBE_VERIFY_TIMEOUT:-120}"
LOCK_FILE="${SCRIBE_LOCK_FILE:-${TMPDIR:-/tmp}/scribe-release-deploy.lock}"

log()   { printf '[release-deploy] %s\n' "$*"; }
err()   { printf '[release-deploy] ERROR: %s\n' "$*" >&2; }
alert() { printf '[release-deploy] ALERT: %s\n' "$*" >&2; }

usage() {
    cat >&2 <<'EOF'
usage:
  release-deploy.sh <X.Y.Z>             build + deploy version, verify, auto-rollback on failure
  release-deploy.sh --rollback <X.Y.Z>  re-point scribe:current to a prior tag and recreate (no build)

required env: SCRIBE_STACK_DIR, SCRIBE_HEALTH_URL
optional env: SCRIBE_SRC_DIR SCRIBE_IMAGE SCRIBE_KEEP_IMAGES SCRIBE_VERIFY_TIMEOUT SCRIBE_SKIP_CANARY
EOF
}

# --- argument parsing -------------------------------------------------------
MODE="deploy"
VERSION=""
case "${1:-}" in
    -h|--help|"")
        usage
        [ -n "${1:-}" ] && exit 0 || exit 2
        ;;
    --rollback)
        MODE="rollback"
        VERSION="${2:-}"
        ;;
    *)
        VERSION="$1"
        ;;
esac

if ! [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    err "version must be X.Y.Z (got '${VERSION}')"
    usage
    exit 2
fi

STACK_DIR="${SCRIBE_STACK_DIR:-}"
SRC_DIR="${SCRIBE_SRC_DIR:-$STACK_DIR}"
HEALTH_URL="${SCRIBE_HEALTH_URL:-}"
if [ -z "$STACK_DIR" ]; then err "SCRIBE_STACK_DIR is required"; exit 2; fi
if [ -z "$HEALTH_URL" ]; then err "SCRIBE_HEALTH_URL is required"; exit 2; fi

# --- single-instance lock ---------------------------------------------------
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    err "another release-deploy holds $LOCK_FILE; refusing to run concurrently"
    exit 1
fi

# --- docker / compose helpers ----------------------------------------------
compose() { docker compose --project-directory "$STACK_DIR" "$@"; }

image_id() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null; }

image_exists() { docker image inspect "$1" >/dev/null 2>&1; }

# Versioned tags (X.Y.Z) of $IMAGE, ascending by semver.
versioned_tags() {
    local tag out=""
    while read -r tag; do
        [[ "$tag" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] && out+="${tag}"$'\n'
    done < <(docker images --format '{{.Tag}}' "$IMAGE" 2>/dev/null)
    [ -n "$out" ] && printf '%s' "$out" | sort -t. -k1,1n -k2,2n -k3,3n -u
    return 0
}

# Version (X.Y.Z) that scribe:current currently resolves to, if any.
current_version() {
    local cur
    cur="$(image_id "${IMAGE}:current")" || return 0
    [ -n "$cur" ] || return 0
    local t
    for t in $(versioned_tags); do
        if [ "$(image_id "${IMAGE}:${t}")" = "$cur" ]; then
            printf '%s' "$t"
            return 0
        fi
    done
}

# Parse `.version` out of the health endpoint JSON (no jq dependency).
health_version() {
    local body
    body="$(curl -fsS --max-time 10 "$HEALTH_URL" 2>/dev/null)" || return 1
    printf '%s' "$body" \
        | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
        | head -n1
}

# Wait until the health endpoint reports $1 as its version, or time out.
wait_for_health_version() {
    local want="$1" deadline got
    deadline=$(( $(date +%s) + VERIFY_TIMEOUT ))
    while [ "$(date +%s)" -lt "$deadline" ]; do
        got="$(health_version || true)"
        if [ "$got" = "$want" ]; then
            log "health reports version ${got}"
            return 0
        fi
        sleep 3
    done
    err "health version did not reach ${want} within ${VERIFY_TIMEOUT}s (last: '${got:-unreachable}')"
    return 1
}

# Run the real yt-dlp download canary inside the running container.
canary_green() {
    if [ "${SCRIBE_SKIP_CANARY:-0}" = "1" ]; then
        log "download canary check skipped (SCRIBE_SKIP_CANARY=1)"
        return 0
    fi
    log "running in-container download canary"
    if compose exec -T scribe uv run python -c \
        'import sys; from scribe.worker.download_canary import run_download_canary; sys.exit(0 if run_download_canary() else 1)'; then
        log "download canary green"
        return 0
    fi
    err "download canary RED"
    return 1
}

# Full verify: health reports the expected version AND the canary is green.
verify() {
    wait_for_health_version "$1" && canary_green
}

retag_current() {
    docker tag "${IMAGE}:$1" "${IMAGE}:current"
    log "scribe:current -> ${IMAGE}:$1"
}

recreate() {
    log "docker compose up -d"
    compose up -d
}

# Keep only the newest $KEEP_IMAGES versioned tags; never drop the one
# scribe:current points to.
prune_images() {
    local keep="$KEEP_IMAGES" protected total tags drop t
    protected="$(current_version || true)"
    tags="$(versioned_tags)"
    if [ -z "$tags" ]; then return 0; fi
    total="$(printf '%s\n' "$tags" | wc -l | tr -d ' ')"
    if [ "$total" -le "$keep" ]; then
        return 0
    fi
    drop="$(printf '%s\n' "$tags" | head -n "$((total - keep))")"
    for t in $drop; do
        [ "$t" = "$protected" ] && continue
        if docker image rm "${IMAGE}:${t}" >/dev/null 2>&1; then
            log "pruned ${IMAGE}:${t}"
        else
            log "kept ${IMAGE}:${t} (in use or removal failed)"
        fi
    done
}

# --- rollback mode ----------------------------------------------------------
if [ "$MODE" = "rollback" ]; then
    if ! image_exists "${IMAGE}:${VERSION}"; then
        err "cannot roll back to ${IMAGE}:${VERSION}: image not found"
        exit 1
    fi
    log "rollback requested -> ${VERSION}"
    retag_current "$VERSION"
    recreate
    if wait_for_health_version "$VERSION"; then
        log "rollback to ${VERSION} verified"
        exit 0
    fi
    alert "rollback to ${VERSION} FAILED to verify — manual intervention required"
    exit 1
fi

# --- deploy mode ------------------------------------------------------------
PREV_VERSION="$(current_version || true)"
log "deploying ${VERSION} (previous scribe:current = ${PREV_VERSION:-none})"

# Idempotency: already deployed and healthy -> no-op (no rebuild, no restart).
if image_exists "${IMAGE}:${VERSION}" \
    && [ "$(image_id "${IMAGE}:current")" = "$(image_id "${IMAGE}:${VERSION}")" ] \
    && [ "$(health_version || true)" = "$VERSION" ]; then
    log "${VERSION} already deployed and healthy — nothing to do"
    exit 0
fi

# 1. Check out the vX.Y.Z tag in the build context.
log "checking out v${VERSION} in ${SRC_DIR}"
git -C "$SRC_DIR" fetch --tags --quiet
git -C "$SRC_DIR" checkout --quiet "v${VERSION}"

# 2. Build the versioned image, retag current, prune old tags.
log "docker build -t ${IMAGE}:${VERSION}"
docker build -t "${IMAGE}:${VERSION}" "$SRC_DIR"
retag_current "$VERSION"
prune_images

# 3. Recreate the stack.
recreate

# 4. Verify health version + canary.
if verify "$VERSION"; then
    log "deploy of ${VERSION} verified — scribe:current is live"
    exit 0
fi

# 5. Auto-rollback on verify failure.
err "verify failed for ${VERSION}"
if [ -z "$PREV_VERSION" ] || ! image_exists "${IMAGE}:${PREV_VERSION}"; then
    alert "verify failed for ${VERSION} and no previous version to roll back to — runtime may be broken"
    exit 1
fi

alert "verify failed for ${VERSION} — rolling back to ${PREV_VERSION}"
retag_current "$PREV_VERSION"
recreate
if wait_for_health_version "$PREV_VERSION"; then
    alert "deploy of ${VERSION} FAILED; rolled back to last-good ${PREV_VERSION}"
    exit 1
fi
alert "deploy of ${VERSION} FAILED and rollback to ${PREV_VERSION} did NOT verify — manual intervention required"
exit 1
