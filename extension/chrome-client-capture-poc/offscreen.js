// offscreen.js — the DE-RISKED half of the extension path (spike #413).
//
// Runs in an offscreen document (a full renderer page) because a service worker
// can't hold a large Blob and idles out after ~30 s (§5). Two jobs:
//   1) download an audio stream in HTTP range chunks (memory-safe), and
//   2) upload the resulting Blob to Scribe's existing POST /jobs/upload (#408).
//
// Both work today given a ready-to-fetch URL. Producing that URL is resolve.js's
// job and is the open risk — this file assumes it is handed one.

// 8 MiB range chunks: small enough to bound memory and to (anecdotally) dodge
// the worst googlevideo throttling on unsolved-`n` URLs, large enough to keep
// request overhead low. Not a substitute for solving `n` (§4).
const RANGE_CHUNK = 8 * 1024 * 1024;

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "capture-and-upload") {
    return undefined;
  }
  captureAndUpload(message.payload)
    .then((result) => sendResponse({ ok: true, result }))
    .catch((error) => sendResponse({ ok: false, message: String(error?.message || error) }));
  return true; // async sendResponse
});

/**
 * @param {{contentUrl:string, mimeType:string, itag:number, filename:string,
 *          baseUrl:string, bearerToken:string, source?:string}} payload
 */
async function captureAndUpload(payload) {
  const blob = await downloadRanged(payload.contentUrl, payload.mimeType);
  return uploadToScribe(blob, payload);
}

// Memory-safe download: sequential Range GETs, appended into one Blob. Host
// permissions for *.googlevideo.com let the offscreen doc read the response body
// cross-origin (a plain web page could not — §6).
async function downloadRanged(url, mimeType) {
  const parts = [];
  let offset = 0;
  let total = null;

  // Probe total length from the first ranged response's Content-Range.
  for (;;) {
    const end = offset + RANGE_CHUNK - 1;
    const resp = await fetch(url, { headers: { Range: `bytes=${offset}-${end}` } });
    if (resp.status === 416) {
      break; // requested past the end -> done
    }
    if (!(resp.status === 206 || resp.status === 200)) {
      throw new Error(`stream fetch failed: HTTP ${resp.status}`);
    }
    const buf = await resp.arrayBuffer();
    if (buf.byteLength === 0) {
      break;
    }
    parts.push(new Uint8Array(buf));
    offset += buf.byteLength;

    if (total === null) {
      total = parseContentRangeTotal(resp.headers.get("Content-Range"));
      // A 200 (no range support) means we already have the whole body.
      if (resp.status === 200) {
        break;
      }
    }
    reportProgress(offset, total);
    if (total !== null && offset >= total) {
      break;
    }
    if (buf.byteLength < RANGE_CHUNK) {
      break; // short read -> last chunk
    }
  }

  if (offset === 0) {
    throw new Error("stream produced 0 bytes");
  }
  return new Blob(parts, { type: mimeType || "application/octet-stream" });
}

function parseContentRangeTotal(header) {
  // "bytes 0-8388607/57600000" -> 57600000
  const match = /\/(\d+)\s*$/.exec(header || "");
  return match ? Number(match[1]) : null;
}

function reportProgress(loaded, total) {
  try {
    chrome.runtime.sendMessage({ type: "capture-progress", loaded, total });
  } catch {
    // Progress is best-effort; never fail the download on a messaging hiccup.
  }
}

// Upload the audio Blob to the EXISTING server endpoint (#408). Single streamed
// multipart POST — sufficient for a ~55 MB audio blob. A resumable protocol is a
// documented fast-follow, not needed for the PoC (§3.3).
async function uploadToScribe(blob, payload) {
  const form = new FormData();
  form.append("file", blob, payload.filename || "audio.m4a");
  form.append("source", payload.source || "client-capture-poc");

  const headers = {};
  if (payload.bearerToken) {
    headers.Authorization = `Bearer ${payload.bearerToken}`;
  }

  const resp = await fetch(`${payload.baseUrl}/jobs/upload`, {
    method: "POST",
    headers, // do NOT set Content-Type; the browser sets the multipart boundary
    body: form,
  });

  const text = await resp.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }
  if (!resp.ok) {
    const detail = body?.detail ? JSON.stringify(body.detail) : `HTTP ${resp.status}`;
    throw new Error(`Scribe upload rejected (${resp.status}): ${detail}`);
  }
  return body || {};
}
