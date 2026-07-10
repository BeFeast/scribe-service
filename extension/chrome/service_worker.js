try {
  importScripts("cookies.js");
  importScripts("preflight.js");
} catch (_err) {
  // Service worker cold-start in test contexts may not expose importScripts.
}

const DEFAULT_BASE_URL = "https://scribe.oklabs.uk";
const SOURCE = "chrome-extension";
const NOTIFICATION_LINKS_KEY = "notificationLinks";
// #406: maps a cookie-gate failure notification id -> the URL to resubmit
// without youtube_cookies when its "Retry without cookies" button is clicked.
const RETRY_URLS_KEY = "cookieRetryUrls";
const NOTIFICATION_ICON = "icons/scribe-128.png";
const CLEAR_BADGE_ALARM = "clear-scribe-badge";

const YOUTUBE_HOST = /(^|\.)youtube\.com$|^youtu\.be$/i;
const YOUTUBE_COOKIE_ORIGIN = "https://*.youtube.com/*";

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "submit-page",
      title: "Submit this video page to Scribe",
      contexts: ["page"],
    });

    chrome.contextMenus.create({
      id: "submit-link",
      title: "Submit video link to Scribe",
      contexts: ["link"],
    });

    // #378: a dedicated "open the web app" entry on the toolbar action's
    // context menu, so an operator can jump straight to the Scribe queue
    // without typing the base URL. Uses `action` context only — it never
    // clutters page/link menus.
    chrome.contextMenus.create({
      id: "open-scribe",
      title: "Open Scribe queue",
      contexts: ["action"],
    });
  });
});

// The toolbar click opens popup.html (manifest `default_popup`), which asks
// this worker to preflight + submit the active tab — the popup IS the receipt
// (#339). The action's onClicked event does not fire when an action has a
// popup, so the blind one-click submit is gone: a single-media URL submits,
// everything else surfaces a confirm step in the popup before any job is minted.
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type === "submit-active-tab") {
    // `force` is the popup's Submit-anyway button (#339): the user already saw
    // the preflight confirm state, so skip the check and submit. `noCookies` is
    // the popup's Retry-without-cookies button (#406): resubmit the same tab
    // dropping youtube_cookies after the owner-gate rejected them.
    submitActiveTab({
      force: Boolean(message.force),
      noCookies: Boolean(message.noCookies),
    }).then(sendResponse, (error) =>
      sendResponse({ ok: false, message: String(error?.message || error) }),
    );
    return true;
  }
  return undefined;
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  // #378: "Open Scribe queue" jumps straight to the configured service in a
  // new tab. Early branch — it never touches the submit/preflight path and
  // does not need a tab URL.
  if (info.menuItemId === "open-scribe") {
    const config = await getConfig();
    await chrome.tabs.create({ url: config.baseUrl });
    return;
  }

  const url = info.linkUrl || info.pageUrl || tab?.url || "";

  let config;
  try {
    config = await getConfig();
  } catch (error) {
    await notifyFailure(error.message || String(error));
    return;
  }
  const baseHost = baseHostOf(config.baseUrl);
  const helpers = preflightHelpers();

  // Local hard-refusal first (no server call): non-http(s) schemes and Scribe's
  // own pages can never be jobs.
  if (helpers.classifySubmit(url, baseHost, null) === "refuse") {
    await notifyFailure("Use this menu on an http(s) video page or link — Scribe's own pages can't be jobs.");
    return;
  }

  // Gate the context menu through preflight too (#339): only single-media
  // auto-submits. Containers/generic/unsupported get a guidance notification —
  // never a silent job. When preflight is unavailable the explicit right-click
  // still submits; we never hard-block a deliberate submit on infrastructure.
  const preflightResult = await helpers.fetchPreflight(config.baseUrl, url, {
    headers: authHeaders(config),
  });
  if (preflightResult !== null) {
    await recordAuthenticatedAt();
  }
  const verdict = helpers.classifySubmit(url, baseHost, preflightResult);
  if (verdict !== "submit" && preflightResult !== null) {
    await notifyFailure(
      `Not submitted — ${helpers.verdictMessage(preflightResult)} Open the page and use the toolbar to confirm.`,
    );
    return;
  }

  await submitToScribe(url);
});

chrome.notifications.onClicked.addListener(async (notificationId) => {
  const links = await getNotificationLinks();
  const url = links[notificationId];
  if (!url) {
    return;
  }

  await chrome.tabs.create({ url });
  delete links[notificationId];
  await chrome.storage.local.set({ [NOTIFICATION_LINKS_KEY]: links });
  chrome.notifications.clear(notificationId);
});

