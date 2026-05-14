"""Web-UI: browse transcript history (server-rendered Jinja).

GET /                       -> transcript list
GET /transcripts/{id}       -> transcript detail (summary + transcript, rendered)

The detail page lives at /transcripts/{id} on purpose: the worker mints the
summary shortlink against this path, so a human clicking it lands on HTML.
The JSON API keeps /transcripts (list) and the raw .md endpoints.
"""
from __future__ import annotations

from pathlib import Path

import markdown as md
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from scribe.api.routes import get_session
from scribe.db.models import Transcript

router = APIRouter(tags=["web"])
_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block so it doesn't render as a table."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def _render_md(text: str) -> str:
    return md.markdown(
        _strip_frontmatter(text or ""),
        extensions=["extra", "sane_lists", "nl2br"],
    )


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    rows = session.scalars(
        select(Transcript).order_by(Transcript.id.desc()).limit(200)
    ).all()
    return _TEMPLATES.TemplateResponse(
        request, "list.html", {"transcripts": rows}
    )


@router.get("/transcripts/{transcript_id}", response_class=HTMLResponse)
def detail(
    transcript_id: int,
    request: Request,
    session: Session = Depends(get_session),
) -> HTMLResponse:
    t = session.get(Transcript, transcript_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"transcript {transcript_id} not found")
    return _TEMPLATES.TemplateResponse(
        request,
        "detail.html",
        {
            "t": t,
            "summary_html": _render_md(t.summary_md),
            "transcript_html": _render_md(t.transcript_md),
        },
    )
