# Scribe Chrome Extension

Small Manifest V3 operator tool for submitting YouTube URLs to Scribe.

## Install

1. Open `chrome://extensions`.
2. Enable Developer mode.
3. Choose Load unpacked.
4. Select `extension/chrome`.

No build step is required.

## Configure

Open the extension options page and set:

- Scribe base URL, defaulting to `https://scribe.oklabs.uk`.
- Optional bearer token. Leave it blank unless the Scribe deployment requires one.

The token is stored in Chrome sync storage and sent as `Authorization: Bearer ...` only when configured. No token is hardcoded in the extension.

## Manual Verification

1. Load the unpacked extension and keep the default Scribe base URL or set a local/runtime Scribe URL.
2. Open `https://www.youtube.com/watch?v=jNQXAC9IVRw` and click the toolbar action.
3. Confirm Chrome shows a success notification and clicking it opens `{Scribe base URL}/__spa__/#/jobs/{job_id}`.
4. Right-click a YouTube page and choose Submit this YouTube page to Scribe; confirm success or already-known status is shown clearly.
5. Right-click a YouTube link and choose Submit YouTube link to Scribe; confirm success or already-known status is shown clearly.
6. Set the base URL to an unreachable host and submit again; confirm the notification includes a useful connectivity error.
7. Submit a non-YouTube toolbar page; confirm the extension reports that a YouTube watch page is required.

The extension posts to Scribe's existing `POST /jobs` API with:

```json
{"url":"https://www.youtube.com/watch?v=...","source":"chrome-extension"}
```