// #406: the one-click "Retry without cookies" button on a cookie owner-gate
// failure notification (context-menu submit path). The stored URL is resubmitted
// with youtube_cookies omitted — the public download path still works for
// non-gated videos, and LAN mode cannot mint the extension token the gate wants.
chrome.notifications.onButtonClicked.addListener(async (notificationId, buttonIndex) => {
  const retryUrls = await getRetryUrls();
  const url = retryUrls[notificationId];
  if (!url) {
    return;
  }

  delete retryUrls[notificationId];
  await chrome.storage.local.set({ [RETRY_URLS_KEY]: retryUrls });
  chrome.notifications.clear(notificationId);
  if (buttonIndex === 0) {
    await submitToScribe(url, { includeCookies: false });
  }
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === CLEAR_BADGE_ALARM) {
    chrome.action.setBadgeText({ text: "" });
  }
});

async function submitToScribe(url, { includeCookies = true } = {}) {
  setBadge("...", "#5b6472");

  try {
    const config = await getConfig();
    await ensureHostPermission(config.baseUrl);
    const result = await createJob(config, url, { includeCookies });
    await notifySuccess(config.baseUrl, result);
    setBadge("OK", "#137333");
  } catch (error) {
    // #406: a cookie owner-gate rejection gets a retry-without-cookies button,
    // but only on the first (cookie-bearing) attempt — a retry that still fails
    // falls through to the generic failure notice and cannot loop.
    if (error?.cookieGate && includeCookies) {
      await notifyFailure(error.message || String(error), { retryUrl: url });
    } else {
      await notifyFailure(error.message || String(error));
    }
    setBadge("ERR", "#b3261e");
  }
}

// Preflight gate helpers, loaded via importScripts("preflight.js"). Read from
// the global at call time so the worker degrades gracefully if the import was
// skipped (cold-start test contexts).
function preflightHelpers() {
  return (typeof globalThis !== "undefined" ? globalThis : self).scribePreflight;
}

function authHeaders(config) {
  return config.bearerToken ? { Authorization: `Bearer ${config.bearerToken}` } : {};
}

// Record the last time Scribe accepted our credentials (2xx). Surfaced on the
// options page so an operator can see when the saved token last worked. The
// timestamp is device-local and never synced.
async function recordAuthenticatedAt() {
  try {
    await chrome.storage.local.set({ lastAuthenticatedAt: new Date().toISOString() });
  } catch {
    // Storage failures must not break the submit flow.
  }
}

// The toolbar popup's submit flow (#339): preflight the active tab and only
// auto-submit a single-media URL. Containers/generic/unsupported return a
// confirm/refuse verdict the popup renders before any job is minted; `force`
// (the popup's Submit-anyway button) skips straight to submit. Returns a plain
// object the popup renders — no notification (the popup IS the receipt).
async function submitActiveTab({ force = false, noCookies = false } = {}) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  const url = tab?.url || "";
  const tabTitle = tab?.title || "";

  let config;
  try {
    config = await getConfig();
  } catch (error) {
    return { ok: false, message: error.message || String(error) };
  }
  const baseHost = baseHostOf(config.baseUrl);
  const helpers = preflightHelpers();

  // Local hard-refusals (no server call): non-http(s) schemes and Scribe's own
  // pages. classifySubmit returns "refuse" for these even with a null verdict.
  if (helpers.classifySubmit(url, baseHost, null) === "refuse") {
    return {
      ok: false,
      message:
        "Open an http(s) video page before submitting — Scribe's own pages and chrome:// URLs can't be jobs.",
    };
  }

  // Surface a missing host grant before the preflight: its fetch would only
  // fail silently into the confirm state, hiding the actionable settings fix.
  try {
    await ensureHostPermission(config.baseUrl);
  } catch (error) {
    return { ok: false, message: error.message || String(error) };
  }

  let extractor = null;
  // A retry-without-cookies (#406) skips preflight: the operator already
  // confirmed this submit, we are only dropping the cookie attachment.
  if (!force && !noCookies) {
    const preflightResult = await helpers.fetchPreflight(config.baseUrl, url, {
      headers: authHeaders(config),
    });
    if (preflightResult !== null) {
      await recordAuthenticatedAt();
    }
    const verdict = helpers.classifySubmit(url, baseHost, preflightResult);
    if (verdict === "confirm") {
      return { ok: false, confirm: true, message: helpers.verdictMessage(preflightResult) };
    }
    if (verdict === "refuse") {
      return { ok: false, message: helpers.verdictMessage(preflightResult) };
    }
    extractor = preflightResult?.extractor ?? null;
  }

  setBadge("...", "#5b6472");
  try {
    const result = await createJob(config, url, { includeCookies: !noCookies });
    if (!result.job_id) {
      throw new Error("Scribe responded OK but returned no job ID.");
    }
    setBadge("OK", "#137333");
    return {
      ok: true,
      jobId: result.job_id,
      deduplicated: Boolean(result.deduplicated),
      status: result.status ?? null,
      extractor,
      baseUrl: config.baseUrl,
      tabTitle,
    };
  } catch (error) {
    setBadge("ERR", "#b3261e");
    // #406: flag the cookie owner-gate so the popup can offer a one-click retry
    // that drops youtube_cookies — but only on the cookie-bearing attempt.
    const cookieGate = Boolean(error?.cookieGate) && !noCookies;
    return { ok: false, cookieGate, message: error.message || String(error) };
  }
}

