from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import textwrap

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"


def test_service_container_runs_migrations_before_uvicorn() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in dockerfile
    assert 'CMD ["uv", "run", "uvicorn", "scribe.main:app"' in dockerfile

    migration_step = entrypoint.index("uv run alembic upgrade head")
    exec_step = entrypoint.index('exec "$@"')
    assert migration_step < exec_step
    assert "set -eu" in entrypoint


def test_dockerfile_runs_as_non_root() -> None:
    """#348: the image must drop privileges to a non-root user before the CMD."""
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    # App user is created with a stable UID/GID for volume ownership.
    assert "--gid 1001 scribe" in dockerfile
    assert "--uid 1001" in dockerfile
    # Writable paths are chowned to the app user before dropping privileges.
    assert "chown -R scribe:scribe /app /home/scribe /data/tmp" in dockerfile
    # USER directive appears after the chown and before EXPOSE.
    chown_step = dockerfile.index("chown -R scribe:scribe")
    user_step = dockerfile.index("USER scribe")
    expose_step = dockerfile.index("EXPOSE 8000")
    assert chown_step < user_step < expose_step


def test_dockerfile_codex_auth_path_matches_non_root_home() -> None:
    """#348: codex auth volume now mounts under the non-root user's home."""
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "/home/scribe/.codex" in compose
    assert "/root/.codex" not in compose


def test_compose_codex_mount_uses_prod_codex_dir() -> None:
    """#383: reconcile repo compose with prod — codex auth is bind-mounted from
    ./codex (host-owned UID 1001), not the stale ./data/codex-auth path."""
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "./codex:/home/scribe/.codex" in compose
    assert "./data/codex-auth" not in compose


def test_compose_tmp_uses_named_volume_not_dead_nfs_bind() -> None:
    """#383: /data/tmp is a named volume (scribe-tmp) matching prod. The dead
    NFS bind-mount (/mnt/nfs/scribe-tmp) is gone, with the abandoned-NFS
    decision documented as a TODO(task#12) note."""
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "scribe-tmp:/data/tmp" in compose
    # The dead NFS bind-mount line is gone; the abandoned-NFS decision is kept
    # only as an explicit TODO(task#12) comment, not a live mount.
    assert "/mnt/nfs/scribe-tmp:/data/tmp" not in compose
    assert "TODO(task#12)" in compose


def test_compose_runs_infisical_agent_sidecar() -> None:
    """#383: the prod stack runs the Infisical Agent sidecar (#261) that renders
    boot secrets into the scribe-secrets volume; the repo reference compose
    mirrors that so it is the source of truth."""
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "infisical-agent:" in compose
    assert "infisical/cli:latest" in compose
    assert "scribe-secrets:/secrets:ro" in compose
    assert "scribe-secrets:/secrets" in compose


def test_compose_pins_project_subnet_and_external_db_dev_net() -> None:
    """#383/#300: the implicit project network subnet is pinned (so the
    scribe-backups sidecar stays inside SCRIBE_TRUSTED_CIDRS) and the external
    db-dev-net is declared so the infisical-agent reaches Infisical."""
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")
    assert "172.29.0.0/16" in compose
    assert "db-dev-net:" in compose
    assert "external: true" in compose


def test_env_example_documents_codex_model_and_prompt_dir() -> None:
    """#383: prod .env sets SCRIBE_CODEX_MODEL=gpt-5.5 (ChatGPT-account codex
    can't reach the default) and SCRIBE_PROMPT_DIR=/data/prompts; the repo
    .env.example documents both with a default + comment."""
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "SCRIBE_CODEX_MODEL=" in env_example
    assert "gpt-5.5" in env_example
    assert "SCRIBE_PROMPT_DIR=/data/prompts" in env_example


def test_non_root_volume_ownership_runbook_exists() -> None:
    """#383: the expected ownership of every mounted path under the non-root
    (UID 1001) runtime is documented in docs/runbooks/."""
    runbook = (REPO_ROOT / "docs/runbooks/non-root-volume-ownership.md").read_text(encoding="utf-8")
    assert "UID 1001" in runbook
    assert "1001:1001" in runbook
    assert "/home/scribe/.codex" in runbook
    assert "scribe-tmp" in runbook
    assert "infisical-agent" in runbook.lower() or "Dockhand" in runbook


def test_compose_persists_scribe_logs_in_journald() -> None:
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

    assert "logging:\n      driver: journald\n      options:\n        tag: scribe" in compose


def test_compose_runs_bgutil_pot_provider_sidecar() -> None:
    """#309: the stack compose stub documents the bgutil PO-token sidecar
    and wires its URL into the scribe container. The deploy compose at
    /opt/stacks/scribe/compose.yaml mirrors this contract for the
    Dockhand-adopted lifecycle."""
    compose = (REPO_ROOT / "compose.yaml").read_text(encoding="utf-8")

    # Sidecar service block.
    assert "scribe-pot:" in compose
    assert "brainicism/bgutil-ytdlp-pot-provider" in compose
    # In-network only — the provider is not published to the host.
    assert "    ports:\n      - \"4416" not in compose

    # scribe reaches it via container-DNS over the project network.
    assert "SCRIBE_BGUTIL_POT_BASE_URL: http://scribe-pot:4416" in compose


def test_pyproject_pins_bgutil_ytdlp_pot_provider_plugin() -> None:
    """#309: yt-dlp auto-discovers any installed plugin; the bgutil plugin
    must be in the scribe image so the sidecar is actually consulted."""
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "bgutil-ytdlp-pot-provider==" in pyproject


