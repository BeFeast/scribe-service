"""Web-UI: SPA shell plus legacy transcript browsing.

GET /                       -> React SPA shell
GET /classic                -> legacy transcript list (optional ?q=, ?tag=)
GET /feed.xml               -> RSS 2.0 of the latest transcripts

Transcript detail lives in the SPA; /transcripts/{id} is handled by the API
router as JSON for clients or a browser redirect to #/transcript/{id}.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import functools
import html
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from scribe.api.routes import get_session
from scribe.config import settings
from scribe.db.models import Transcript
from scribe.db.query import escape_like

router = APIRouter(tags=["web"])
_WEB_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_WEB_DIR / "templates"))
_SPA_STATIC_DIR = _WEB_DIR / "static" / "spa"
_SPA_MANIFEST_PATH = _SPA_STATIC_DIR / ".vite" / "manifest.json"
# Hard cap on the rows the home page renders. Keeps the page fast even after
# years of accretion; older entries are still reachable by tag/search.
_LIST_LIMIT = 200
_FEED_LIMIT = 40


@functools.cache
def _spa_asset_urls() -> dict[str, list[str]]:
    try:
        manifest = json.loads(_SPA_MANIFEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        manifest = {}

    entry = manifest.get("index.html")
    if entry is None:
        entry = next((item for item in manifest.values() if item.get("isEntry")), {})

    scripts = []
    if file := entry.get("file"):
        scripts.append(f"/static/spa/{file}")

    styles = [f"/static/spa/{path}" for path in entry.get("css", [])]
    return {"scripts": scripts, "styles": styles}


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter block so it doesn't render as a table."""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4 :].lstrip("\n")
    return text


def _build_filter(stmt, *, q: str | None, tag: str | None):
    """Apply optional search + tag filters in-place. q matches title +
    transcript_md case-insensitively; tag is exact-match against the
    Postgres array column."""
    if q:
        like = f"%{escape_like(q.strip())}%"
        stmt = stmt.where(or_(Transcript.title.ilike(like), Transcript.transcript_md.ilike(like)))
    if tag:
        # Postgres-specific: `value = ANY(array_col)`. tags column is TEXT[].
        stmt = stmt.where(Transcript.tags.any(tag.strip()))
    # Always hide partials from the home/RSS view; the include_partial JSON
    # query param is the API-side opt-in for ops.
    stmt = stmt.where(Transcript.summary_md.is_not(None))
    return stmt


@router.get("/", response_class=HTMLResponse)
@router.get("/__spa__/", response_class=HTMLResponse)
@router.get("/__spa__/{spa_path:path}", response_class=HTMLResponse)
def spa_shell(request: Request, spa_path: str = "") -> HTMLResponse:
    assets = _spa_asset_urls()
    return _TEMPLATES.TemplateResponse(
        request,
        "spa.html",
        {"scripts": assets["scripts"], "styles": assets["styles"]},
    )


@router.get("/classic", response_class=HTMLResponse)
def classic_index(
    request: Request,
    session: Session = Depends(get_session),
    q: str | None = Query(None, description="Substring against title + transcript_md"),
    tag: str | None = Query(None, description="Exact tag match"),
) -> HTMLResponse:
    stmt = _build_filter(select(Transcript), q=q, tag=tag).order_by(Transcript.id.desc()).limit(_LIST_LIMIT)
    rows = session.scalars(stmt).all()
    return _TEMPLATES.TemplateResponse(
        request,
        "list.html",
        {"transcripts": rows, "q": q or "", "tag": tag or ""},
    )


# ---------------------------------------------------------------- RSS feed
def _summary_excerpt(transcript: Transcript, limit: int = 320) -> str:
    """First N chars of the summary body, with frontmatter stripped and
    markdown reduced to plain text suitable for a feed reader."""
    body = _strip_frontmatter(transcript.summary_md or "")
    # quickly strip markdown headings + emphasis + lists for the excerpt
    body = re.sub(r"^#+\s*", "", body, flags=re.MULTILINE)
    body = re.sub(r"[*_`]+", "", body)
    body = re.sub(r"\s+", " ", body).strip()
    return body[:limit] + ("…" if len(body) > limit else "")


def _rss_date(when: dt.datetime) -> str:
    # RFC 822 — RSS spec. email.utils.formatdate is locale-independent;
    # strftime would honour LC_TIME and emit non-English month/day names
    # on hosts that aren't on C / en_US, breaking strict feed parsers.
    return email.utils.formatdate(when.astimezone(dt.UTC).timestamp(), usegmt=True)


@router.get("/feed.xml", include_in_schema=False)
def feed(
    request: Request,
    session: Session = Depends(get_session),
    tag: str | None = Query(None, description="Optional tag filter"),
) -> Response:
    stmt = _build_filter(select(Transcript), q=None, tag=tag).order_by(Transcript.id.desc()).limit(_FEED_LIMIT)
    rows = session.scalars(stmt).all()
    base = html.escape(settings.public_base_url.rstrip("/"))
    title_suffix = f" — tag:{tag}" if tag else ""
    items: list[str] = []
    for t in rows:
        link = f"{base}/transcripts/{t.id}"
        item_pub = _rss_date(t.created_at)
        item_title = html.escape(t.title or "Untitled")
        item_desc = html.escape(_summary_excerpt(t))
        cats = ""
        for raw_tag in (t.tags or []):
            cats += f"\n      <category>{html.escape(raw_tag)}</category>"
        items.append(
            f"    <item>\n"
            f"      <title>{item_title}</title>\n"
            f"      <link>{link}</link>\n"
            f"      <guid isPermaLink=\"true\">{link}</guid>\n"
            f"      <description>{item_desc}</description>\n"
            f"      <pubDate>{item_pub}</pubDate>{cats}\n"
            f"    </item>"
        )
    now = _rss_date(dt.datetime.now(dt.UTC))
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        "  <channel>\n"
        f"    <title>scribe{html.escape(title_suffix)}</title>\n"
        f"    <link>{base}/</link>\n"
        "    <description>Latest transcripts</description>\n"
        f"    <lastBuildDate>{now}</lastBuildDate>\n"
        + "\n".join(items)
        + "\n  </channel>\n</rss>\n"
    )
    return Response(content=body, media_type="application/rss+xml; charset=utf-8")
