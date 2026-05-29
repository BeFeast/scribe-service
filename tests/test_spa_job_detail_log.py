"""Production guard against the design-export mock data leaking into the
job-detail PIPELINE LOG.

scr#255: the SPA was rendering hardcoded codex/RTX 4090 strings instead of
the real job log streamed from GET /api/jobs/{id}/log/stream. These checks
keep the production component (`web/spa/src/design-app/job-pages.jsx`) and
its log/SSE client wired to real data and free of the legacy fabrications.

The design-source mirror (`web/spa/src/design-source/app/job-pages.jsx`) is
the literal Claude Design export and is intentionally left as reference.
"""

from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JOB_PAGES = ROOT / "web" / "spa" / "src" / "design-app" / "job-pages.jsx"
API_JSX = ROOT / "web" / "spa" / "src" / "design-app" / "api.jsx"
DESIGN_SOURCE_JOB_PAGES = (
    ROOT / "web" / "spa" / "src" / "design-source" / "app" / "job-pages.jsx"
)

MOCK_STRINGS = (
    "android-vr",
    "RTX 4090",
    "i-8e9b2",
    "gpt-5",
    "78.2 MB",
    "1.1 MB/s",
    "16kHz mono WAV",
    "whisper-l3-turbo",
    "240 tok/s",
    "ssh tunnel up",
    "acquired codex lock",
    "prompt template v3",
    "residential IP",
    "Vast.ai GPU",
    "Shortlinks · webhook · DB write",
    "Waiting for a worker slot",
)


@pytest.fixture(scope="module")
def production_source() -> str:
    return JOB_PAGES.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def api_source() -> str:
    return API_JSX.read_text(encoding="utf-8")


@pytest.mark.parametrize("needle", MOCK_STRINGS)
def test_production_job_pages_has_no_design_mock_strings(
    production_source: str, needle: str
) -> None:
    assert needle not in production_source, (
        f"production job-pages.jsx still contains design-mock literal {needle!r}; "
        "PIPELINE LOG / STAGE_SUBLABEL must render real data only."
    )


def test_production_job_pages_drops_synthetic_buildlog(production_source: str) -> None:
    assert "function buildLog" not in production_source
    assert "buildLog(" not in production_source


def test_production_job_pages_drops_stage_sublabel_map(production_source: str) -> None:
    assert "STAGE_SUBLABEL" not in production_source


def test_production_log_tail_consumes_only_real_lines(production_source: str) -> None:
    assert "selectLogLines" in production_source
    # The log selector must derive from log.lines and contain no hardcoded log text.
    selector_block_start = production_source.index("export function selectLogLines")
    selector_block_end = production_source.index(
        "function LogTail", selector_block_start
    )
    selector_block = production_source[selector_block_start:selector_block_end]
    assert "log?.lines" in selector_block or "log.lines" in selector_block


def test_api_client_streams_from_real_endpoint(api_source: str) -> None:
    assert "/api/jobs/" in api_source
    assert "/log/stream" in api_source
    assert "export async function streamJobLog" in api_source


def test_design_source_mirror_is_left_as_reference() -> None:
    # The Claude Design export is the structural recipe and may still contain
    # the original fake log lines; only the production design-app copy is
    # rewritten. This test pins that intent so a future cleanup does not
    # silently delete the reference.
    text = DESIGN_SOURCE_JOB_PAGES.read_text(encoding="utf-8")
    assert "STAGE_SUBLABEL" in text
    assert "buildLog" in text
