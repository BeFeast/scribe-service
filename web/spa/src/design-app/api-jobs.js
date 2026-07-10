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
//   command-palette.jsx submitUrl()  →  submitJob(auth, url, opts) below.
//
// Contract:
//   - `auth` is the value returned from useAuth(); the only field we touch is
//     `auth.protectedFetch`, which already attaches Clerk/trusted-network
//     credentials and returns a real fetch Response.
//   - `url` must be a string that already passed `parseVideoUrl(url) != null`;
//     the helper does NOT re-validate the YouTube URL shape — that is the
//     caller's responsibility (mirrors the existing palette behaviour).
//   - `opts` carries the optional per-job Capture toggles (#296):
//       { summarize?: boolean, notify?: boolean, summaryPrompt?: string }
//     Each field is forwarded only when the caller set it, so the legacy
//     submitJob(auth, url) call stays byte-identical on the wire and POST /jobs
//     applies its server-side defaults (summarize/notify default true, active
//     prompt template) for anything omitted.
//
// Returns the parsed JobView ({job_id, video_id, status}) on success.
// Throws an Error on any HTTP/parse failure; the caller surfaces the message
// inline (no window.alert/confirm/prompt — see check-forbidden-primitives.sh).

import { isJobView } from "./command-utils.js";

export async function submitJob(auth, url, opts = {}) {
	const payload = { url, source: "manual" };
	if (typeof opts.summarize === "boolean") payload.summarize = opts.summarize;
	if (typeof opts.notify === "boolean") payload.notify = opts.notify;
	const summaryPrompt =
		typeof opts.summaryPrompt === "string" ? opts.summaryPrompt.trim() : "";
	if (summaryPrompt) payload.summary_prompt = summaryPrompt;

	const response = await auth.protectedFetch("/jobs", {
		method: "POST",
		headers: { "Content-Type": "application/json" },
		body: JSON.stringify(payload),
	});
	const body = await response.json().catch(() => null);
	if (!response.ok || !isJobView(body)) {
		throw new Error(`HTTP ${response.status}`);
	}
	return { job_id: body.job_id, video_id: body.video_id, status: body.status };
}

// Upload-your-own-video submit helper (#408). POSTs a user-selected local
// video/audio File to POST /jobs/upload as multipart/form-data; the backend
// streams it to disk, transcribes + summarizes it like any other job, and
// archives a downscaled copy to R2.
//
// Contract:
//   - `auth` is the value from useAuth(); we only touch `auth.protectedFetch`.
//   - `file` is a browser File (from an <input type="file"> or a drop).
//   - `opts` mirrors submitJob's per-job Capture toggles (#296):
//       { summarize?: boolean, notify?: boolean, summaryPrompt?: string,
//         source?: string }
//     Each is forwarded only when set; the server applies its defaults for
//     anything omitted.
//
// We intentionally do NOT set a Content-Type header: the browser fills in the
// multipart boundary for a FormData body. protectedFetch only attaches the
// auth header, so the boundary survives.
//
// Returns the parsed JobView on success; throws on HTTP/parse failure so the
// caller can render the message inline (no browser-native dialogs).
export async function submitUploadJob(auth, file, opts = {}) {
	const form = new FormData();
	form.append("file", file);
	form.append(
		"source",
		typeof opts.source === "string" ? opts.source : "upload",
	);
	if (typeof opts.summarize === "boolean")
		form.append("summarize", String(opts.summarize));
	if (typeof opts.notify === "boolean")
		form.append("notify", String(opts.notify));
	const summaryPrompt =
		typeof opts.summaryPrompt === "string" ? opts.summaryPrompt.trim() : "";
	if (summaryPrompt) form.append("summary_prompt", summaryPrompt);

	const response = await auth.protectedFetch("/jobs/upload", {
		method: "POST",
		body: form,
	});
	const body = await response.json().catch(() => null);
	if (!response.ok || !isJobView(body)) {
		// Surface the server's rejection detail so callers can show the size-cap
		// (413) and ffprobe (422) reasons inline, not just the bare status. The
		// `HTTP <status>` prefix is preserved for backward compatibility.
		const detail =
			body && typeof body === "object" && typeof body.detail === "string"
				? body.detail
				: null;
		throw new Error(
			detail ? `HTTP ${response.status}: ${detail}` : `HTTP ${response.status}`,
		);
	}
	return { job_id: body.job_id, video_id: body.video_id, status: body.status };
}
