// Shared `submitJob(auth, url)` helper — extracted from the desktop
// CommandPalette `submitUrl()` (web/spa/src/design-app/command-palette.jsx)
// so the mobile CaptureSheet (Wave 2f, Issue #281) can share the exact
// same POST /jobs path. Both desktop ⌘K and the mobile capture sheet must
// hit the real endpoint with `{url, source: "manual"}` — no mock submit,
// no fabricated video metadata.
//
// This module lives in the editable integration layer (design-app/), NOT
// in the byte-locked design-source/app/. The desktop command-palette.jsx
// in the editable layer imports it; design-source/ is left untouched.

import { isJobView, parseVideoUrl } from "./command-utils.js";

/**
 * Submit a YouTube URL to POST /jobs via `auth.protectedFetch`.
 *
 * Returns the parsed job view ({ job_id, video_id, status, ... }) on
 * success, or throws on transport / parse error. The caller is responsible
 * for surfacing the parsed `video_id` back to the user before submit and
 * for routing to `#/jobs/:job_id` on success.
 *
 * The helper does not invent any metadata beyond the URL itself: only the
 * `video_id` echoed by the server and the literal `source: "manual"` body
 * field are returned.
 */
export async function submitJob(auth, url) {
	const parsed = parseVideoUrl(url);
	if (!parsed) {
		const err = new Error("invalid_youtube_url");
		err.code = "invalid_youtube_url";
		throw err;
	}
	const response = await auth.protectedFetch("/jobs", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ url: parsed.url, source: "manual" }),
	});
	const body = await response.json().catch(() => null);
	if (!response.ok || !isJobView(body)) {
		const err = new Error(`HTTP ${response.status}`);
		err.code = "submit_failed";
		err.status = response.status;
		throw err;
	}
	return body;
}
