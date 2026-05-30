// Submit-a-job helper (Wave 2f / Issue #281).
//
// This is a verbatim extract of the submit path that already lives inline in
// `command-palette.jsx submitUrl()` (auth.protectedFetch → POST /jobs with
// {url, source: "manual"} → isJobView validation). The desktop palette is
// byte-locked design-source code, so we cannot edit it; instead the mobile
// CaptureSheet imports this helper, and a follow-up wave can DRY the palette
// onto the same path.
//
// Source mapping:
//   command-palette.jsx submitUrl()  →  submitJob(auth, url) below.
//
// Contract:
//   - `auth` is the value returned from useAuth(); the only field we touch is
//     `auth.protectedFetch`, which already attaches Clerk/trusted-network
//     credentials and returns a real fetch Response.
//   - `url` must be a string that already passed `parseVideoUrl(url) != null`;
//     the helper does NOT re-validate the YouTube URL shape — that is the
//     caller's responsibility (mirrors the existing palette behaviour).
//
// Returns the parsed JobView ({job_id, video_id, status}) on success.
// Throws an Error on any HTTP/parse failure; the caller surfaces the message
// inline (no window.alert/confirm/prompt — see check-forbidden-primitives.sh).

import { isJobView } from "./command-utils.js";

export async function submitJob(auth, url) {
	const response = await auth.protectedFetch("/jobs", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify({ url, source: "manual" }),
	});
	const body = await response.json().catch(() => null);
	if (!response.ok || !isJobView(body)) {
		throw new Error(`HTTP ${response.status}`);
	}
	return { job_id: body.job_id, video_id: body.video_id, status: body.status };
}
