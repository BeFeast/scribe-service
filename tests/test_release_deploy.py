"""Tests for scripts/release-deploy.sh.

The script orchestrates docker/git/curl/compose, so we run it against stub
binaries on PATH (same harness style as test_docker_runtime.py). The fake
`docker` keeps image state in a directory: each tag is a file whose content is
its image id (we use the version string as the id), and `compose up` records
the version scribe:current points to as the "running" version that the fake
`curl` then reports from /healthz.
"""
from __future__ import annotations

import os
import pathlib
import stat
import subprocess
import types

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "release-deploy.sh"

FAKE_DOCKER = r"""#!/usr/bin/env bash
set -eu
IMG="$FAKE_STATE/images"
mkdir -p "$IMG"
cmd="${1:-}"; shift || true
case "$cmd" in
  build)
    tag=""
    while [ $# -gt 0 ]; do
      case "$1" in -t) tag="$2"; shift 2;; *) shift;; esac
    done
    name="${tag#*:}"
    printf '%s' "$name" > "$IMG/$name"   # image id == version
    ;;
  tag)
    src="${1#*:}"; dst="${2#*:}"
    cp "$IMG/$src" "$IMG/$dst"
    ;;
  images)
    ls "$IMG" 2>/dev/null || true
    ;;
  image)
    sub="$1"; shift
    case "$sub" in
      inspect)
        fmt=""
        if [ "${1:-}" = "--format" ]; then fmt="$2"; shift 2; fi
        ref="${1#*:}"
        if [ -f "$IMG/$ref" ]; then
          [ -n "$fmt" ] && cat "$IMG/$ref"
          exit 0
        fi
        exit 1
        ;;
      rm)
        ref="${1#*:}"; rm -f "$IMG/$ref"
        ;;
    esac
    ;;
  compose)
    while [ $# -gt 0 ]; do
      case "$1" in
        --project-directory) shift 2;;
        up) cat "$IMG/current" > "$FAKE_STATE/running"; break;;
        exec) exit "${FAKE_CANARY_EXIT:-0}";;
        *) shift;;
      esac
    done
    ;;
esac
exit 0
"""

FAKE_GIT = r"""#!/usr/bin/env bash
set -eu
# git -C DIR <fetch|checkout> ...
[ "${1:-}" = "-C" ] && shift 2
if [ "${1:-}" = "checkout" ]; then
  shift
  for a in "$@"; do case "$a" in --quiet) ;; *) echo "$a" >> "$FAKE_STATE/checkouts";; esac; done
fi
exit 0
"""

FAKE_CURL = r"""#!/usr/bin/env bash
set -eu
running=""
[ -f "$FAKE_STATE/running" ] && running="$(cat "$FAKE_STATE/running")"
[ -z "$running" ] && exit 7   # connection refused
printf '{"status":"ok","service":"scribe","version":"%s"}' "$running"
"""


def _write_exec(path: pathlib.Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def env(tmp_path: pathlib.Path):
    """Build a stub PATH + state dir, return (state_dir, runner)."""
    binu = tmp_path / "bin"
    binu.mkdir()
    _write_exec(binu / "docker", FAKE_DOCKER)
    _write_exec(binu / "git", FAKE_GIT)
    _write_exec(binu / "curl", FAKE_CURL)

    state = tmp_path / "state"
    (state / "images").mkdir(parents=True)
    stack = tmp_path / "stack"
    stack.mkdir()

    def seed(tag: str, points_to: str | None = None) -> None:
        # points_to lets `current` alias a versioned tag's id (== version).
        (state / "images" / tag).write_text(points_to or tag, encoding="utf-8")

    def running(version: str) -> None:
        (state / "running").write_text(version, encoding="utf-8")

    def read_image(tag: str) -> str | None:
        f = state / "images" / tag
        return f.read_text(encoding="utf-8") if f.exists() else None

    def versioned() -> list[str]:
        return sorted(
            p.name
            for p in (state / "images").iterdir()
            if all(part.isdigit() for part in p.name.split("."))
            and p.name.count(".") == 2
        )

    def run(*args: str, extra_env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        e = {
            "PATH": f"{binu}:{os.environ.get('PATH', '/usr/bin:/bin')}",
            "FAKE_STATE": str(state),
            "SCRIBE_STACK_DIR": str(stack),
            "SCRIBE_SRC_DIR": str(stack),
            "SCRIBE_HEALTH_URL": "http://stub/healthz",
            "SCRIBE_LOCK_FILE": str(tmp_path / "lock"),
            "SCRIBE_VERIFY_TIMEOUT": "6",
            "TMPDIR": str(tmp_path),
        }
        if extra_env:
            e.update(extra_env)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            env=e,
            capture_output=True,
            text=True,
            timeout=60,
        )

    return types.SimpleNamespace(
        run=run,
        seed=seed,
        running=running,
        read_image=read_image,
        versioned=versioned,
        running_version=lambda: (state / "running").read_text(encoding="utf-8"),
        checkouts=state / "checkouts",
    )


# --- structural guards ------------------------------------------------------
def test_script_exists_and_is_executable() -> None:
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)
    text = SCRIPT.read_text(encoding="utf-8")
    assert "set -euo pipefail" in text
    assert "flock" in text
    assert "--rollback" in text


