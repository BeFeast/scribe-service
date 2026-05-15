"""Pure-template test for the dark-mode toggle.

Renders the home page template directly through Jinja2 so the test doesn't
need Postgres. The view is thin, so this is enough to lock in the toggle
markup, the localStorage glue, and the `html.theme-*` CSS hook required by
PRD §4.9."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader


def _render_index() -> str:
    templates_dir = Path("src/scribe/web/templates")
    env = Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=True,
    )
    return env.get_template("list.html").render(transcripts=[], q="", tag="")


def test_header_has_theme_toggle_with_aria_label():
    html = _render_index()
    assert 'id="theme-toggle"' in html
    assert 'aria-label="Toggle dark mode"' in html


def test_toggle_persists_choice_to_localstorage():
    """The toggle's whole purpose is to override prefers-color-scheme on
    reload, which only works if the click handler writes localStorage."""
    html = _render_index()
    assert 'localStorage.setItem("theme"' in html
    assert 'localStorage.getItem("theme")' in html


def test_css_keys_off_html_theme_classes():
    """PRD §4.9: 'CSS adjusts on html.theme-* class'."""
    html = _render_index()
    assert "html.theme-light" in html
    assert "html.theme-dark" in html


def test_no_js_framework_loaded():
    """PRD §4.9: vanilla JS only — no React/Vue/Alpine/etc."""
    html = _render_index().lower()
    for needle in ("react", "vue", "alpine", "htmx", "jquery", "svelte"):
        assert needle not in html, f"unexpected framework reference: {needle}"
