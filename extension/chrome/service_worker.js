const DEFAULT_BASE_URL = "https://scribe.oklabs.uk";
const SOURCE = "chrome-extension";
const NOTIFICATION_LINKS_KEY = "notificationLinks";

const YOUTUBE_WATCH_URL = /^https:\/\/www\.youtube\.com\/watch(?:[?#]|$)/;
const YOUTUBE_URL = /^https:\/\/(?:www\.)?(?:youtube\.com\/|youtu\.be\/)/;

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: "submit-page",
      title: "Submit this YouTube page to Scribe",
      contexts: ["page"],
      documentUrlPatterns: ["*://*.youtube.com/*", "*://youtu.be/*"],
    });

    chrome.contextMenus.create({
      id: "submit-link",
      title: "Submit YouTube link to Scribe",
      contexts: ["link"],
      targetUrlPatterns: ["*://*.youtube.com/*", "*://youtu.be/*"],
    });
  });
});

chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.url || !YOUTUBE_WATCH_URL.test(tab.url)) {
    await notifyFailure("Open a YouTube watch page before using the toolbar action.");
    return;
  }

  await submitToScribe(tab.url);
});

chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  const url = info.linkUrl || info.pageUrl || tab?.url || "";
  if (!YOUTUBE_URL.test(url)) {
    await notifyFailure("Use this menu on a YouTube page or YouTube link.");
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

async function submitToScribe(url) {
  setBadge("...", "#5b6472");

  try {
    const config = await getConfig();
    await ensureHostPermission(config.baseUrl);
    const result = await createJob(config, url);
    await notifySuccess(config.baseUrl, result);
    setBadge("OK", "#137333");
  } catch (error) {
    await notifyFailure(error.message || String(error));
    setBadge("ERR", "#b3261e");
  }
}

async function getConfig() {
  const stored = await chrome.storage.sync.get({
    baseUrl: DEFAULT_BASE_URL,
    bearerToken: "",
  });

  return {
    baseUrl: normalizeBaseUrl(stored.baseUrl),
    bearerToken: String(stored.bearerToken || "").trim(),
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

  const granted = await chrome.permissions.request({ origins: [originPattern] });
  if (!granted) {
    throw new Error(`Chrome has not granted access to ${new URL(baseUrl).origin}.`);
  }
}

async function createJob(config, url) {
  const headers = {
    "Content-Type": "application/json",
  };
  if (config.bearerToken) {
    headers.Authorization = `Bearer ${config.bearerToken}`;
  }

  let response;
  try {
    response = await fetch(`${config.baseUrl}/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify({ url, source: SOURCE }),
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
    throw new Error(`Scribe rejected the URL (${response.status}): ${formatDetail(body)}`);
  }

  return body || {};
}

async function notifySuccess(baseUrl, result) {
  const jobUrl = `${baseUrl}/__spa__/#/jobs/${result.job_id}`;
  const title = result.deduplicated ? "Already known to Scribe" : "Submitted to Scribe";
  const status = result.status ? `Status: ${result.status}. ` : "";
  const message = `${status}Click to open job #${result.job_id}.`;
  const notificationId = `scribe-job-${result.job_id}-${Date.now()}`;

  const links = await getNotificationLinks();
  links[notificationId] = jobUrl;
  await chrome.storage.local.set({ [NOTIFICATION_LINKS_KEY]: links });

  chrome.notifications.create(notificationId, {
    type: "basic",
    iconUrl: "icons/scribe.svg",
    title,
    message,
    priority: 1,
  });
}

async function notifyFailure(message) {
  chrome.notifications.create(`scribe-error-${Date.now()}`, {
    type: "basic",
    iconUrl: "icons/scribe.svg",
    title: "Scribe submit failed",
    message: truncate(message, 240),
    priority: 2,
  });
}

async function getNotificationLinks() {
  const stored = await chrome.storage.local.get({ [NOTIFICATION_LINKS_KEY]: {} });
  return stored[NOTIFICATION_LINKS_KEY] || {};
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

function setBadge(text, color) {
  chrome.action.setBadgeText({ text });
  chrome.action.setBadgeBackgroundColor({ color });
  if (text !== "...") {
    setTimeout(() => chrome.action.setBadgeText({ text: "" }), 3500);
  }
}

function truncate(value, limit) {
  const text = String(value);
  return text.length > limit ? `${text.slice(0, limit - 1)}...` : text;
}
