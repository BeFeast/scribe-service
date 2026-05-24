from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPA_SRC = ROOT / "web" / "spa" / "src"


def read(path: str) -> str:
    return (SPA_SRC / path).read_text(encoding="utf-8")


def test_app_shell_files_exist() -> None:
    for path in (
        "components/CommandPalette.tsx",
        "components/ConfirmDialog.tsx",
        "components/TopBar.tsx",
        "components/Sidebar.tsx",
        "components/TweaksPanel.tsx",
        "constants.ts",
        "hooks/useEventSource.ts",
        "hooks/usePoll.ts",
        "hooks/useAuth.tsx",
        "hooks/useRoute.ts",
        "hooks/useTweaks.ts",
        "components/PrivateShareLinks.tsx",
        "pages/Library.tsx",
        "pages/Transcript.tsx",
        "shareTargets.ts",
    ):
        assert (SPA_SRC / path).is_file()


def test_app_mounts_shell_and_placeholder_router() -> None:
    source = read("main.tsx")

    assert "TopBar" in source
    assert "Sidebar" in source
    assert "CommandPalette" in source
    assert "TweaksPanel" in source
    assert "useRoute" in source
    assert "Library" in source
    assert "layout={tweaks.libraryLayout}" in source
    assert "setLibraryLayout={(libraryLayout)" in source
    assert "displayCurrency={displayCurrency}" in source
    assert 'auth.protectedFetch("/api/config")' in source
    assert 'route.page === "library"' in source
    assert 'route.page === "transcript"' in source
    assert "<Transcript" in source


def test_confirm_dialog_is_in_app_not_browser_prompt() -> None:
    dialog = read("components/ConfirmDialog.tsx")
    library = read("pages/Library.tsx")
    transcript = read("pages/Transcript.tsx")

    assert "ConfirmDialog" in library
    assert "ConfirmDialog" in transcript
    assert 'className="settings-modal compact confirm-dialog"' in dialog
    assert 'aria-modal="true"' in dialog
    assert "window.confirm" not in library + transcript + dialog


def test_transcript_page_fetches_json_and_renders_markdown_locally() -> None:
    source = read("pages/Transcript.tsx")
    markdown = read("components/Markdown.tsx")

    assert 'auth.protectedFetch(`/transcripts/${id}`' in source
    assert "`/transcripts/${id}/resummarize`" in source
    assert "`/admin/transcripts/${record.id}`" in source
    assert 'headers: { Accept: "application/json" }' in source
    assert 'import { Markdown } from "../components/Markdown";' in source
    assert "function parseBlocks" in markdown
    assert "function renderInline" in markdown
    assert "copyTextToClipboard" in source
    assert "Run summarizer" in source
    assert "Delete transcript" in source
    assert "record.source_url" in source
    assert "record.source_label" in source
    assert "youtu.be" not in source


def test_transcript_markdown_autolinks_plain_urls_safely() -> None:
    transcript = read("pages/Transcript.tsx")
    shared = read("components/Markdown.tsx")
    source = transcript + shared

    assert "plainUrlPattern = /https?:\\/\\/" in source
    assert "pushTextParts(parts, rest)" in shared
    assert '| { type: "link"; text: string; href: string }' in source
    assert 'target="_blank"' in source
    assert 'rel="noopener noreferrer"' in source
    assert "dangerouslySetInnerHTML" not in source


