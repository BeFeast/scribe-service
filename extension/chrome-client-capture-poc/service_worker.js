// service_worker.js — orchestration for the client-capture PoC (spike #413).
//
// Flow: popup asks to capture the active tab -> resolve an audio-stream URL
// (resolve.js, the open-risk seam) -> ensure the offscreen document exists ->
// hand it the URL + Scribe config -> offscreen downloads (ranged) and uploads to
// POST /jobs/upload -> relay the job result back to the popup.
//
// The SW never holds the audio Blob (it would idle out / can't hold it — §5);
// all byte-handling lives in the offscreen doc.

try {
  importScripts("resolve.js");
} catch (_err) {
  // Cold-start test contexts may not expose importScripts.
}

const DEFAULT_BASE_URL = "https://scribe.oklabs.uk";
const OFFSCREEN_URL = "offscreen.html";

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "capture-active-tab") {
    captureActiveTab()
      .then(sendResponse, (error) =>
        sendResponse({ ok: false, message: String(error?.message || error) }),
      );
    return true;
  }
  // "capture-progress" is emitted by the offscreen doc; the popup listens for it
  // directly, so the SW just ignores it here.
  return undefined;
});

async function captureActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const watchUrl = tab?.url || "";
  if (!/^https:\/\/(www\.)?youtube\.com\/watch\?/.test(watchUrl) && !/^https:\/\/youtu\.be\//.test(watchUrl)) {
    return { ok: false, message: "Open a YouTube watch page first." };
  }

  const config = await getConfig();

  // The open-risk step: resolve a full-speed, token-valid audio URL. Throws
  // until a resolution strategy is implemented and validated (see resolve.js).
  const resolver = (typeof globalThis !== "undefined" ? globalThis : self).scribeResolve;
  const stream = await resolver.resolveAudioStream(watchUrl);

  await ensureOffscreen();

  const filename = `${videoIdOf(watchUrl) || "audio"}.${extForMime(stream.mimeType)}`;
  const response = await chrome.runtime.sendMessage({
    type: "capture-and-upload",
    payload: {
      contentUrl: stream.contentUrl,
      mimeType: stream.mimeType,
      itag: stream.itag,
      filename,
      baseUrl: config.baseUrl,
      bearerToken: config.bearerToken,
      source: "client-capture-poc",
    },
  });

  if (!response?.ok) {
    return { ok: false, message: response?.message || "capture failed" };
  }
  return { ok: true, job: response.result };
}

// One offscreen document per profile: create it only if it isn't already open.
async function ensureOffscreen() {
  const existing = await chrome.runtime.getContexts({
    contextTypes: ["OFFSCREEN_DOCUMENT"],
    documentUrls: [chrome.runtime.getURL(OFFSCREEN_URL)],
  });
  if (existing.length > 0) {
    return;
  }
  await chrome.offscreen.createDocument({
    url: OFFSCREEN_URL,
    reasons: ["BLOBS"],
    justification: "Hold and upload a large downloaded audio Blob (spike #413).",
  });
}

async function getConfig() {
  const [sync, local] = await Promise.all([
    chrome.storage.sync.get({ baseUrl: DEFAULT_BASE_URL }),
    chrome.storage.local.get({ bearerToken: "" }),
  ]);
  return {
    baseUrl: String(sync.baseUrl || DEFAULT_BASE_URL).trim().replace(/\/+$/, ""),
    bearerToken: String(local.bearerToken || "").trim(),
  };
}

function videoIdOf(url) {
  try {
    const u = new URL(url);
    if (u.hostname === "youtu.be") {
      return u.pathname.slice(1);
    }
    return u.searchParams.get("v") || "";
  } catch {
    return "";
  }
}

function extForMime(mimeType) {
  if (/webm/.test(mimeType || "")) {
    return "webm";
  }
  return "m4a";
}
