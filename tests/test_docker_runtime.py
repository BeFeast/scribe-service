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