def test_library_page_fetches_api_and_supports_layouts() -> None:
    source = read("pages/Library.tsx")
    command_palette = read("components/CommandPalette.tsx")

    assert "function InFlightStrip" in source
    assert "function InFlightRow" in source
    assert "function LibTable" in source
    assert "function LibFeed" in source
    assert "function LibCards" in source
    assert "buildLibraryUrl(debouncedQuery, selectedTag, libraryPageSize, offset)" in source
    assert '["tag", tag ?? ""]' in source
    assert '["limit", String(limit)]' in source
    assert '["offset", String(offset)]' in source
    assert "setRetryTick((value) => value + 1)" in source
    assert "formatUsdCost(row.vast_cost, displayCurrency)" in source
    assert "Previous" in source
    assert "Next" in source
    assert "window.setTimeout(() => setDebouncedQuery(query), 200)" in source
    assert 'auth.protectedFetch("/api/jobs/active"' in source
    assert 'auth.protectedFetch("/jobs"' in command_palette
    assert "`/admin/transcripts/${row.id}`" in source
    assert 'source: "manual"' in command_palette
    assert "CMDK_OPEN_EVENT" in source
    assert "Submit URL" in source
    assert 'className="lib-toolbar"' in source
    assert 'className="search"' in source
    assert "Search titles + transcripts" in source
    assert 'className="library-submit"' not in source
    assert "Title or summary" not in source
    assert '<h1 className="pane-h1">Library</h1>' in source
    assert "Transcripts</h1>" not in source
    assert "hasNonTerminalJob(jobs) ? 5000 : 30000" in source
    assert "usePoll(poll, interval)" in source
    assert "chip warn" in source
    assert "partial" in source
    assert "Delete transcript" in source
    assert 'className="col-num"' in source
    assert 'className="feed-num"' in source
    assert 'className="card-title"' in source
    assert 'className="card-excerpt"' in source
    assert 'layout === "table"' in source
    assert 'layout === "feed"' in source
    assert 'layout === "cards"' in source
    assert "row.source_url" in source
    assert "row.source_label" in source


def test_library_rejects_old_repainted_dom_and_default_only_tweaks() -> None:
    library = read("pages/Library.tsx")
    hooks = read("hooks/useTweaks.ts")
    styles = read("styles.css")

    assert "library-hero" not in library
    assert "library-search" not in library
    assert "library-submit" not in library
    assert "Title or summary" not in library
    assert "Transcripts</h1>" not in library
    assert '<h1 className="pane-h1">Library</h1>' in library
    assert 'className="lib-toolbar"' in library
    assert 'layout === "table"' in library
    assert 'layout === "feed"' in library
    assert 'layout === "cards"' in library

    assert 'export type ScribeVariant = "paper" | "terminal" | "console" | "field";' in hooks
    assert 'export type ScribeTheme = "light" | "dark";' in hooks
    assert 'export type ScribeDensity = "compact" | "cozy" | "comfy";' in hooks
    for selector in (
        '[data-variant="paper"]',
        '[data-variant="terminal"]',
        '[data-variant="console"]',
        '[data-variant="field"]',
        '[data-density="compact"]',
        '[data-density="cozy"]',
        '[data-density="comfy"]',
        '[data-theme="dark"][data-variant="field"]',
    ):
        assert selector in styles


def test_queue_and_job_detail_can_clear_failed_jobs() -> None:
    queue = read("pages/Queue.tsx")
    detail = read("pages/JobDetail.tsx")
    failure_row = read("components/FailureRow.tsx")

    assert "auth.protectedFetch(`/admin/jobs/${id}`" in queue
    assert 'method: "DELETE"' in queue
    assert "onDismiss={clearFailure}" in queue
    assert "Clear" in failure_row
    assert "auth.protectedFetch(`/admin/jobs/${job.job_id}`" in detail
    assert "Clear failure" in detail
    assert "job.source_url" in detail
    assert "job.source_label" in detail
    assert "YouTube" not in detail


