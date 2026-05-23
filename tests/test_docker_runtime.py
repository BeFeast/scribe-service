from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_service_container_runs_migrations_before_uvicorn() -> None:
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoint = (REPO_ROOT / "docker" / "entrypoint.sh").read_text(encoding="utf-8")

    assert 'ENTRYPOINT ["/app/docker/entrypoint.sh"]' in dockerfile
    assert 'CMD ["uv", "run", "uvicorn", "scribe.main:app"' in dockerfile

    migration_step = entrypoint.index("uv run alembic upgrade head")
    exec_step = entrypoint.index('exec "$@"')
    assert migration_step < exec_step
    assert "set -eu" in entrypoint
