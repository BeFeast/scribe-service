// Toolbar popup — the receipt + confirm surface (#339).
//
// Opening the popup asks the service worker to preflight the active tab
// against GET /preflight and submit it ONLY when yt-dlp classifies it as a
// single video (single_media). Containers (feed/playlist/channel/search),
// generic-only, and unreachable-preflight verdicts render a confirm state with
// a "Submit anyway" button instead of auto-submitting — this is what kills the
// blind one-click submit of the YouTube home page. Errors are shown exactly as
// the service worker / server reported them; nothing is fabricated.

const DEFAULT_BASE_URL = "https://scribe.oklabs.uk";

const statusEl = document.getElementById("status");
const metaEl = document.getElementById("meta");
const confirmBtn = document.getElementById("confirm");
const openJobLink = document.getElementById("openjob");
const settingsBtn = document.getElementById("settings");

init();

async function init() {
  document.getElementById("host").textContent = await hostLabel();
  settingsBtn.addEventListener("click", () => chrome.runtime.openOptionsPage());
  await submitAndRender(false);
}

async function hostLabel() {
  try {
    const stored = await chrome.storage.sync.get({ baseUrl: DEFAULT_BASE_URL });
    return new URL(String(stored.baseUrl || DEFAULT_BASE_URL)).host;
  } catch {
    return "";
  }
}

async function submitAndRender(force) {
  setStatus("", force ? "Submitting…" : "Checking this page…");
  confirmBtn.hidden = true;
  openJobLink.hidden = true;
  metaEl.hidden = true;
  settingsBtn.hidden = true;

  let response;
  try {
    response = await chrome.runtime.sendMessage({ type: "submit-active-tab", force });
  } catch (error) {
    renderError(String(error?.message || error));
    return;
  }
  if (!response) {
    renderError("The extension service worker did not answer.");
    return;
  }

  if (response.ok) {
    renderSubmitted(response);
    return;
  }
  if (response.confirm) {
    renderConfirm(response.message);
    return;
  }
  renderError(response.message || "Submit failed.");
}

function renderSubmitted(response) {
  const heading = response.deduplicated ? "Already known to Scribe ✓" : "Submitted to Scribe ✓";
  setStatus("ok", heading);

  const bits = [];
  if (response.extractor) {
    bits.push(response.extractor);
  }
  if (response.status) {
    bits.push(`status: ${response.status}`);
  }
  if (bits.length) {
    metaEl.textContent = bits.join(" · ");
    metaEl.hidden = false;
  }

  if (response.jobId != null && response.baseUrl) {
    openJobLink.textContent = `Open job #${response.jobId} →`;
    openJobLink.href = `${response.baseUrl}/#/jobs/${response.jobId}`;
    openJobLink.hidden = false;
  }
}

function renderConfirm(message) {
  setStatus("confirm", message || "Submit anyway?");
  confirmBtn.hidden = false;
  confirmBtn.disabled = false;
  confirmBtn.onclick = () => {
    confirmBtn.disabled = true;
    submitAndRender(true);
  };
}

function renderError(message) {
  setStatus("error", message);
  settingsBtn.hidden = false;
}

function setStatus(kind, text) {
  statusEl.className = kind;
  statusEl.textContent = text;
}