def test_no_hardcoded_hosts_or_paths() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "SCRIBE_STACK_DIR" in text
    assert "SCRIBE_HEALTH_URL" in text
    # The real devbox host and stack path must come from env, never baked in.
    assert "10.10.0.13" not in text
    assert "/opt/stacks" not in text


# --- argument validation ----------------------------------------------------
def test_rejects_bad_version(env) -> None:
    r = env.run("not-a-version")
    assert r.returncode == 2
    assert "X.Y.Z" in r.stderr


def test_requires_stack_dir(env) -> None:
    r = env.run("1.0.0", extra_env={"SCRIBE_STACK_DIR": ""})
    assert r.returncode == 2
    assert "SCRIBE_STACK_DIR" in r.stderr


# --- deploy behaviour -------------------------------------------------------
def test_successful_deploy_leaves_current_at_version(env) -> None:
    env.seed("0.9.0")
    env.seed("current", "0.9.0")
    env.running("0.9.0")

    r = env.run("1.0.0", extra_env={"FAKE_CANARY_EXIT": "0"})
    assert r.returncode == 0, r.stderr
    assert env.read_image("current") == "1.0.0"
    assert env.running_version() == "1.0.0"
    assert "verified" in r.stdout
    # The vX.Y.Z tag was checked out.
    assert "v1.0.0" in env.checkouts.read_text()


def test_idempotent_rerun_is_noop(env) -> None:
    env.seed("1.0.0")
    env.seed("current", "1.0.0")
    env.running("1.0.0")

    r = env.run("1.0.0")
    assert r.returncode == 0, r.stderr
    assert "nothing to do" in r.stdout
    # No checkout happened -> no rebuild.
    assert not env.checkouts.exists()


def test_verify_failure_rolls_back_and_exits_nonzero(env) -> None:
    env.seed("0.9.0")
    env.seed("current", "0.9.0")
    env.running("0.9.0")

    r = env.run("1.0.0", extra_env={"FAKE_CANARY_EXIT": "1"})
    assert r.returncode != 0
    # Runtime stays on last-good.
    assert env.read_image("current") == "0.9.0"
    assert env.running_version() == "0.9.0"
    assert "rolled back to last-good 0.9.0" in r.stderr


def test_verify_failure_without_previous_version_alerts(env) -> None:
    # Fresh host: no scribe:current yet.
    r = env.run("1.0.0", extra_env={"FAKE_CANARY_EXIT": "1"})
    assert r.returncode != 0
    assert "no previous version to roll back to" in r.stderr


# --- rollback mode ----------------------------------------------------------
def test_rollback_mode_repoints_without_building(env) -> None:
    env.seed("0.9.0")
    env.seed("1.0.0")
    env.seed("current", "1.0.0")
    env.running("1.0.0")

    r = env.run("--rollback", "0.9.0")
    assert r.returncode == 0, r.stderr
    assert env.read_image("current") == "0.9.0"
    assert env.running_version() == "0.9.0"
    # No build/checkout in rollback mode.
    assert not env.checkouts.exists()


def test_rollback_to_missing_image_fails(env) -> None:
    env.seed("1.0.0")
    env.seed("current", "1.0.0")
    r = env.run("--rollback", "0.8.0")
    assert r.returncode != 0
    assert "not found" in r.stderr


# --- prune ------------------------------------------------------------------
def test_prune_keeps_at_most_five_versioned_images(env) -> None:
    for v in ["1.0.0", "1.0.1", "1.0.2", "1.0.3", "1.0.4", "1.0.5"]:
        env.seed(v)
    env.seed("current", "1.0.5")
    env.running("1.0.5")

    r = env.run("1.1.0", extra_env={"FAKE_CANARY_EXIT": "0"})
    assert r.returncode == 0, r.stderr
    remaining = env.versioned()
    assert len(remaining) == 5
    # Oldest dropped, newest kept.
    assert "1.0.0" not in remaining
    assert "1.1.0" in remaining