def test_queue_active_jobs_can_cancel_running_jobs() -> None:
    """Queue Active Jobs must wire the cancel control to the protected admin
    cancel endpoint and use the in-app ConfirmDialog (not window.confirm).
    See issue #127."""
    queue = read("pages/Queue.tsx")
    job_card = read("components/JobCard.tsx")

    # Cancel posts to the existing protected admin endpoint.
    assert "auth.protectedFetch" in queue
    assert "`/admin/jobs/${job.id}/cancel`" in queue
    assert 'method: "POST"' in queue

    # No browser-native confirm/alert paths.
    assert "window.confirm" not in queue + job_card
    assert "window.alert" not in queue + job_card
    assert "window.prompt" not in queue + job_card

    # Confirmation flows through the existing in-app dialog.
    assert "ConfirmDialog" in queue
    assert "cancelCandidate" in queue
    assert 'confirmLabel="Cancel job"' in queue

    # JobCard exposes the cancel control, with busy + disabled signals.
    assert "onCancel" in job_card
    assert "cancelBusy" in job_card
    assert "aria-busy" in job_card
    assert "disabled={cancelBusy || cancelDisabled}" in job_card
    assert "Cancelling" in job_card

    # Polling stays in place alongside the cancel flow.
    assert "usePoll(load, 2000)" in queue

    # Cancelled job IDs are tracked so an in-flight or lagging
    # /api/jobs/active poll cannot re-add the row after a successful cancel.
    assert "cancelledIdsRef" in queue
    assert "cancelledIdsRef.current.add(job.id)" in queue
    assert "cancelledIdsRef.current.has(job.id)" in queue

    # Cancel failures must close the modal so the inline error banner is
    # visible instead of staying hidden behind the backdrop.
    assert "setCancelCandidate(null)" in queue
    assert (
        "} finally {\n\t\t\tsetCancelBusyId(null);\n"
        "\t\t\tsetCancelCandidate(null);\n\t\t}"
    ) in queue


def test_queue_and_job_detail_match_handoff_structure_and_real_wiring() -> None:
    queue = read("pages/Queue.tsx")
    detail = read("pages/JobDetail.tsx")
    job_card = read("components/JobCard.tsx")
    pipeline = read("components/PipelineDiagram.tsx")
    failure_row = read("components/FailureRow.tsx")
    log_tail = read("components/LogTail.tsx")
    source = queue + detail + job_card + pipeline + failure_row + log_tail

    for expected in (
        'className="pane queue-page"',
        '<h1 className="pane-h1">Queue</h1>',
        "workers",
        "busy",
        "<IconRefresh size={14} /> Poll now",
        "<IconPlus size={14} /> Submit URL",
        "document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT))",
        "Recent terminal jobs &middot; failed",
        'className="job-card-header"',
        'className="job-card-dot"',
        'className="job-card-open"',
        "<PipelineDiagram stages={job.stages} compact />",
        'className="queue-back"',
        'className="row job-detail-meta-row"',
        'className="detail-h1"',
        'className="detail-meta"',
        "<PipelineDiagram stages={job.stages} />",
        'className="log-tail"',
        "Pipeline log",
        "Job actions",
        'className="runtime-notes"',
    ):
        assert expected in source

    for expected in (
        "Waiting for a worker slot",
        "yt-dlp · residential IP",
        "faster-whisper · Vast.ai GPU",
        "codex CLI · prompt template v3",
        "Shortlinks · webhook · DB write",
        'grid-template-columns: repeat(5',
        ".pipeline .stage.active",
        ".pipeline .stage.done",
        ".pipeline .stage.failed",
    ):
        assert expected in pipeline + read("styles.css")

    assert 'auth.protectedFetch("/api/jobs/active"' in queue
    assert 'auth.protectedFetch("/api/jobs/recent-failures?limit=5"' in queue
    assert 'auth.protectedFetch("/api/ops"' in queue + detail
    assert "usePoll(load, 2000)" in queue
    assert "usePoll(load, 2000, { enabled: id !== undefined && !isTerminal })" in detail
    assert "`/admin/jobs/${job.job_id}/retry`" in detail
    assert "`/admin/jobs/${job.job_id}/cancel`" in detail
    assert "ConfirmDialog" in detail
    assert "job.source_url" in detail
    assert "job.source_label" in detail
    assert "useEventSource" in log_tail
    assert "`/api/jobs/${jobId}/log/stream`" in log_tail

    for forbidden in (
        "Math.random",
        'new Date("2026-05-16T09:45:32Z")',
        "buildLog(",
        "https://youtu.be/${",
        "window.confirm",
        "job-panel",
    ):
        assert forbidden not in source


