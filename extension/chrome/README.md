# Scribe Chrome Extension

Small Manifest V3 operator tool for submitting video URLs to Scribe.

## Install

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose Load unpacked.
4. Select `extension/chrome`.

No build step is required.

## Configure

Open the extension options page and set:

- Scribe base URL, defaulting to `https://scribe.oklabs.uk`.
- Optional bearer token. Create a Chrome extension token in Scribe Settings, then paste it here.
  A bearer token is required when the configured Scribe URL is protected, especially when using it
  outside a trusted LAN.

Saving the base URL asks Chrome for permission to reach that Scribe origin (an optional
host permission requested dynamically — the extension never asks for all-sites access up
front). The token is stored in this device's local extension storage only (`chrome.storage.local`) — it
is **not** written to `chrome.storage.sync`, so it is never uploaded to the Chrome cloud-sync account.
The base URL is not secret and is kept in `chrome.storage.sync` for cross-device convenience.
The options page also surfaces a **Last authenticated** timestamp, updated each time Scribe accepts
the saved token (a successful `GET /preflight` or `POST /jobs`), so a silently-revoked token is
visible without waiting for a 401. No token is hardcoded in the extension.

## Permissions

The extension requests only the host origins it actually needs:

- `https://scribe.oklabs.uk/*` as a required `host_permissions` entry (the default
  Scribe origin).
- YouTube origins (`https://*.youtube.com/*` and `https://youtu.be/*`) as
  **optional** `optional_host_permissions`, granted on demand when you enable
  YouTube cookies.
- The user-configured Scribe base URL origin is requested dynamically as an
  optional permission from the options page when you save a non-default base URL.

No `http://*/*` / `https://*/*` all-sites wildcard is requested. Extension HTML
pages (popup and options) ship a `Content-Security-Policy` meta restricting
`script-src` and `style-src` to `'self'`; styles live in external stylesheets
(`popup.css`, `options.css`) and there are no inline scripts.

## Preflight & confirm (no blind submits)

Clicking the toolbar icon opens a small popup — it does **not** silently mint a
job. The popup asks Scribe's `GET /preflight` whether the deployed `yt-dlp`
treats the active tab as a single playable video (offline extractor matching,
no download):

- **Single video** (e.g. `https://www.youtube.com/watch?v=…`) — submitted on
  the spot; the popup shows the receipt with an `Open job #N` link.
- **Feed / playlist / channel / search** (e.g. the YouTube home page
  `https://www.youtube.com/`, `/@channel`, `/playlist?list=…`,
  `/results?search_query=…`) — **not** submitted. The popup explains it is not
  a single video and offers a **Submit anyway** button so you decide.
- **Unknown / preflight unreachable** — same confirm step; the check is a
  courtesy, never an infrastructure hard-block.

The right-click context-menu paths apply the same gate: a single video submits,
a container/unknown page gets a "not submitted" notification telling you to open
the page and use the toolbar to confirm.

### YouTube cookies (optional)

To submit gated YouTube videos (age-restricted, member-only, private) the
extension can forward your `youtube.com` cookies with each submission. On the
options page, click **Enable YouTube cookies** and approve Chrome's host
permission prompt for `https://*.youtube.com/*`. The extension then:

- Reads cookies fresh via `chrome.cookies.getAll({ domain: ".youtube.com" })`
  on every submit; nothing is cached or written to extension storage.
- Serializes them to a Netscape `cookies.txt` blob and attaches the result as
  `youtube_cookies` on the `POST /jobs` body. Non-YouTube URLs never include
  cookies.
- Never logs cookie names or values; only counts/sizes are observable.

When the host permission is not granted, or no cookies exist for `.youtube.com`,
submissions still go through — Scribe just falls back to the public download
path. Click **Disable** on the options page to revoke the permission at any
time.

#### Auth for cookie submits (owner token vs. trusted LAN)

By default, `POST /jobs` accepts `youtube_cookies` only from an **owner-attached**
actor — i.e. with an extension bearer token minted in Scribe Settings (which in
turn needs a Clerk sign-in). A trusted-LAN request without a token is authenticated
by network but not tied to an owner, so a cookie submit is rejected with
`403 "youtube_cookies requires owner or extension-token authentication"`.

For a single-operator LAN deployment where Clerk sign-in is unavailable, an operator
can opt in by setting `SCRIBE_LAN_YOUTUBE_COOKIES_ENABLED=true` (also togglable at
runtime via `POST /api/config`). With the flag on, a submit from a trusted-LAN
client (per `SCRIBE_TRUSTED_CIDRS` / `SCRIBE_TRUSTED_PROXIES`) may include
`youtube_cookies` **without** a bearer token; the job is attributed to the default
owner (`SCRIBE_DEFAULT_OWNER_SUBJECT` / `SCRIBE_DEFAULT_OWNER_EMAIL`). The flag
changes nothing else: machine-bearer callers and non-LAN callers are still rejected,
the cookie blob is still validated, size-capped (256 KiB), kept per-job ephemeral,
and never persisted or logged. Leave the flag off (default) for multi-user or
public deployments so the strict owner gate stays in force.

## Open Scribe queue

Right-clicking the toolbar action shows an **Open Scribe queue** item (#378).
Clicking it opens the configured Scribe `baseUrl` in a new tab — the fastest
way back to the web app when a job fails or you just want to look at the
queue. It lives on the action's context menu (`contexts: ["action"]`), so
it never clutters the page/link right-click menus used for submitting videos.

## Manual Verification

1. Load the unpacked extension and keep the default Scribe base URL or set a local/runtime Scribe URL.
2. If using a non-default Scribe URL, open the extension options page, save the base URL, and approve Chrome's host access prompt.
3. Open the YouTube **home** page (`https://www.youtube.com/`) and click the toolbar action; confirm the popup says it is not a single video and shows **Submit anyway** — no job is minted unless you click it.
4. Open a video watch page supported by `yt-dlp` and click the toolbar action; confirm the popup submits in one click and shows an `Open job #N` link to `{Scribe base URL}/#/jobs/{job_id}`.
5. Right-click a video page and choose Submit this video page to Scribe; confirm success or already-known status is shown clearly.
6. Right-click a video link and choose Submit video link to Scribe; confirm success or already-known status is shown clearly.
7. For a protected Scribe URL, leave the bearer token blank and submit again; confirm a 401/403 notification explains that auth is required.
8. Set an invalid bearer token for a protected Scribe URL and submit again; confirm the notification explains that the token is invalid or unauthorized.
9. Set the base URL to an unreachable host and submit again; confirm the popup includes a useful connectivity error from the unreachable host.
10. Submit a non-http(s) toolbar page; confirm the extension reports that an http(s) video page is required.
11. Right-click the toolbar action and choose **Open Scribe queue**; confirm the configured `baseUrl` opens in a new tab.

The extension posts to Scribe's existing `POST /jobs` API with:

```json
{"url":"https://example.com/video-page","source":"chrome-extension"}
```

For YouTube submissions with the cookie permission granted, the body also
includes `"youtube_cookies": "<Netscape cookies.txt blob>"`.
