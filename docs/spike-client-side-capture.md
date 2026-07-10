# Spike: client-side media acquisition — de-risking the hosted path

**Issue:** [#413](https://github.com/BeFeast/scribe-service/issues/413) · **Refs:** #408 (upload
groundwork), #307/#309 (server-side PO-token/bgutil plumbing) · **Date:** 2026-07-10 ·
**Status:** research + PoC scaffold. End-to-end validation on real videos is a manual
browser step (see [Verification status](#verification-status)).

## TL;DR / recommendation

| Path | Verdict | One-line reason |
| --- | --- | --- |
| **A. Extension-side fetch** (`*.googlevideo.com` via MV3 host permissions) | ⚠️ **Prototype only** | Plumbing is easy; *acquisition* re-imports the exact PO-token/SABR/`n` arms race we fight server-side today, now inside MV3's stricter (no-remote-code) sandbox. |
| **B. Electron desktop app** (bundled yt-dlp + ffmpeg) | ✅ **Recommended primary** | Inherits yt-dlp's constantly-maintained extractor + independent self-update, on the user's residential IP. De-risks *durability*, which is what kills path A. |
| **C. Plain web app** (direct googlevideo fetch) | ❌ **Abandon** | `*.googlevideo.com` sends no permissive `Access-Control-Allow-Origin`; a page `fetch` is CORS-blocked and `no-cors` yields an opaque, unreadable body. |

**Bottom line.** The genuinely hard problem is unchanged by moving the download to the
client: YouTube's **PO-token/BotGuard attestation** and **SABR** delivery. What the client
*does* change is the two axes that hurt a **datacenter-hosted** downloader most — the
**IP reputation** (residential vs. datacenter) and, for the extension, a **live BotGuard
environment for free**. Those are real wins, but they do not remove the descrambling arms
race; they relocate it. Electron relocates it onto a component (yt-dlp) that is maintained
by someone else and updates independently of our release cadence — so we recommend Electron
as the primary hosted-path bet, and treat the extension direct-fetch as a research prototype
whose one clearly-shippable, provider-independent piece (the **upload leg**) is already
buildable today against the existing `POST /jobs/upload` endpoint.

> **Moving-target caveat.** Everything about PO tokens, SABR, and the yt-dlp client matrix
> is an active cat-and-mouse; the yt-dlp wiki itself flags its own PO-token guidance as
> "subject to change." Treat the client-matrix specifics below as a 2026-07 snapshot, not a
> durable contract.

---

## 1. Where we are today (and why acquisition-on-client is being explored)

Today the Chrome extension submits a **URL** (+ optionally the user's `youtube.com` cookies)
to Scribe; the **server** downloads media with yt-dlp. To survive YouTube's anti-bot walls
from a datacenter IP, the server already carries a real anti-bot stack:

- a **bgutil PO-token sidecar** (`brainicism/bgutil-ytdlp-pot-provider`, LuanRT's BotGuard
  interfacing lib) — see [`docs/runbooks/bgutil-pot-provider.md`](./runbooks/bgutil-pot-provider.md);
- cookie forwarding from the extension (`youtube_cookies`, owner-gated) — see
  [`extension/chrome/README.md`](../extension/chrome/README.md).

This works on a residential LAN but does **not** scale to a hosted offering: server-side
YouTube download from datacenter IPs is an arms race and a ToS liability. The exploration is
to move **acquisition** to the client while keeping **transcription (Whisper on GPU)** and
**summarization** on the server.

### Hard constraints (from Oleg) and how they shape the design

- **No realtime capture tricks** — no `chrome.tabCapture`, no playing the video to record it.
- **No DOM automation on youtube.com** — no simulated clicks / scraping the player UI.

These two constraints matter more than they first appear, because (as §2/§4 show) the
*robust* client-side techniques for getting a full-speed, PO-token-valid stream tend to lean
on exactly the surfaces these rules forbid (reading the live player's computed values, or
consuming the segments the player is streaming). Section 2 calls out precisely where each
candidate technique lands against these lines, because that ruling — not the plumbing — is
the gating decision for path A.

---

## 2. Spike Q1 — Can extension JS resolve stream URLs, and is BotGuard "free" in-browser?

**Short answer:** partially, and fragile. Resolving *metadata* is easy with the user's
session; turning that into a *fast, fetchable, PO-token-valid audio URL* is the hard part,
and it collides with MV3's remote-code rules.

### 2.1 Resolving the innertube player response — feasible

An MV3 extension can call `POST https://www.youtube.com/youtubei/v1/player` (innertube) with
the user's cookies from its **service worker / offscreen document** (which hold the host
permission — see §6). [YouTube.js / youtubei.js](https://github.com/LuanRT/YouTube.js) has a
browser build (`youtubei.js/web`) and takes a custom `fetch`, so it can drive innertube from
the extension. The response contains `streamingData.adaptiveFormats[]` with `itag`,
`mimeType`, `bitrate`, and a media `url` (or a `signatureCipher`). Because we send the
**user's own cookies**, the session/visitor binding is "free" — this is genuinely easier than
the server, which must synthesize a session.

### 2.2 The three things standing between "player response" and "downloaded audio"

1. **Signature cipher** — increasingly *not* present on modern formats, but when a
   `signatureCipher` is returned, its `s` value must be descrambled by a function extracted
   from the player's `base.js`. ([tyrrrz.me — Reverse-Engineering YouTube, revisited](https://tyrrrz.me/blog/reverse-engineering-youtube-revisited))
2. **`n` throttling parameter** — present on **most** googlevideo URLs. If you fetch without
   transforming `n`, YouTube silently throttles you to **~40–70 KB/s**, turning a 1-hour audio
   download into hours. ([0x7d0 — how they bypass throttling](https://blog.0x7d0.dev/history/how-they-bypass-youtube-video-download-throttling/),
   [youtube-dl PR #30184](https://github.com/ytdl-org/youtube-dl/pull/30184)) Crucially, the
   `n` challenge is bound to the **URL, not the session** — a real logged-in tab does **not**
   make throttling go away; the *player JS* transforming `n` is what makes the browser fast.
   Both `n` and `sig` are extracted from the **same `base.js`**, so one player update can
   break both at once. ([yt-dlp — JS challenge solving](https://deepwiki.com/yt-dlp/yt-dlp/3.4.2-javascript-challenge-solving))
3. **PO token (GVS)** — see §3.

Descrambling `n`/`sig` requires **executing YouTube's obfuscated JS**. YouTube.js ships **no
interpreter**; the documented pattern is `Platform.shim.eval = async (d) => new
Function(d.output)()`. ([YouTube.js — getting started](https://ytjs.dev/guide/getting-started))
**`eval`/`new Function` is blocked by MV3's default CSP, and MV3 forbids fetching + running
remote code** — which is exactly what running the live `base.js` is. Workarounds (a sandboxed
`sandbox` page, a bundled WASM JS engine, or an offscreen doc with relaxed CSP, à la FreeTube)
exist but are fragile and brush against Chrome Web Store's remote-code policy. This is the
core reason the *robust* extension path is hard — not CORS, not memory.

### 2.3 Does a real logged-in tab give BotGuard/PO attestation "for free"?

**Essentially yes — this is the real structural advantage of the extension idea — but it is
an inference to prototype-validate, not a vendor-confirmed fact, and it is undercut by SABR
(§3).** Every server-side provider's hardest job is *replicating a compliant BotGuard
runtime*; LuanRT's BgUtils says plainly it "does not bypass BotGuard; you still need a
compliant environment." A real youtube.com tab **is** that environment and already mints a
valid, correctly-bound token on every playback. The documented manual method is literally to
read `serviceIntegrityDimensions.poToken` (or the `pot=` query param) out of a live tab, and
[bgutils-js notes](https://www.npmjs.com/package/bgutils-js/v/1.0.1) that "Electron and other
Chromium-based environments work out of the box with 0 dependencies."

**But:** (a) reading that value out of the page is exactly the "read the live player's state"
surface the **no-DOM-automation constraint** is wary of — this needs an explicit ruling from
Oleg (see §9); and (b) a free *token* is not a free *direct URL* — see SABR next.

---

## 3. Spike Q3 (and the PO-token/SABR reality) — server side

### 3.1 PO token / BotGuard

A **GVS PO token is effectively required for web clients to fetch googlevideo media**, for
both logged-out and logged-in users (login only changes the *binding*, not whether it's
needed); without it you get HTTP 403. Binding: logged-out → **Visitor ID**
(`VISITOR_INFO1_LIVE`/`visitorData`); logged-in → **account Session ID**. Most web GVS tokens
are also **bound to the video id** and short-lived (~12 h). ([yt-dlp — PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide))
BotGuard is an obfuscated **register-based JS VM** that attests a genuine client, per-platform
and non-portable (BotGuard=Web, DroidGuard=Android, iOSGuard=iOS).

### 3.2 SABR — the biggest 2025–26 obstacle

Independent of the token, YouTube is rolling out **SABR (Server-Adaptive Bitrate)**: format
selection moves server-side, media is delivered as **UMP binary parts**, and the plain HTTPS
`videoplayback` URLs are **stripped from the web player response**. yt-dlp warns "web client
https formats are skipped… missing a URL because YouTube is forcing SABR streaming"
([#12482](https://github.com/yt-dlp/yt-dlp/issues/12482)), reportedly even with Premium
cookies + a token provider ([#14390](https://github.com/yt-dlp/yt-dlp/issues/14390)); yt-dlp
built a native SABR downloader on LuanRT's `googlevideo`/`SabrStream`
([PR #13515](https://github.com/yt-dlp/yt-dlp/pull/13515)).

**Design implication for path A:** the durable extraction may need to consume **SABR/UMP
segments** (again: the segments the tab is *playing* — constraint-sensitive) or use the
`web_safari` client, which is served **HLS (m3u8)** that "does not require a PO Token for GVS
at this time." That HLS path plus live streams (§7) are the comparative soft spots.

### 3.3 Server upload endpoint — **already exists** (#408)

The "minimal `POST /api/jobs` variant accepting an uploaded audio blob" the spike asks about
**is already implemented** as **`POST /jobs/upload`** (issue #408): see
[`src/scribe/api/routes.py`](../src/scribe/api/routes.py) (`create_upload_job`) and
[`src/scribe/pipeline/uploads.py`](../src/scribe/pipeline/uploads.py). It already does what a
client-acquisition path needs:

- **Streams the body to disk in 1 MiB chunks** — never buffers the payload in memory
  (`uploads.CHUNK_SIZE`), enforcing `SCRIBE_UPLOAD_MAX_BYTES` (default 4 GiB) mid-stream (413
  on overflow).
- **Validates with `ffprobe`** before the file enters the pipeline (422 on non-media).
- **Deduplicates by content SHA-256** (`video_id = upload:<sha16>`).
- **Enqueues through the identical Whisper/summary chain** as URL jobs; the worker then
  transcodes a downscaled archival copy to R2 and deletes the original.
- Same `require_actor` auth as `POST /jobs`; 503 when R2 media storage is unconfigured.

**What is missing for a client-acquisition path is small and additive, not a rewrite:**

- **Auth from the extension**: the extension already mints/holds a bearer token
  (`chrome.storage.local`), so `POST /jobs/upload` works from the extension today with no
  server change.
- **Resumable/chunked *upload* protocol**: `POST /jobs/upload` is a **single streamed
  request**. For a flaky-network 50–200 MB upload, a resumable protocol (e.g. `tus`, or a
  simple `Content-Range` PUT-to-parts + finalize) would be a nice-to-have — **not required
  for the PoC**, because a single streamed multipart upload of a ~55 MB audio blob is
  well within reach. Recommended as a fast-follow only if real-world upload failure rates
  justify it.
- **R2 must be configured** for the endpoint to be enabled (it 503s otherwise). This is an
  operator step, not code.

Net: **spike Q3 is effectively done.** The client side does not need a new server endpoint to
prove the pipeline; it needs to *produce an audio blob* and POST it to `/jobs/upload`.

---

## 4. Spike Q4 — failure modes

| Mode | Client-side behaviour |
| --- | --- |
| **`n` throttling** | Fetching a googlevideo URL without transforming `n` → **~40–70 KB/s** silent throttle. Session/cookies do **not** help (URL-bound). Requires running player JS (§2.2) or a range-chunk trick (`<~10 MB` GETs sometimes serve full-speed unsolved — real but undocumented and fragile; do not build on it). |
| **PO token 403 / SABR** | Missing/expired GVS token → 403. SABR strips direct URLs for the `web` client (§3.2). `web_safari` HLS is the current soft spot. |
| **Age-gated** | Not bypassable cryptographically; needs the **user's own logged-in, age-verified cookies**. Client-side is the *natural* fit (session is right there). Cookies can be revoked by Google at any time. ([yt-dlp #13013](https://github.com/yt-dlp/yt-dlp/issues/13013)) |
| **Members-only** | Works with the **member's own session cookies**, if the account truly has the membership. Genuinely *private* (invite) videos remain inaccessible even with cookies. |
| **Live / premieres** | HLS/DASH manifests; an *ongoing* live can't finish until the stream ends (`--live-from-start` semantics), whereas a *completed VOD of a past stream* behaves normally. Per the PO-token wiki, **HLS live streams don't require a PO token** (except `ios`) — a soft spot for live audio. |
| **googlevideo throttling from extension context** | Same URL-bound `n` throttle as anywhere; the extension context confers no special exemption. |

---

## 5. Spike Q2 — MV3 mechanics (this part is solved and low-risk)

- **Where the fetch lives.** The **service worker** *can* `fetch` cross-origin (host
  permissions bypass page CORS — §6), but it **terminates after ~30 s idle** and is killed if
  a single `fetch` takes >30 s or an event runs >5 min. So a 50–200 MB download must live in
  an **offscreen document** (`chrome.offscreen`, Chrome 109+, `reasons: ["BLOBS"]`), which is
  a full renderer page that survives the SW lifecycle and is the standard home for
  `Blob`/`URL.createObjectURL` work. ([offscreen API](https://developer.chrome.com/docs/extensions/reference/api/offscreen),
  [SW lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle))
- **Memory for ~50–200 MB audio.** No officially documented Blob ceiling, but an offscreen
  doc has normal page memory characteristics and Chromium's Blob storage **spills large blobs
  to disk**, so 50–200 MB is comfortably feasible. ([Chromium Blob storage](https://chromium.googlesource.com/chromium/src/+/HEAD/storage/browser/blob/README.md))
  The real risk is holding **multiple copies** at once (Blob + ArrayBuffer + base64) and not
  calling `URL.revokeObjectURL()`. Mitigation: fetch in **HTTP range chunks**, append to a
  growing `Blob`/`Uint8Array`, and stream that up — never base64.
- **Chunked/resumable upload.** For the PoC, a **single streamed multipart POST** to
  `/jobs/upload` is sufficient for a ~55 MB audio blob. A resumable protocol is a
  documented fast-follow (§3.3), not a blocker.

The PoC scaffold in [`extension/chrome-client-capture-poc/`](../extension/chrome-client-capture-poc/)
implements this topology: **service worker orchestrates → offscreen document fetches (ranged)
→ chunked upload to `/jobs/upload`**, with the *stream-URL resolution* deliberately isolated
behind a documented seam (`resolve.js`) because that is the fragile, unsolved part — not the
plumbing.

---

## 6. Spike Q3/Q6 — CORS: why extension works and a plain web app does not

- **`*.googlevideo.com` sends no permissive `Access-Control-Allow-Origin`.** A plain web page
  `fetch` is **CORS-blocked**; a `no-cors` fetch returns an **opaque, unreadable** body — you
  can't extract the audio bytes. The only plain-web escape is a proxy you control (backend
  HTTP isn't subject to CORS), which just moves the download back to your datacenter — i.e.
  defeats the purpose. **→ Path C (plain web app): abandon.**
- **MV3 `host_permissions` genuinely bypass CORS for declared origins** ("a script executing
  in an extension service worker or foreground tab can talk to remote servers outside of its
  origin, as long as the extension requests host permissions"). ([Chrome — network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests))
  **Catch:** **content scripts do *not* get this** — they run in the page's origin under SOP.
  So the privileged googlevideo fetch must happen in the **service worker or offscreen
  document**, and a content script (if used for page harvest) must **message** the value
  out. The PoC follows exactly this pattern.

*Honesty flag:* googlevideo's CORS behaviour is confirmed observationally (consistent
developer reports + the CORS mechanism), not by an official Google CORS policy, and can vary
by endpoint/params.

---

## 7. Spike Q5 — Electron path estimate (the recommended primary bet)

- **Bundling effort: low-to-moderate, well-trodden.** Ship `yt-dlp` + `ffmpeg`/`ffprobe` as
  static binaries via electron-builder `extraResources`, invoke as child processes. Prebuilt
  helpers exist ([`ffmpeg-static`](https://www.npmjs.com/package/ffmpeg-static),
  [`yt-dlp-wrap`](https://www.npmjs.com/package/yt-dlp-wrap), combined static bundles). **Main
  gotcha: `asarUnpack`** — binaries inside `app.asar` aren't executable; unpack them. Plus
  per-OS binaries and macOS notarization/code-signing of the bundled executables.
- **Auto-update: two independent layers.** App shell → **`electron-updater`** (standard); the
  yt-dlp binary → its own **`yt-dlp -U` self-update**, so the fast-moving extractor refreshes
  **without shipping a whole app release**. This is the decisive durability advantage: the
  arms race is fought by yt-dlp's maintainers on their cadence, not ours. Recent yt-dlp also
  increasingly needs a **JS runtime (Deno)** for n-sig/SABR — factor that into the bundle.
- **Residential-IP advantage: real and documented.** YouTube flags datacenter IPs
  (AWS/GCP/Oracle) aggressively — reportedly after far fewer downloads than from residential
  IPs. Running yt-dlp on the **user's machine** removes the IP-reputation axis that most hurts
  a server, while inheriting yt-dlp's maintained PO-token/SABR handling. ([yt-dlp #12264](https://github.com/yt-dlp/yt-dlp/issues/12264))
- **Rough effort:** a focused MVP (submit box → yt-dlp audio-only extract → upload to
  `/jobs/upload` → poll status) is on the order of **1–2 weeks** for a first internal build;
  the tail cost is **packaging/signing/auto-update per-OS**, not the download logic. It does
  **not** exempt us from PO-token/SABR/`n`, but it delegates them to a maintained component.

---

## 8. Quality vs. the yt-dlp baseline

- **Same source formats.** Both the extension and Electron paths target the same audio-only
  DASH formats yt-dlp uses — **itag 140** (`audio/mp4` AAC-LC ~128 kbps) or **itag 251**
  (`audio/webm` Opus, VBR ≤160 kbps, real captures often ~128–140 kbps). For a 1-hour video
  that's **~55–58 MB** (up to ~72 MB for 251 at ceiling). Whisper transcription quality off
  these is **indistinguishable from the current server path** — we already feed yt-dlp's
  audio-only output to Whisper; the bytes are the same.
- **Electron == yt-dlp baseline** by construction (it *is* yt-dlp).
- **Extension direct-fetch** can *match* quality **when it successfully resolves a full-speed,
  token-valid audio URL**, but its **reliability** is below the yt-dlp baseline because it
  must re-implement (and keep re-implementing) `n`/`sig`/PO/SABR handling inside MV3's
  sandbox. Quality-per-success is fine; **success rate over time** is the risk.

---

## 9. Open questions for Oleg (constraint rulings that gate path A)

The plumbing is decided; these product/constraint calls are not, and they determine whether
path A is even attemptable:

1. **Page-value harvest vs. "no DOM automation."** The most robust extension technique is a
   content script on a youtube.com tab reading the player's already-computed `poToken` /
   deciphered URLs / SABR segments. Is *reading the live player's network/state* (no clicks,
   no UI scraping) inside or outside the constraint? This is the single biggest gate.
2. **Consuming SABR/UMP segments** the player streams — is tapping the segments the tab is
   already fetching acceptable, or too close to "capture"? (It is network-level, not
   `tabCapture`, but it is adjacent to the "don't play it to record it" line.)
3. **Web Store policy appetite.** A robust in-extension descrambler needs
   sandboxed/relaxed-CSP JS execution that flirts with the remote-code policy. Acceptable for
   an internal/unlisted extension; risky for a public listing.

---

## 10. Verification status

- ✅ **Server upload path** exists and is unit-tested ([`tests/test_jobs_upload.py`](../tests/test_jobs_upload.py),
  [`tests/test_uploads_staging.py`](../tests/test_uploads_staging.py)) — 503/413/422/auth
  gating and streaming staging.
- ✅ **PoC extension static contract** is guarded by
  [`tests/test_client_capture_poc.py`](../tests/test_client_capture_poc.py) (manifest shape,
  offscreen + googlevideo host permissions, upload target, SW↔offscreen topology).
- ⚠️ **End-to-end on 3 real videos (incl. one >1 h)** — **not performed by this automated
  spike run.** It requires a signed-in Chrome profile driving real YouTube playback and a
  live Scribe with R2 configured; an autonomous agent cannot exercise a browser extension
  against a logged-in YouTube session. The scaffold is structured so a human can load it
  unpacked and run that validation; the resolution seam (`resolve.js`) is where the
  hypothesis of §2.3 must be proven or disproven on real videos. **This is the crux
  experiment and is explicitly left as a manual follow-up.**

## 11. Recommended next steps

1. **Adopt Electron (path B) as the hosted-path primary.** Time-box a 1–2 week internal MVP:
   submit → yt-dlp audio-only → `POST /jobs/upload` → poll. Reuse the existing endpoint as-is.
2. **Keep the extension as the low-friction submit UI** it already is; do **not** ship the
   direct-fetch acquisition into the production extension yet.
3. **Get the §9 constraint rulings**, then run the manual §10 crux experiment with the PoC
   scaffold to prove/disprove the "BotGuard-for-free" hypothesis on 3 real videos.
4. **Only if the crux experiment succeeds and the constraints allow it**, consider a resumable
   upload protocol on `/jobs/upload` and productionizing the extension acquisition as a
   lighter-weight alternative to the Electron install.

## Sources

- yt-dlp — [PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide) ·
  [SABR #12482](https://github.com/yt-dlp/yt-dlp/issues/12482) ·
  [SABR native downloader PR #13515](https://github.com/yt-dlp/yt-dlp/pull/13515) ·
  [JS challenge solving](https://deepwiki.com/yt-dlp/yt-dlp/3.4.2-javascript-challenge-solving) ·
  [datacenter-IP flagging #12264](https://github.com/yt-dlp/yt-dlp/issues/12264)
- YouTube.js — [getting started](https://ytjs.dev/guide/getting-started) ·
  [browser usage](https://ytjs.dev/guide/browser-usage) · [Player API](https://ytjs.dev/api/classes/Player) ·
  [LuanRT/BgUtils](https://github.com/LuanRT/BgUtils) · [bgutils-js](https://www.npmjs.com/package/bgutils-js/v/1.0.1)
- Chrome — [offscreen API](https://developer.chrome.com/docs/extensions/reference/api/offscreen) ·
  [service worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle) ·
  [network requests / CORS](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)
- Throttling — [0x7d0 blog](https://blog.0x7d0.dev/history/how-they-bypass-youtube-video-download-throttling/) ·
  [youtube-dl PR #30184](https://github.com/ytdl-org/youtube-dl/pull/30184)
- Reverse engineering — [tyrrrz.me, revisited](https://tyrrrz.me/blog/reverse-engineering-youtube-revisited)
- itags — [AgentOak gist](https://gist.github.com/AgentOak/34d47c65b1d28829bb17c24c04a0096f) ·
  [pytubefix streams](https://pytubefix.readthedocs.io/en/latest/user/streams.html)
- Electron — [ffmpeg-static](https://www.npmjs.com/package/ffmpeg-static) ·
  [yt-dlp-wrap](https://www.npmjs.com/package/yt-dlp-wrap)
</invoke>