def test_live_update_hooks_wrap_poll_and_eventsource_lifecycles() -> None:
    use_poll = read("hooks/usePoll.ts")
    use_event_source = read("hooks/useEventSource.ts")

    assert "document.hidden" in use_poll
    assert 'document.addEventListener("visibilitychange"' in use_poll
    assert "AbortController" in use_poll
    assert "window.clearTimeout" in use_poll
    assert "controller?.abort()" in use_poll
    assert "new EventSource(url)" in use_event_source
    assert "source.close()" in use_event_source


def test_spa_auth_config_and_protected_fetch_are_wired() -> None:
    source = read("hooks/useAuth.tsx")
    main = read("main.tsx")
    settings = read("pages/Settings.tsx")
    command_palette = read("components/CommandPalette.tsx")
    transcript = read("pages/Transcript.tsx")

    assert 'fetch("/api/auth/config"' in source
    assert "clerk_publishable_key" in source
    assert "clerk_frontend_api" in source
    assert "trusted_network" in source
    assert "@clerk/ui@1/dist/ui.browser.js" not in source
    assert "@clerk/clerk-js@6/dist/clerk.browser.js" in source
    assert "__internal_ClerkUICtor" not in source
    assert "redirectToSignIn" in source
    assert "redirectToSignUp" in source
    assert "addListener" in source
    assert "setSignedIn(Boolean(window.Clerk?.session))" in source
    assert "setSignedIn(Boolean(session))" in source
    assert "window.Clerk?.session?.getToken()" in source
    assert 'headers.set("Authorization", `Bearer ${token}`)' in source
    assert "protectedFetch" in source
    assert "AuthProvider" in main
    assert 'auth.protectedFetch("/api/config"' in settings
    assert 'auth.protectedFetch("/jobs"' in command_palette
    assert "`/transcripts/${id}/resummarize`" in transcript


def test_settings_creates_extension_tokens_without_browser_prompts() -> None:
    settings = read("pages/Settings.tsx")

    assert 'auth.protectedFetch("/api/auth/extension-token"' in settings
    assert "Chrome extension token" in settings
    assert "Extension token created" in settings
    assert "localStorage" not in settings
    assert "window.prompt" not in settings
    assert "/api/config/rotate-token" not in settings
    assert "Rotate API token" not in settings


def test_settings_access_section_wires_current_user_and_admin_users() -> None:
    settings = read("pages/Settings.tsx")

    assert "AccessSection" in settings
    assert 'auth.protectedFetch("/api/auth/me")' in settings
    assert 'auth.protectedFetch("/api/admin/users")' in settings
    assert "Admin role required to manage Scribe users." in settings
    assert "At least one active admin account is required." in settings
    assert "auth.authBlockedMessage === null" in settings
    assert "Add or update user" in settings
    assert "`/api/admin/users/${disableTarget.id}/disable`" in settings
    assert "ConfirmDialog" in settings
    assert "window.alert" not in settings
    assert "window.confirm" not in settings
    assert "window.prompt" not in settings


def test_global_shell_shows_access_status_and_operator_auth() -> None:
    settings_source = read("pages/Settings.tsx")
    topbar = read("components/TopBar.tsx")
    main = read("main.tsx")

    for label in ("Trusted network", "Signed in", "Read-only"):
        assert label in read("hooks/useAuth.tsx")
    assert "auth.accessStatus" in settings_source
    assert "auth.signIn" in settings_source
    assert "auth.signUp" in settings_source
    assert "auth.signOut" in settings_source
    assert "auth.accessStatus" in topbar
    assert "auth.signIn" in topbar
    assert "auth.signUp" in topbar
    assert "auth.signOut" in topbar
    assert 'route.page === "settings"' in main


def test_transcript_route_does_not_render_auth_gate() -> None:
    """Shared transcript pages are intentionally public (read-only via shortlinks);
    they must not force a Clerk gate on first load. Library is owner-scoped after
    #119/#129, so it can render an auth-required state on 401/403."""
    transcript = read("pages/Transcript.tsx")

    assert "Sign in" not in transcript
    assert "auth.accessStatus" not in transcript


