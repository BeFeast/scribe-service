# Social caption titles

The downloader normalizes whitespace, strips pipe-delimited English social
engagement counters, and removes a trailing author suffix only when it matches
the extracted author name. Titles longer than 120 characters after cleanup are
cut at a word boundary to at most 100 characters including the ellipsis. A single
overlong word falls back to a character cut.

`jobs.original_title` and `transcripts.original_title` retain the exact raw value
only when it differs from the display title. The nullable columns are added by
Alembic revision `j4k5l6m7n807`; the migration does not rewrite existing data.
The transcript detail API exposes `original_title`.

For long captions, worker completion and API resummarize reuse the first useful
Markdown heading in the existing summary, skipping frontmatter, code fences and
generic headings such as TL;DR, Summary and Кратко. The heading is cleaned and
bounded to 120 characters. Without a useful heading, the truncated caption is
kept. There is no additional LLM call. The original caption preserves the
long-title decision across retries. Short titles are not replaced by headings.

Summary frontmatter `source` uses the resulting title as a Unicode-safe slug,
capped at 80 characters. Tag slug rules are unchanged. Library titles have a
three-line CSS clamp and full-text tooltips; detail titles wrap without a clamp.

## Existing rows

After upgrading the target database schema, use the backfill script with an
explicit database URL. The default is a dry-run that prints old and new titles
and rolls back; `--apply` commits. Both modes also handle jobs without a
transcript and update summary source links. Repeated application is idempotent.
The script makes no network or LLM calls and leaves transcript bodies intact.

```bash
SCRIBE_DATABASE_URL='<target-database-url>' uv run python scripts/normalize_titles.py
SCRIBE_DATABASE_URL='<target-database-url>' uv run python scripts/normalize_titles.py --apply
```

No production backfill is part of the implementation PR. Tests use the isolated
database selected by `SCRIBE_TEST_DATABASE_URL` and exercise dry-run, apply,
repeat application, migration upgrade/downgrade, and worker/API write-back.
