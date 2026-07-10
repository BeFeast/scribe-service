// popup.js — minimal PoC UI. Triggers a client-side capture of the active tab
// and shows the resulting job id (or the honest failure from the resolve seam).

const statusEl = document.getElementById("status");
const captureBtn = document.getElementById("capture");

function setStatus(text) {
  statusEl.textContent = text;
}

// Progress messages come straight from the offscreen doc (broadcast).
chrome.runtime.onMessage.addListener((message) => {
  if (message?.type === "capture-progress") {
    const total = message.total ? ` / ${fmtMB(message.total)}` : "";
    setStatus(`Downloading… ${fmtMB(message.loaded)}${total}`);
  }
});

captureBtn.addEventListener("click", async () => {
  captureBtn.disabled = true;
  setStatus("Resolving stream…");
  try {
    const res = await chrome.runtime.sendMessage({ type: "capture-active-tab" });
    if (res?.ok) {
      const job = res.job || {};
      setStatus(`Uploaded. Job #${job.job_id ?? "?"} (${job.status ?? "queued"}).`);
    } else {
      setStatus(res?.message || "Failed.");
    }
  } catch (error) {
    setStatus(String(error?.message || error));
  } finally {
    captureBtn.disabled = false;
  }
});

function fmtMB(bytes) {
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