def test_cmdk_custom_event_is_wired_without_window_globals() -> None:
    constants = read("constants.ts")
    main = read("main.tsx")
    topbar = read("components/TopBar.tsx")
    palette = read("components/CommandPalette.tsx")

    assert 'export const CMDK_OPEN_EVENT = "scribe:cmdk-open";' in constants
    assert "CommandPalette" in main
    assert "CMDK_OPEN_EVENT" in topbar
    assert "CMDK_OPEN_EVENT" in palette
    assert 'document.addEventListener(CMDK_OPEN_EVENT, open)' in palette
    assert 'window.addEventListener("keydown", keydown, { capture: true })' in palette
    assert 'window.removeEventListener("keydown", keydown, { capture: true })' in palette
    assert "isCommandPaletteShortcut(event)" in palette
    assert 'key === "unidentified" && event.code === "KeyK"' in palette
    assert "event.stopPropagation()" in palette
    assert "document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT))" in topbar
    assert "window.scribe" not in main + topbar + palette


def test_command_palette_covers_submit_search_navigation_and_a11y() -> None:
    source = read("components/CommandPalette.tsx")

    assert "parseVideoUrl" in source
    assert "url.protocol !== \"http:\"" in source
    assert 'auth.protectedFetch("/jobs"' in source
    assert 'source: "manual"' in source
    assert "Queued as job #" in source
    assert "Watch pipeline" in source
    assert 'protectedFetch("/api/library?limit=100"' in source
    assert 'protectedFetch("/api/jobs/active"' in source
    assert "Go to library" in source
    assert "Go to queue" in source
    assert "Go to ops dashboard" in source
    assert "Go to settings" in source
    assert "TRANSCRIPTS" not in source
    assert "ACTIVE_JOBS" not in source
    assert "STATS" not in source
    assert "Recent submissions" in source
    assert "recent-job-${job.id}" in source
    assert "recent-transcript-${transcript.id}" in source
    assert "Linus Torvalds on Git" not in source
    assert "Rich Hickey" not in source
    assert "Bryan Cantrill" not in source
    assert "source=manual" in source
    assert "Scribe could not submit that job" in source
    assert "Sign in is required" in source
    assert "<dialog" in source
    assert "cmdk-modal" in source
    assert 'aria-modal="true"' in source
    assert 'event.key === "Escape"' in source
    assert 'event.key === "Enter"' in source
    assert 'event.key === "Tab"' in source
    assert "new AbortController()" in source
    assert 'setQuery("")' in source
    assert 'submitState.state === "success"' in source
    assert "safeSelectedIndex" in source
    assert "watchPipeline(submitState.job.job_id)" in source
    assert "navigate(route(\"job\", jobId))" in source
    assert "setSubmitted({ id: 219" not in source
    assert "navigate(\"job\", { id: 218 })" not in source
    assert "Queued as job #219" not in source
    assert "cmdk-modal" in source
    assert "cmdk-input-row" in source


def test_tweaks_defaults_persist_and_apply_to_html_dataset() -> None:
    source = read("hooks/useTweaks.ts")

    assert 'const STORAGE_KEY = "scribe.tweaks";' in source
    assert 'variant: "field"' in source
    assert 'theme: "light"' in source
    assert 'density: "compact"' in source
    assert 'libraryLayout: "feed"' in source
    for value in ('"paper"', '"terminal"', '"console"', '"field"'):
        assert value in source
    assert '"dark"' in source
    assert '"cozy"' in source
    assert '"comfy"' in source
    assert "localStorage.getItem(STORAGE_KEY)" in source
    assert "localStorage.setItem(STORAGE_KEY" in source
    assert "parsed.variant" in source
    assert "parsed.density" in source
    assert "parsed.libraryLayout" in source
    assert "variants.has(parsed.variant)" in source
    assert "densities.has(parsed.density)" in source
    assert "libraryLayouts.has(parsed.libraryLayout)" in source
    assert 'type: "variant"' not in source
    assert 'type: "density"' not in source
    assert 'type: "libraryLayout"' not in source
    assert "document.documentElement" in source
    assert "dataset.variant = tweaks.variant" in source
    assert "dataset.theme = tweaks.theme" in source
    assert "dataset.density = tweaks.density" in source
    assert "dataset.libraryLayout = tweaks.libraryLayout" in source


