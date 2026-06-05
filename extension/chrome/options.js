const DEFAULT_BASE_URL = "https://scribe.oklabs.uk";
const YOUTUBE_COOKIE_ORIGIN = "https://*.youtube.com/*";

const form = document.querySelector("#settings-form");
const baseUrlInput = document.querySelector("#base-url");
const bearerTokenInput = document.querySelector("#bearer-token");
const status = document.querySelector("#status");
const youtubeStatus = document.querySelector("#youtube-status");
const grantYoutubeButton = document.querySelector("#grant-youtube");
const revokeYoutubeButton = document.querySelector("#revoke-youtube");

document.addEventListener("DOMContentLoaded", restoreOptions);
document.addEventListener("DOMContentLoaded", refreshYoutubeStatus);
form.addEventListener("submit", saveOptions);
grantYoutubeButton?.addEventListener("click", grantYoutubeCookies);
revokeYoutubeButton?.addEventListener("click", revokeYoutubeCookies);

async function restoreOptions() {
  const stored = await chrome.storage.sync.get({
    baseUrl: DEFAULT_BASE_URL,
    bearerToken: "",
  });
  baseUrlInput.value = stored.baseUrl;
  bearerTokenInput.value = stored.bearerToken;
}

async function saveOptions(event) {
  event.preventDefault();
  status.textContent = "";

  let baseUrl;
  try {
    baseUrl = normalizeBaseUrl(baseUrlInput.value);
  } catch (error) {
    status.textContent = error.message;
    status.style.color = "#b3261e";
    return;
  }

  const originPattern = `${new URL(baseUrl).origin}/*`;
  const granted = await chrome.permissions.request({ origins: [originPattern] });
  if (!granted) {
    status.textContent = `Chrome did not grant access to ${new URL(baseUrl).origin}.`;
    status.style.color = "#b3261e";
    return;
  }

  await chrome.storage.sync.set({
    baseUrl,
    bearerToken: bearerTokenInput.value.trim(),
  });
  status.style.color = "#137333";
  status.textContent = "Saved.";
}

async function refreshYoutubeStatus() {
  if (!youtubeStatus) {
    return;
  }
  const granted = await chrome.permissions.contains({
    origins: [YOUTUBE_COOKIE_ORIGIN],
  });
  youtubeStatus.style.color = granted ? "#137333" : "#5b6472";
  youtubeStatus.textContent = granted
    ? "Enabled. Cookies will be attached to YouTube submissions."
    : "Disabled. YouTube submissions will be sent without cookies.";
}

async function grantYoutubeCookies() {
  const granted = await chrome.permissions.request({
    origins: [YOUTUBE_COOKIE_ORIGIN],
  });
  if (!granted) {
    youtubeStatus.style.color = "#b3261e";
    youtubeStatus.textContent = "Chrome did not grant access to youtube.com.";
    return;
  }
  await refreshYoutubeStatus();
}

async function revokeYoutubeCookies() {
  await chrome.permissions.remove({ origins: [YOUTUBE_COOKIE_ORIGIN] });
  await refreshYoutubeStatus();
}

function normalizeBaseUrl(value) {
  const trimmed = String(value || DEFAULT_BASE_URL).trim().replace(/\/+$/, "");
  const parsed = new URL(trimmed);
  if (parsed.protocol !== "https:" && parsed.protocol !== "http:") {
    throw new Error("Base URL must start with http:// or https://.");
  }
  return parsed.origin + parsed.pathname.replace(/\/+$/, "");
}
