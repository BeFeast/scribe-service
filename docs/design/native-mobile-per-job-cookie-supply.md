# Design: native mobile per-job cookie supply (WKWebView / Android WebView)

Status: **Design only — not implemented.** Tracks #315, a child of #308
(download anti-bot Layer B). Mirrors the karaoke-service #78 design.

This document specifies how a native mobile app (iOS + Android) supplies the
user's youtube.com session cookies with a job submission, reusing the exact
`POST /jobs` contract already shipped for the Chrome extension (#314) and the
`youtube_cookies` API field (#312). No production code is changed by this
issue. Implementation is explicitly blocked until the desktop/extension path
(#308 children) is verified end-to-end in production.

## 1. Goals and non-goals

### Goals

- Let a logged-in native mobile user submit gated YouTube videos
  (age-restricted, member-only, private) to Scribe from their own device,
  using their own youtube.com session, without ever typing cookies by hand.
- Reuse the existing per-job cookie contract verbatim: a Netscape
  `cookies.txt` blob posted as `youtube_cookies` on `POST /jobs`.
- Inherit the existing server-side security model unchanged: cookies are
  owner-scoped secrets, never persisted, never logged, per-job ephemeral,
  with a public-only fallback when the user does not opt in.

### Non-goals

- Implementing any of this. No mobile app code, no API change, no DB
  migration, no new endpoint. The server contract is already shipped.
- A general-purpose "cookie vault" that survives across jobs or sessions.
- Supplying cookies for non-YouTube sources. The extension path already
  restricts cookies to youtube.com; the mobile path keeps the same
  restriction.
- Android/iOS account-manager or OS-level credential reuse. The session
  comes from an in-app WebView on youtube.com, not from the OS keychain.

### Out of scope (blocked gate)

Implementation is blocked until #308's desktop/extension children
(#312 API, #313 worker threading, #314 extension forwarding) are verified in
production. This doc is the design handoff only; it must not be marked
`maestro-ready` and no code PR may close #315.

## 2. Reused contract (no server change)

The server already accepts per-job YouTube cookies. The mobile app is just a
new caller of an existing, stable surface.

### 2.1 `POST /jobs` request shape

```json
{
  "url": "https://www.youtube.com/watch?v=…",
  "source": "ios-app" ,
  "youtube_cookies": "<Netscape cookies.txt blob>"
}
```

- `youtube_cookies` is optional. Omitting it is identical to today's
  public-only submit — no behavior change for callers that do not opt in.
- `source` SHOULD identify the platform (`ios-app`, `android-app`) so
  operators can distinguish supply channels in metrics/dedup views, exactly
  as the extension uses `chrome-extension`.
- The blob MUST be a valid Netscape `cookies.txt` (7 tab-separated fields
  per data line; `#`-prefixed lines and blank lines ignored; at least one
  data line). The server validates via `validate_youtube_cookies`
  (`src/scribe/api/schemas.py`) and returns a **value-free** 422 on failure.
- The blob MUST be ≤ `YOUTUBE_COOKIES_MAX_BYTES` (256 KiB). A real
  youtube.com `cookies.txt` is ~8–20 KiB.

### 2.2 Auth gate (unchanged)

`youtube_cookies` requires an **owner-attached** actor
(`src/scribe/api/routes.py`, `POST /jobs`):

- The mobile app MUST authenticate as the human owner. Concretely it sends
  `Authorization: Bearer <extension token>` (the same per-owner token type
  the Chrome extension uses, minted in Scribe Settings). A trusted-LAN or
  machine-bearer actor is rejected with `403
  "youtube_cookies requires owner or extension-token authentication"` when
  cookies are present.
- **LAN exception (#405, does not apply to mobile).** A single-operator LAN
  deployment can opt in with `SCRIBE_LAN_YOUTUBE_COOKIES_ENABLED=true`, after
  which a plain **trusted-LAN** request (per `SCRIBE_TRUSTED_CIDRS` /
  `SCRIBE_TRUSTED_PROXIES`) may attach `youtube_cookies` without a token; the
  job is attributed to the default owner. This does not change the mobile
  contract: a mobile app talks to Scribe over the internet, not from the
  trusted LAN, so it still MUST send the owner extension token. Machine-bearer
  and non-LAN callers remain rejected regardless of the flag.
- The cookie gate fires **only when `youtube_cookies` is present**. A
  mobile app that authenticates as the owner but submits a public video
  without cookies is unaffected.

### 2.3 Server-side lifecycle (unchanged, inherited)

Once validated, the blob flows through the existing path:

1. `cookie_jar.stash(job_id, blob)` after the `Job` row commits
   (`src/scribe/api/cookie_jar.py`) — an in-process, in-memory dict, keyed
   by `job_id`, guarded by a lock.
2. The worker `cookie_jar.take(job_id)` pops it at download time
   (`src/scribe/worker/loop.py`) and writes it to a `0600` temp file for
   `yt-dlp`'s `--cookies` flag (#313).
3. `cookie_jar.discard(job_id)` on failure paths so no blob outlives its
   job. Anything that survives a process restart is intentionally lost; a
   queued-but-not-downloaded gated job fails with the standard public-only
   error, matching operator intent.

The mobile app depends on none of these internals. It only posts the blob.

## 3. WebView cookie-extraction flow

The mobile app hosts a logged-in `WKWebView` (iOS) / `WebView` (Android) on
`https://www.youtube.com`. The user signs in once inside the WebView using
their normal Google account flow. The app reads the resulting session
cookies from the WebView's cookie store and serializes them to the Netscape
format. Serialization MUST reuse the **same algorithm** as the Chrome
extension's `extension/chrome/cookies.js` so the server sees an identical
blob shape.

### 3.1 iOS (WKWebView)

- Store: `WKWebsiteDataStore.default().httpCookieStore`
  (`WKHTTPCookieStore`). For an app-private session that does not leak into
  Safari, use a non-persistent `WKWebsiteDataStore.nonPersistent()`
  instead. **Recommendation: non-persistent by default** so a YouTube
  session never outlives the app, matching the "ephemeral, per-job"
  security model. Persistent storage is opt-in only.
- Read: `httpCookieStore.getAllCookies(_:)` returns `[HTTPCookie]`.
  Async-await wrapper: `await withCheckedContinuation`.
- Filter to `domain` ending in `.youtube.com` / `youtube.com`, matching the
  extension's `chrome.cookies.getAll({ domain: ".youtube.com" })`.
- Map `HTTPCookie` → the 7-field Netscape line. Field mapping is identical
  to `extension/chrome/cookies.js` `formatCookieLine`:

  | Netscape field | HTTPCookie source |
  |---|---|
  | domain | `cookie.domain` (prefix `.` unless `hostOnly`) |
  | include_subdomains | `FALSE` if `hostOnly`, else `TRUE` |
  | path | `cookie.path` ?? `"/"` |
  | secure | `"TRUE"` if `cookie.isSecure` else `"FALSE"` |
  | expiry | `0` if session-only, else `cookie.expiresDate?.timeIntervalSince1970` floored |
  | name | `cookie.name` |
  | value | `cookie.value` |

  `httpOnly` cookies get the `#HttpOnly_` domain prefix, as in the
  extension. Reject any name/value containing tab, CR, or LF (the
  extension's `containsTabOrNewline` guard) so the blob cannot forge extra
  lines.
- Header: prepend the same `# Netscape HTTP Cookie File …` comment block.
- Refresh on every submit — **never cache the blob in app memory, keychain,
  or UserDefaults between submits.** Read fresh from the cookie store each
  time, exactly as the extension reads fresh via `chrome.cookies.getAll`
  on every submit.

### 3.2 Android (WebView)

- Store: `CookieManager.getInstance()`. Call
  `CookieManager.setAcceptCookie(true)` and ensure third-party cookies are
  allowed for the WebView (`CookieManager.setAcceptThirdPartyCookies(webView,
  true)`) so the YouTube/Google SSO flow can set its cross-domain session
  cookies.
- Read: `CookieManager.getCookie(url)` returns the `Cookie:` header string
  for a single URL (`name=value; name=value; …`). Unlike iOS/Chrome there
  is no structured cookie list, so the app parses the header string.
  - `domain` is inferred from the URL host the cookie was read from
    (`https://www.youtube.com` → domain `youtube.com`,
    `include_subdomains=TRUE`). This is a known limitation vs. the
    structured stores: Android's `CookieManager` does not expose per-cookie
    domain/path/secure/expiry/httpOnly. The serialized blob therefore marks
    all cookies as `include_subdomains=TRUE`, `path=/`, `secure=TRUE` (the
    URL is https), `expiry=0` (session). This is sufficient for `yt-dlp`
    replay and matches what `yt-dlp`'s own `--cookies-from-browser`
    produces from Chrome on Android.
  - For richer metadata, an alternative is to evaluate cookies via
    `WebView.evaluateJavascript("document.cookie", …)`. This is **not
    recommended** as the primary path because `document.cookie` omits
    `HttpOnly` cookies (e.g. `SID`, `HSID`, `SSID`), which are exactly the
    session cookies needed to authenticate a gated download. `CookieManager`
    is the right surface.
- Serialize with the shared serializer (see §3.3). Reject tab/CR/LF in
  name/value.
- Refresh on every submit — never cache.

### 3.3 Shared serializer

The Netscape serializer (`serializeCookiesToNetscape`) is already a pure
function in `extension/chrome/cookies.js`. The mobile apps SHOULD vendor a
literal port of that function (Swift + Kotlin), preserving:

- the `# Netscape HTTP Cookie File` header,
- the `#HttpOnly_` prefix rule,
- the host-only domain normalization,
- the tab/CR/LF rejection,
- the session-cookie `expiry=0` rule,
- the "empty input → empty string" rule.

Keeping the serializer byte-identical to the extension means the server's
`validate_youtube_cookies` accepts the mobile blob without any new server
branch. A shared test corpus (see §6) pins the parity.

## 4. Submit contract reuse

The submit flow is a thin wrapper around the existing API:

1. User picks a video URL in the app (share sheet, paste, or a YouTube
   deep-link handler).
2. App runs the **same preflight courtesy** the extension runs
   (`GET /preflight?url=…`, #339). For `single_media=true`, submit
   directly; for containers (playlist/channel/feed/search), show a
   "Submit anyway" confirm. This is a UX parity point, not a server
   requirement.
3. If the URL host matches youtube.com **and** the user has enabled
   per-job cookies in app settings, read cookies fresh from the WebView
   store (§3) and serialize.
4. POST `/jobs` with `Authorization: Bearer <extension token>`,
   `source: "ios-app"`/`"android-app"`, and `youtube_cookies` only when
   (a) the URL is YouTube and (b) cookies were collected. Non-YouTube URLs
   never include `youtube_cookies`, matching the extension.
5. On `403` (owner-auth gate) → prompt the user to mint/paste an extension
   token in Scribe Settings, same as the extension's bearer-token flow.
6. On `422` (size/format) → the server message is value-free; surface it
   verbatim. Do **not** include the blob in any client-side error log or
   crash report.
7. On success → show the receipt with `Open job #N` deep-link to
   `scribeservice://jobs/{job_id}` (or the web URL).

The contract is reused so literally that the only mobile-specific surface is
"where the cookie blob came from." Everything downstream is unchanged.

## 5. Security model

Cookies are bearer-equivalent secrets: possession of a youtube.com session
cookie set is sufficient to act as the user on youtube.com. The mobile path
inherits the server-side guarantees from #312/#313 and adds client-side
guarantees.

### 5.1 Server-side guarantees (inherited, unchanged)

- **Never persisted.** The blob lives only in the in-process `cookie_jar`
  and is `pop`-ed at download time. No DB column, no disk file outside the
  `0600` yt-dlp temp file that is removed after download. Verified by
  `tests/test_routes_dedup.py` (`assert not hasattr(job, "youtube_cookies")`).
- **Never logged.** `validate_youtube_cookies` raises value-free messages;
  the route raises `HTTPException` rather than letting pydantic echo
  `input`. Verified by `tests/test_jobs_youtube_cookies.py`
  (`test_post_jobs_*_does_not_log_value`, `..._omits_input`).
- **Owner-scoped only.** A non-owner actor (trusted-LAN, machine bearer)
  gets `403` when cookies are present, unless the LAN opt-in above is enabled
  for a trusted-LAN caller (#405). The mobile app MUST authenticate as
  the owner via an extension token. Verified by
  `test_post_jobs_cookies_from_non_owner_actor_is_403` and the `#405`
  flag on/off cases in `tests/test_jobs_youtube_cookies.py`.
- **Per-job ephemeral.** `stash`/`take`/`discard` are keyed by `job_id`;
  nothing survives process restart by design.
- **Public-only fallback.** If the user does not enable cookies, or the
  WebView has no youtube.com session, or permission was revoked, the app
  omits `youtube_cookies` and the job runs the standard public download
  path. Gated videos simply fail with the normal public-only error; no
  degraded-auth state is stored.

### 5.2 Client-side guarantees (new, mobile-specific)

- **No persistence by default.** Use a non-persistent
  `WKWebsiteDataStore` (iOS) and do not call
  `CookieManager.setAcceptCookie(true)` for any store shared with the
  system browser. The YouTube session lives only in the in-app WebView for
  the duration the app is open. The user signs in again next launch. This
  is the strongest mapping of the server's "ephemeral, per-job" intent to
  the client.
- **Refresh on every submit; never cache the blob.** The serialized
  `cookies.txt` is built in memory, sent over TLS to `/jobs`, and dropped.
  It is never written to app storage, never written to logs, never
  included in crash/ANR reports. Crash reporters MUST be configured with
  a denylist on the request body and on any field named
  `youtube_cookies`.
- **No logging of cookie names or values.** Only counts/sizes are
  observable, matching the extension's rule. UI surfaces say "cookies
  attached: N" at most, never the names/values.
- **Owner-scoped transport.** The blob is sent only to the user's
  configured Scribe base URL over HTTPS, with the owner's extension token.
  The app MUST NOT let the user point at an `http://` URL when cookies are
  enabled (the extension already requires `http(s)://`; the mobile app
  SHOULD require `https://` whenever cookies are enabled, since cookies
  over plaintext would expose the session).
- **Permission UX mirrors the extension.** A single, explicit
  "Enable YouTube cookies" toggle in app settings. Disabling it clears the
  WebView's cookie store immediately (`WKHTTPCookieStore.delete` /
  `CookieManager.removeAllCookies`) and drops the host permission. There
  is no implicit on-by-default.
- **No cross-app leakage.** The in-app WebView's cookies MUST NOT be
  shared with Safari/Chrome on the device. Use the app-private,
  non-persistent store (iOS) and the app's own `WebView`/`CookieManager`
  instance (Android). Do not use `SFSafariViewController` for the YouTube
  sign-in — its cookies are not readable by the app at all, which would
  silently break extraction; the design uses `WKWebView` precisely so the
  app can read its own store.
- **Token storage.** The extension token is stored in the OS keychain
  (iOS Keychain / Android Keystore), never in plain SharedPreferences /
  UserDefaults. It is the same secret the Chrome extension stores in
  `chrome.storage.sync`; on mobile it gets the stronger keychain
  protection the OS provides.

### 5.3 Threat notes

- **App is compromised / jailbroken device:** out of scope. Any app on a
  compromised device can be made to exfiltrate cookies. The mitigation is
  the same as for any banking-like app: OS integrity, app sandbox, no
  root. This design does not attempt to defend against a malicious host.
- **User pastes the wrong Scribe URL:** the cookies go to that URL.
  Mitigation: the app shows the configured base URL prominently on the
  submit confirm sheet, and requires `https://` when cookies are on.
- **Replay of a captured blob:** the blob is only valid until the YouTube
  session rotates. The server uses it once and discards it. A captured
  blob has the same shelf life as the underlying session cookies, which
  is why "refresh on every submit, never cache" matters: it minimizes the
  window in which a stale blob could be replayed from app memory.

## 6. Test / verification plan (for the future implementation PR)

This issue lands no code, so no tests are added now. The implementation PR
must include:

- **Serializer parity tests.** A shared corpus of `(cookie input →
  Netscape output)` pairs, generated from the Chrome extension's
  `serializeCookiesToNetscape`, and asserted byte-equal against the Swift
  and Kotlin ports. This pins the contract the server validates.
- **No-persistence assertions** (client side): after a submit, the app's
  UserDefaults/SharedPreferences and keychain contain no `youtube_cookies`
  blob; only the extension token is present.
- **No-log assertions** (client side): the app's crash-reporter
  denylist is tested by submitting with a known sentinel cookie value and
  confirming the sentinel never appears in a synthesized crash payload.
- **Server-side tests already exist** and are reused unchanged:
  `tests/test_jobs_youtube_cookies.py` (auth gate, validation, log safety,
  value-free 422) and `tests/test_routes_dedup.py` (blob never persisted).
  The implementation PR must not weaken any of these.

## 7. Open questions (resolved by design, listed for traceability)

1. **iOS persistent vs non-persistent store?** → Non-persistent by
   default; persistent is opt-in. Ephemeral matches the server model.
2. **Android `document.cookie` vs `CookieManager`?** → `CookieManager`,
   because `document.cookie` omits `HttpOnly` session cookies.
3. **New endpoint?** → No. Reuse `POST /jobs` and `GET /preflight`.
4. **New auth?** → No. Reuse the owner extension token the Chrome
   extension already uses.
5. **`source` value?** → `ios-app` / `android-app`, for metric
   distinguishability alongside `chrome-extension`.
6. **Where does the serializer live?** → Vendored as a literal port of
   `extension/chrome/cookies.js` into Swift and Kotlin, kept byte-equal
   via the shared parity corpus. The repo's JS serializer is the source of
   truth.

## 8. Relationship to #308 and verification gate

#315 is a child of #308 (download anti-bot Layer B). The already-merged
siblings are:

- #312 — `POST /jobs` accepts `youtube_cookies` (API contract).
- #313 — worker threads the blob to `yt-dlp` via a `0600` temp file.
- #314 — Chrome extension forwards `.youtube.com` cookies on submit.

This design adds the **fourth supply channel** (native mobile) on top of
the identical contract. Implementation is blocked until the desktop /
extension path (#312/#313/#314) is verified in production, per the issue's
explicit out-of-scope clause. No `maestro-ready`, no code, no auto-close.

Refs #308 #77 #78.