def test_tweaks_panel_exposes_design_variant_theme_density_controls() -> None:
    hooks = read("hooks/useTweaks.ts")
    topbar = read("components/TopBar.tsx")
    panel = read("components/TweaksPanel.tsx")

    assert 'export type ScribeTheme = "light" | "dark";' in hooks
    for value in ('"paper"', '"terminal"', '"console"', '"field"'):
        assert value in panel
    assert 'const themeOptions: ScribeTheme[] = ["light", "dark"]' in panel
    assert 'const densityOptions: ScribeDensity[] = ["compact", "cozy", "comfy"]' in panel
    assert "Toggle theme" in topbar
    assert "replaceTweaks({ ...tweaks, theme:" in topbar


def test_sidebar_routes_protected_fetch_without_mock_fallback() -> None:
    """Per #129, the sidebar must not paper over auth failures with mock data
    or `[mock]` badges. Auth failures route into a Sign-in CTA, generic
    failures render an unavailable state, and success renders fetched data."""
    source = read("components/Sidebar.tsx")

    assert 'auth.protectedFetch("/api/library?limit=100"' in source
    assert 'auth.protectedFetch("/api/ops"' in source
    assert "[mock]" not in source
    assert "mockTags" not in source
    assert "mockPipeline" not in source
    assert "mock-chip" not in source
    assert "auth-required" in source
    assert "Sign in" in source


def test_route_hook_uses_typed_hash_routes() -> None:
    source = read("hooks/useRoute.ts")

    assert "export type RoutePage =" in source
    for page in ("library", "transcript", "queue", "job", "ops", "settings"):
        assert f'| "{page}"' in source
    assert "window.location.hash" in source
    assert "window.addEventListener(\"hashchange\"" in source
    assert "type RouteAction" in source
    assert 'case "jobs":' in source
    assert 'parts[0] = "jobs";' in source
    assert "export function routeToHref" in source
    assert "export function handleRouteAnchorClick" in source


def test_spa_internal_navigation_uses_real_relative_anchors() -> None:
    sidebar = read("components/Sidebar.tsx")
    tweaks = read("components/TweaksPanel.tsx")
    library = read("pages/Library.tsx")
    transcript = read("pages/Transcript.tsx")
    job_card = read("components/JobCard.tsx")
    failure_row = read("components/FailureRow.tsx")
    job_detail = read("pages/JobDetail.tsx")
    route = read("hooks/useRoute.ts")
    source = sidebar + tweaks + library + transcript + job_card + failure_row + job_detail

    assert 'href={routeToHref(nextRoute)}' in sidebar
    assert 'href={routeToHref(item.route)}' in tweaks
    assert 'className="job-card-open"' in job_card
    assert 'className="failure-action"' in failure_row
    assert "row.summary_shortlink" not in library
    assert "row.transcript_shortlink" not in library
    assert "record.summary_shortlink" not in transcript
    assert "PrivateShareLinks" in library
    assert "copyTextToClipboard" in transcript
    assert "`/transcripts/${transcript.id}/summary.md`" in transcript
    assert 'href={`/transcripts/${record.id}/transcript.md`}' in transcript
    assert "handleRouteAnchorClick" in source
    assert "metaKey" in route
    assert "ctrlKey" in route
    assert "event.button !== 0" in route
    assert 'return `#/${parts.join("/")}${query ? `?${query}` : ""}`;' in route
    assert "go.oklabs.uk" not in source + route


def test_private_share_target_helper_uses_managed_target_kinds() -> None:
    source = read("shareTargets.ts")

    assert '{ kind: "page", label: "Page" }' in source
    assert 'kind: "summary"' in source
    assert 'kind: "transcript"' in source
    assert "/transcripts/${id}" not in source


