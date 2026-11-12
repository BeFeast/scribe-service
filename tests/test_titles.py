"""Social metadata cleanup must preserve ordinary, multilingual titles."""
import pytest

from scribe.pipeline.titles import (
    MAX_SOURCE_SLUG_LENGTH,
    MAX_TITLE_LENGTH,
    TRUNCATED_TITLE_LENGTH,
    normalize_title,
    summary_heading,
    summary_source,
    title_slug,
)


@pytest.mark.parametrize("prefix", [
    "9.9K views · 61 reactions | ",
    "1M Views • 2.5K Likes | ",
    "1,234 views | ",
    "1 view · 1 reaction | ",
    "2.1B plays · 56 comments · 3 shares | ",
])
def test_engagement_prefix(prefix):
    assert normalize_title(prefix + "A useful title | Sergey Pimenov", author="Sergey Pimenov") == (
        "A useful title", False,
    )


@pytest.mark.parametrize("title", [
    "A short title", "Notion как общая память для ИИ-агентов", "זיכרון משותף לסוכני בינה מלאכותית",
    "100 views of Jerusalem", "Tools | Unknown Author", "100 views | a study",
])
def test_short_titles(title):
    expected = "a study" if title == "100 views | a study" else title
    assert normalize_title(title) == (expected, False)


def test_whitespace_and_known_author_only():
    assert normalize_title("  Hello\n\tworld | Jane Doe  ", author="jane   doe") == ("Hello world", False)
    assert normalize_title("Hello | Jane Doe", author="Someone Else") == ("Hello | Jane Doe", False)
    assert normalize_title("Hello | Jane Doe") == ("Hello | Jane Doe", False)


@pytest.mark.parametrize("word", ["caption", "кириллица", "עברית", "x" * 150])
def test_long_titles_are_bounded(word):
    raw = (word + " ") * 40
    title, was_long = normalize_title(raw)
    assert was_long
    assert len(title) <= TRUNCATED_TITLE_LENGTH
    assert title.endswith("…")
    assert normalize_title(title) == (title, False)
    if len(word) < TRUNCATED_TITLE_LENGTH:
        assert title.removesuffix("…").endswith(word)


def test_threshold_after_removing_metadata():
    assert normalize_title("9.9K views · 61 reactions | " + "a" * 120) == ("a" * 120, False)
    assert normalize_title("a" * 121)[1]


@pytest.mark.parametrize("markdown,expected", [
    ('---\nsource: "[[huge]]"\n# fake heading\n---\n## **Notion** как общая память\n', "Notion как общая память"),
    ("## TL;DR\ntext\n## Summary\n## Кратко\n## A meaningful topic", "A meaningful topic"),
    ("```md\n# Fake\n```\n### [Shared memory](https://example.test) ###", "Shared memory"),
    ("~~~\n# Fake\n~~~\n# זיכרון משותף", "זיכרון משותף"),
    ("## Почему стоит учить C#", "Почему стоит учить C#"),
    ("## Learn F# fast ##", "Learn F# fast"),
    ("## Summary\nNo other heading", None),
    ("ordinary prose", None),
    (None, None),
])
def test_summary_heading(markdown, expected):
    assert summary_heading(markdown) == expected


def test_heading_is_bounded():
    assert len(summary_heading("## " + "topic " * 70)) <= MAX_TITLE_LENGTH


def test_source_is_bounded_unicode_and_body_is_preserved():
    title = "Память для агентов " * 60
    slug = title_slug(title)
    assert len(slug) <= MAX_SOURCE_SLUG_LENGTH
    assert slug.startswith("память-для-агентов")
    md = '---\nsource: "[[old]]"\ntags: [ai]\n---\n## Heading\nsource: [[body]]\n'
    result = summary_source(md, title)
    assert f'source: "[[{slug}]]"' in result
    assert result.endswith("## Heading\nsource: [[body]]\n")
    assert summary_source(result, title) == result
    assert summary_source("# No YAML", title) == "# No YAML"
    assert title_slug("!!!") == "transcript"


def test_summary_prompt_uses_a_bounded_source_slug():
    from scribe.pipeline.summarizer import _build_prompt

    raw = "Caption about AI agents " * 80
    for explicit_slug in (None, "overlong-slug-" * 100):
        _, instructions = _build_prompt(
            "transcript", title=raw, transcript_slug=explicit_slug, summary_date=None,
            prompt_version=None, prompt_body='source: "[[{transcript_slug}]]"',
        )
        slug = instructions.split("[[", 1)[1].split("]]", 1)[0]
        assert len(slug) <= MAX_SOURCE_SLUG_LENGTH
        assert slug == title_slug(explicit_slug or raw)
