// resolve.js — the STREAM-URL RESOLUTION SEAM (spike #413).
//
// This is the fragile, UNSOLVED part of the extension direct-fetch path. Read
// docs/spike-client-side-capture.md §2 before touching it. Everything else in
// this PoC (offscreen ranged fetch, chunked upload to /jobs/upload) is
// de-risked and works given a ready-to-fetch audio URL; *producing* that URL is
// the open research question.
//
// Contract: given a YouTube watch URL, return a descriptor the offscreen doc can
// fetch directly:
//   { itag, mimeType, contentUrl, contentLength }  // contentLength optional
// contentUrl MUST be full-speed-ready: the `n` throttling parameter already
// transformed and any GVS PO token already present. If it is not, the download
// silently throttles to ~40-70 KB/s (see §4).
//
// Two candidate strategies, NEITHER shipped here as working — each has an open
// constraint or technical blocker that only manual validation on real videos
// can resolve:
//
//   Strategy A — innertube + in-extension descramble
//     Call POST https://www.youtube.com/youtubei/v1/player with the user's
//     cookies (works from the SW/offscreen — host permissions bypass CORS),
//     read streamingData.adaptiveFormats, pick itag 140 (m4a/AAC) or 251
//     (webm/Opus), then transform `n` (and decipher `sig` if present) using the
//     player base.js. BLOCKER: descrambling requires executing YouTube's
//     obfuscated JS; MV3 forbids remote-code eval, so this needs a
//     sandboxed/relaxed-CSP execution context (fragile, Web-Store-policy risk),
//     and SABR increasingly strips the direct URL entirely (§3.2).
//
//   Strategy B — harvest from a live youtube.com tab (content script)
//     A content script on an already-open watch page reads the player's
//     already-computed poToken / deciphered URLs / SABR segments and messages
//     them to the SW. This is where the "BotGuard for free" advantage is real
//     (§2.3) — but it brushes the "no DOM automation / no scraping the player
//     UI" hard constraint and needs Oleg's ruling (§9) before it can be built.
//
// Until one of those is chosen and validated, this seam throws so the PoC fails
// loudly rather than silently pretending resolution is solved. For local plumbing
// tests of the download+upload legs, MANUAL_STREAM_URL below lets a human paste a
// ready-to-fetch URL captured by hand.

// Set this (by hand, for a single local test) to a full-speed-ready googlevideo
// audio URL you captured from your own browser's network panel. Leave empty for
// normal operation.
const MANUAL_STREAM_URL = "";

/**
 * Resolve a fetchable audio-stream descriptor for a YouTube watch URL.
 * @param {string} _watchUrl
 * @returns {Promise<{itag:number, mimeType:string, contentUrl:string, contentLength?:number}>}
 */
async function resolveAudioStream(_watchUrl) {
  if (MANUAL_STREAM_URL) {
    return {
      itag: 140,
      mimeType: "audio/mp4",
      contentUrl: MANUAL_STREAM_URL,
    };
  }
  throw new Error(
    "resolveAudioStream is an unimplemented spike seam — see resolve.js header " +
      "and docs/spike-client-side-capture.md §2/§9. Pick strategy A or B and " +
      "validate on real videos, or set MANUAL_STREAM_URL for a local plumbing test.",
  );
}

// Exposed for both the service worker (importScripts) and the offscreen doc
// (module-less classic script): attach to globalThis.
(typeof globalThis !== "undefined" ? globalThis : self).scribeResolve = {
  resolveAudioStream,
};