def test_transcript_and_library_share_ui_use_managed_share_links() -> None:
    share = read("components/PrivateShareLinks.tsx")
    library = read("pages/Library.tsx")
    transcript = read("pages/Transcript.tsx")
    source = share + library + transcript

    assert "transcriptShareTargets(id)" in source
    assert "`/api/transcripts/${id}/share-links`" in share
    assert "`/api/share-links/${link.id}/revoke`" in share
    assert "copyTextToClipboard(created.share_url)" in share
    assert "href={target.href}" not in source
    assert 'const pageCopyKinds = new Set<ShareTarget["kind"]>(["page"]);' in library
    assert "copyKinds={pageCopyKinds}" in library
    assert 'const partialShareTargetKinds = new Set<ShareTarget["kind"]>([' in library
    assert "row.is_partial ? partialShareTargetKinds : completeShareTargetKinds" in library
    assert "targetKinds" in share
    assert "row.source_url" in library
    assert "record.source_url" in transcript
    assert "`/api/transcripts/${transcript.id}/share-links`" in transcript
    assert "`/api/share-links/${link.id}/revoke`" in transcript
    assert "created.share_url" in transcript


def test_transcript_detail_matches_handoff_structure_and_real_wiring() -> None:
    transcript = read("pages/Transcript.tsx")
    styles = read("styles.css")
    markdown = read("components/Markdown.tsx")
    source = transcript + styles + markdown

    for expected in (
        'className="row transcript-top-row"',
        'className="share-wrap"',
        '<div className="share-sheet" ref={ref}>',
        'className="sh-hd"',
        'className="sh-url"',
        'className="sh-section-label">Copy as Markdown',
        'className="sh-section-label">Download',
        'className="sh-section-label">Managed links',
        'className="mono muted transcript-kicker"',
        'className="detail-h1"',
        'className="detail-meta"',
        'className="detail-tags"',
        "Transcript excerpt",
        'className="transcript-body"',
        'className="detail-footer"',
        'className="danger-zone"',
        'className="btn danger"',
        "<ConfirmDialog",
        "<Markdown body={summaryBody} />",
        "<Markdown body={stripFrontmatter(record.transcript_md)} />",
    ):
        assert expected in transcript

    for forbidden in (
        "summary_shortlink",
        "transcript_shortlink",
        "go.oklabs",
        "youtu.be",
        "window.confirm",
        "dangerouslySetInnerHTML",
    ):
        assert forbidden not in source

    assert "`/api/transcripts/${transcript.id}/share-links`" in transcript
    assert "`/api/share-links/${link.id}/revoke`" in transcript
    assert "`/transcripts/${transcript.id}/summary.md`" in transcript
    assert "`/transcripts/${transcript.id}/transcript.md`" in transcript
    assert "`/transcripts/${id}/resummarize`" in transcript
    assert "`/admin/transcripts/${record.id}`" in transcript
    assert "record.source_url" in transcript
    assert "record.source_label" in transcript


def test_private_share_links_are_compact_and_have_clipboard_fallback() -> None:
    share = read("components/PrivateShareLinks.tsx")
    styles = read("styles.css")

    assert 'className="private-share"' in share
    assert '<summary className="btn ghost">Share</summary>' in share
    assert 'className="private-share-panel"' in share
    assert 'className="share-menu-row"' in share
    assert 'className="share-targets"' not in share
    assert 'className="share-target"' not in share
    assert "export async function copyTextToClipboard" in share
    assert "navigator.clipboard?.writeText" in share
    assert 'document.createElement("textarea")' in share
    assert 'document.execCommand("copy")' in share
    assert "Link created. Allow clipboard access, then try again." in share
    assert "window.setTimeout(() => setCopyState(null), 4500)" in share
    assert ".private-share-panel" in styles
    assert "position: absolute;" in styles
    assert ".row-links .private-share" in styles


def test_internal_share_ui_does_not_use_shortlinks() -> None:
    source = (
        read("components/PrivateShareLinks.tsx")
        + read("pages/Library.tsx")
        + read("pages/Transcript.tsx")
        + read("shareTargets.ts")
    )

    assert "go.oklabs.uk" not in source
    assert "summary_shortlink" not in source
    assert "transcript_shortlink" not in source
    assert "shortlink" not in source.lower()