function baseHostOf(baseUrl) {
  try {
    return new URL(baseUrl).host;
  } catch {
    return "";
  }
}

function isYoutubeUrl(url) {
  try {
    const host = new URL(String(url || "")).hostname;
    return YOUTUBE_HOST.test(host);
  } catch (_err) {
    return false;
  }
}

async function collectYoutubeCookies() {
  // Refresh on each submit — never cache. Returns "" if the user has not
  // granted the optional youtube.com host permission, or no cookies exist.
  try {
    const granted = await chrome.permissions.contains({
      origins: [YOUTUBE_COOKIE_ORIGIN],
    });
    if (!granted) {
      return "";
    }
    if (!chrome.cookies || typeof chrome.cookies.getAll !== "function") {
      return "";
    }
    const cookies = await chrome.cookies.getAll({ domain: ".youtube.com" });
    const serializer = (typeof globalThis !== "undefined" ? globalThis : self)
      .scribeCookies?.serializeCookiesToNetscape;
    if (!serializer) {
      return "";
    }
    return serializer(cookies) || "";
  } catch (_err) {
    // Never surface cookie values via error messages.
    return "";
  }
}

async function getConfig() {
  // The bearer token is a credential: keep it in chrome.storage.local, which
  // is scoped to this device and never cloud-synced. baseUrl is not secret,
  // so it stays in chrome.storage.sync for cross-device convenience.
  const [sync, local] = await Promise.all([
    chrome.storage.sync.get({ baseUrl: DEFAULT_BASE_URL }),
    chrome.storage.local.get({ bearerToken: "" }),
  ]);

  return {
    baseUrl: normalizeBaseUrl(sync.baseUrl),
    bearerToken: String(local.bearerToken || "").trim(),
  };
}

function normalizeBaseUrl(value) {
  const trimmed = String(value || DEFAULT_BASE_URL).trim().replace(/\/+$/, "");
  const parsed = new URL(trimmed);
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("Scribe base URL must start with http:// or https://.");
  }
  return parsed.origin + parsed.pathname.replace(/\/+$/, "");
}

async function ensureHostPermission(baseUrl) {
  const originPattern = `${new URL(baseUrl).origin}/*`;
  const hasPermission = await chrome.permissions.contains({ origins: [originPattern] });
  if (hasPermission) {
    return;
  }

  throw new Error(
    `Chrome has not granted access to ${new URL(baseUrl).origin}. Open extension settings and save the Scribe base URL.`,
  );
}