def test_bgutil_pot_runbook_documents_lifecycle_and_verification() -> None:
    runbook = (REPO_ROOT / "docs/runbooks/bgutil-pot-provider.md").read_text(encoding="utf-8")
    # Dockhand lifecycle reminder + verification commands.
    assert "Dockhand" in runbook
    assert "scribe-pot" in runbook
    assert "SCRIBE_BGUTIL_POT_BASE_URL" in runbook


def test_entrypoint_sources_infisical_env_file_and_runs_guard() -> None:
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    # Sourcing of the rendered env-file happens before the alembic step.
    source_step = entrypoint.index('. "$INFISICAL_ENV_FILE"')
    guard_step = entrypoint.index("SCRIBE_BOOT_REQUIRE_SECRETS")
    migration_step = entrypoint.index("uv run alembic upgrade head")
    assert source_step < guard_step < migration_step

    # The default path matches the docs and the sidecar agent.yaml destination.
    assert 'SCRIBE_INFISICAL_ENV_FILE:-/secrets/scribe.env' in entrypoint

    # The fail-loud guard checks both required boot secrets.
    assert "SCRIBE_TRUSTED_CIDRS" in entrypoint
    assert "SCRIBE_MACHINE_BEARER_TOKEN" in entrypoint
    assert "exit 1" in entrypoint


def _run_entrypoint(env: dict[str, str], *, cmd: str = "/bin/sh -c 'echo READY'") -> subprocess.CompletedProcess[str]:
    """Run entrypoint.sh in a stub harness that fakes `uv` so alembic is a noop."""
    sh = shutil.which("sh")
    assert sh is not None
    base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    base_env.update(env)
    # Wrap the real entrypoint in a tempdir-aware shim: prepend a stub
    # `uv` to PATH so the alembic step is a noop, then exec the entrypoint
    # with a harmless final CMD that prints READY on success.
    script = textwrap.dedent(
        f"""
        set -eu
        TMP=$(mktemp -d)
        cat >"$TMP/uv" <<'STUB'
        #!/usr/bin/env sh
        exit 0
        STUB
        chmod +x "$TMP/uv"
        PATH="$TMP:$PATH" exec sh {ENTRYPOINT} {cmd}
        """
    ).strip()
    return subprocess.run(
        [sh, "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_entrypoint_fails_loud_when_required_secrets_missing(tmp_path: pathlib.Path) -> None:
    env_file = tmp_path / "scribe.env"
    env_file.write_text("# empty\n", encoding="utf-8")
    result = _run_entrypoint({"SCRIBE_INFISICAL_ENV_FILE": str(env_file)})

    assert result.returncode != 0
    assert "missing required boot secrets" in result.stderr
    assert "SCRIBE_TRUSTED_CIDRS" in result.stderr
    assert "SCRIBE_MACHINE_BEARER_TOKEN" in result.stderr


def test_entrypoint_boots_when_env_file_provides_required_secrets(tmp_path: pathlib.Path) -> None:
    env_file = tmp_path / "scribe.env"
    env_file.write_text(
        "SCRIBE_TRUSTED_CIDRS=10.10.0.0/16\nSCRIBE_MACHINE_BEARER_TOKEN=fixture-bearer\n",
        encoding="utf-8",
    )
    result = _run_entrypoint({"SCRIBE_INFISICAL_ENV_FILE": str(env_file)})

    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert "applying alembic migrations" in result.stdout


def test_entrypoint_boots_when_env_already_carries_secrets(tmp_path: pathlib.Path) -> None:
    # No env-file at the configured path; required secrets supplied directly.
    missing_path = tmp_path / "absent.env"
    result = _run_entrypoint(
        {
            "SCRIBE_INFISICAL_ENV_FILE": str(missing_path),
            "SCRIBE_TRUSTED_CIDRS": "10.10.0.0/16",
            "SCRIBE_MACHINE_BEARER_TOKEN": "fixture-bearer",
        }
    )

    assert result.returncode == 0, result.stderr
    assert "no env-file" in result.stdout


def test_entrypoint_guard_can_be_disabled(tmp_path: pathlib.Path) -> None:
    missing_path = tmp_path / "absent.env"
    result = _run_entrypoint(
        {
            "SCRIBE_INFISICAL_ENV_FILE": str(missing_path),
            "SCRIBE_BOOT_REQUIRE_SECRETS": "0",
        }
    )

    assert result.returncode == 0, result.stderr


def test_infisical_agent_assets_are_committed() -> None:
    agent_dir = REPO_ROOT / "docker" / "infisical-agent"
    agent_yaml = (agent_dir / "agent.yaml").read_text(encoding="utf-8")
    template = (agent_dir / "scribe.env.tpl").read_text(encoding="utf-8")

    # Container-DNS, not the public hairpin or host-published port.
    assert "infisical-app:8080" in agent_yaml
    assert "secrets.oklabs.uk" not in agent_yaml
    # Render destination matches the entrypoint default.
    assert "/secrets/scribe.env" in agent_yaml

    # Template renders the two boot-critical secrets at minimum.
    assert "SCRIBE_TRUSTED_CIDRS" in template
    assert "SCRIBE_MACHINE_BEARER_TOKEN" in template
    # No real secret values were committed.
    assert "CHANGE_ME" not in template
    assert "secrets.oklabs.uk" not in template


@pytest.mark.parametrize(
    "doc_path",
    [
        "docs/runtime/infisical-agent.md",
    ],
)
def test_runtime_docs_describe_resilient_secret_delivery(doc_path: str) -> None:
    text = (REPO_ROOT / doc_path).read_text(encoding="utf-8")

    assert "infisical-agent" in text.lower()
    assert "db-dev-net" in text
    assert "last-known-good" in text.lower()
    assert "fail-loud" in text.lower()
