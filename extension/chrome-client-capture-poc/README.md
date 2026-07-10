# Scribe client-side capture — spike PoC (#413)

> **This is a de-risking spike artifact, not a shipping extension.** It is
> intentionally separate from the production extension in `../chrome/` so it
> cannot affect that extension's manifest, permissions, or tests. Do not load
> both under the same profile expecting them to interoperate. Read
> [`docs/spike-client-side-capture.md`](../../docs/spike-client-side-capture.md)
> first — it has the full findings and the recommendation.

## What this demonstrates

The **de-risked half** of the "acquire audio in the extension" idea, end to end:

1. **Offscreen document** holds the download so it survives the service worker's
   ~30 s idle timeout and can hold a ~50–200 MB audio `Blob` (`offscreen.js`).
2. **Memory-safe ranged download** — sequential HTTP `Range` GETs appended into
   one `Blob`, reading `*.googlevideo.com` cross-origin (the MV3 `host_permissions`
   CORS bypass a plain web page can't get).
3. **Chunked/streamed upload** to Scribe's **existing** `POST /jobs/upload`
   endpoint (#408) as multipart — same pipeline as any other job.

## What this does NOT solve (the actual risk)

**Stream-URL resolution** (`resolve.js`) — turning a watch URL into a
full-speed, PO-token-valid audio URL. That requires solving YouTube's
`n`-throttling / signature descramble (which needs running the player's
obfuscated JS — blocked by MV3's no-remote-code rule) and/or SABR/PO-token
attestation. `resolve.js` is a **deliberately unimplemented seam** that throws
loudly rather than pretending resolution works. See §2/§3/§9 of the findings doc.

## Verification status

- The download + upload plumbing is real and structurally guarded by
  [`tests/test_client_capture_poc.py`](../../tests/test_client_capture_poc.py).
- **End-to-end on real videos is a manual step** — an autonomous agent can't
  drive a signed-in Chrome against real YouTube. To run the crux experiment
  yourself, see below.

## Try it (manual)

1. `chrome://extensions` → Developer mode → **Load unpacked** → select this
   folder (`extension/chrome-client-capture-poc`).
2. Point it at a Scribe with R2 configured (so `/jobs/upload` is enabled) and
   store a bearer token:
   ```js
   // In the extension's service-worker devtools console:
   chrome.storage.sync.set({ baseUrl: "https://your-scribe" });
   chrome.storage.local.set({ bearerToken: "<extension token from Scribe Settings>" });
   ```
3. **To test the plumbing only** (skip the unsolved resolution): capture a
   ready-to-fetch googlevideo audio URL by hand from your browser's Network
   panel while a video plays, and paste it into `MANUAL_STREAM_URL` in
   `resolve.js`. Reload the extension.
4. Open a YouTube **watch** page, click the toolbar button → **Capture & upload
   active tab**. On success the popup shows `Job #N`.
5. **To run the real crux experiment**, implement `resolveAudioStream` per
   strategy A or B in `resolve.js` (after getting the §9 constraint rulings) and
   run it against 3 real videos including one >1 h.

## Files

| File | Role |
| --- | --- |
| `manifest.json` | MV3 manifest: `activeTab` (read the watch tab's URL) + `offscreen` permissions, googlevideo + youtube + scribe host permissions. |
| `service_worker.js` | Orchestrates resolve → offscreen → upload; never holds bytes. |
| `resolve.js` | **Open-risk seam.** Stream-URL resolution (unimplemented). |
| `offscreen.html` / `offscreen.js` | Ranged download + multipart upload to `/jobs/upload`. |
| `popup.html` / `popup.js` / `popup.css` | Minimal trigger UI + progress. |