async function createJob(config, url, { includeCookies = true } = {}) {
  const headers = {
    "Content-Type": "application/json",
  };
  if (config.bearerToken) {
    headers.Authorization = `Bearer ${config.bearerToken}`;
  }

  const payload = { url, source: SOURCE };
  // #406: the retry-without-cookies path passes includeCookies=false so the
  // resubmit takes the public download route the owner-gate leaves open.
  if (includeCookies && isYoutubeUrl(url)) {
    const cookies = await collectYoutubeCookies();
    if (cookies) {
      payload.youtube_cookies = cookies;
    }
  }

  let response;
  try {
    response = await fetch(`${config.baseUrl}/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  } catch (error) {
    throw new Error(`Could not reach Scribe at ${config.baseUrl}: ${error.message}`);
  }

  let body = null;
  const text = await response.text();
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = { detail: text };
    }
  }

  if (!response.ok) {
    const error = new Error(formatHttpError(response.status, body, Boolean(config.bearerToken)));
    // #406: tag the cookie owner-gate so callers can offer a retry that drops
    // youtube_cookies instead of the misleading "add a bearer token" advice.
    if (isCookieGateError(response.status, body)) {
      error.cookieGate = true;
    }
    throw error;
  }

  await recordAuthenticatedAt();
  return body || {};
}

async function notifySuccess(baseUrl, result) {
  if (!result.job_id) {
    throw new Error("Scribe responded OK but returned no job ID.");
  }

  const jobUrl = `${baseUrl}/#/jobs/${result.job_id}`;
  const title = result.deduplicated ? "Already known to Scribe" : "Submitted to Scribe";
  const status = result.status ? `Status: ${result.status}. ` : "";
  const message = `${status}Click to open job #${result.job_id}.`;
  const notificationId = `scribe-job-${result.job_id}-${Date.now()}`;

  const links = await getNotificationLinks();
  links[notificationId] = jobUrl;
  await chrome.storage.local.set({ [NOTIFICATION_LINKS_KEY]: links });

  chrome.notifications.create(notificationId, {
    type: "basic",
    iconUrl: NOTIFICATION_ICON,
    title,
    message,
    priority: 1,
  });
}

async function notifyFailure(message, { retryUrl = null } = {}) {
  const notificationId = `scribe-error-${Date.now()}`;
  const options = {
    type: "basic",
    iconUrl: NOTIFICATION_ICON,
    title: "Scribe submit failed",
    message: truncate(message, 240),
    priority: 2,
  };
  // #406: a cookie owner-gate failure carries a one-click retry that resubmits
  // the same URL without youtube_cookies. Store the URL keyed by notification id
  // so onButtonClicked can resubmit it.
  if (retryUrl) {
    options.buttons = [{ title: "Retry without cookies" }];
    const retryUrls = await getRetryUrls();
    retryUrls[notificationId] = retryUrl;
    await chrome.storage.local.set({ [RETRY_URLS_KEY]: retryUrls });
  }
  chrome.notifications.create(notificationId, options);
}

async function getNotificationLinks() {
  const stored = await chrome.storage.local.get({ [NOTIFICATION_LINKS_KEY]: {} });
  return stored[NOTIFICATION_LINKS_KEY] || {};
}

async function getRetryUrls() {
  const stored = await chrome.storage.local.get({ [RETRY_URLS_KEY]: {} });
  return stored[RETRY_URLS_KEY] || {};
}

function formatDetail(body) {
  if (!body) {
    return "No response body.";
  }
  if (typeof body.detail === "string") {
    return body.detail;
  }
  if (Array.isArray(body.detail)) {
    return body.detail.map((item) => item.msg || JSON.stringify(item)).join("; ");
  }
  return JSON.stringify(body);
}

// #406: the cookie owner-gate rejects an anonymous `youtube_cookies` submit with
// a specific 403 detail (routes.py: "youtube_cookies requires owner or
// extension-token authentication"). Match on the stable prefix so a wording
// tweak on the server side does not silently fall back to generic guidance.
function isCookieGateError(status, body) {
  if (status !== 403) {
    return false;
  }
  const detail = formatDetail(body);
  return typeof detail === "string" && detail.toLowerCase().includes("youtube_cookies requires owner");
}

function formatHttpError(status, body, tokenConfigured) {
  if (status === 401) {
    const guidance = tokenConfigured
      ? "The saved bearer token was rejected. Check the token in extension settings."
      : "This Scribe URL requires authentication. Add a bearer token in extension settings.";
    return `Scribe authentication required (401): ${guidance}`;
  }

  // #406: distinguish the cookie owner-gate from a generic 403. On a trusted-LAN
  // Scribe URL no token is needed at all — "add a bearer token" sends the
  // operator down a dead end (LAN mode cannot mint extension tokens). The real
  // fix is to disable YouTube cookies (or retry without them).
  if (isCookieGateError(status, body)) {
    return (
      "Scribe rejected the YouTube cookies: this Scribe URL only accepts cookies " +
      "from a signed-in user. Disable YouTube cookies in extension settings, or " +
      "sign in and add an extension token."
    );
  }

  if (status === 403) {
    const guidance = tokenConfigured
      ? "The saved bearer token is invalid or does not allow this request. Check the token in extension settings."
      : "This Scribe URL is protected. Add a bearer token in extension settings.";
    return `Scribe authorization failed (403): ${guidance}`;
  }

  return `Scribe rejected the URL (${status}): ${formatDetail(body)}`;
}

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
  if (text !== "...") {
    chrome.alarms.create(CLEAR_BADGE_ALARM, { when: Date.now() + 3500 });
  }
}

function truncate(value, limit) {
  const text = String(value);
  return text.length > limit ? `${text.slice(0, limit - 1)}...` : text;
}
