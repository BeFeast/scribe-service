export const YOUTUBE_VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const YOUTUBE_HOSTS = new Set([
	"youtube.com",
	"www.youtube.com",
	"m.youtube.com",
	"music.youtube.com",
]);

export function parseVideoUrl(raw) {
	const value = raw.trim();
	if (!value) return null;
	const withProtocol = /^[a-z][a-z0-9+.-]*:\/\//i.test(value)
		? value
		: `https://${value}`;
	let url;
	try {
		url = new URL(withProtocol);
	} catch {
		return null;
	}
	let videoId = null;
	if (url.hostname === "youtu.be") {
		videoId = url.pathname.split("/").filter(Boolean)[0] ?? null;
	} else if (YOUTUBE_HOSTS.has(url.hostname)) {
		if (url.pathname === "/watch") videoId = url.searchParams.get("v");
		else {
			const [kind, id] = url.pathname.split("/").filter(Boolean);
			if (["shorts", "live", "embed"].includes(kind)) videoId = id ?? null;
		}
	}
	if (!videoId || !YOUTUBE_VIDEO_ID_RE.test(videoId)) return null;
	return { url: value, videoId };
}

export function isJobView(value) {
	return Boolean(
		value &&
			typeof value === "object" &&
			typeof value.job_id === "number" &&
			typeof value.video_id === "string" &&
			typeof value.status === "string",
	);
}

// A submit is "in flight" only while the request is pending. A terminal error
// or a queued success must NOT block the next pick, so the palette can retry a
// rejected upload (e.g. 413 too large / 422 invalid media) without reopening.
export function isSubmitInFlight(submitted) {
	return Boolean(submitted && submitted.state === "submitting");
}

export function isCommandPaletteShortcut(event) {
	const key = event.key?.toLowerCase?.() ?? "";
	return (
		(event.metaKey || event.ctrlKey) && (key === "k" || event.code === "KeyK")
	);
}

const RECENT_SUBMISSIONS_KEY = "scribe.cmdk.recentSubmissions";
const MAX_RECENT_SUBMISSIONS = 5;

function isRecentSubmission(value) {
	return Boolean(
		value &&
			typeof value === "object" &&
			typeof value.id === "number" &&
			typeof value.video_id === "string",
	);
}

// Recent ⌘K submissions are persisted client-side so the palette can offer a
// one-key jump back to a just-queued job across reloads. Real ids only — the
// prototype shipped hardcoded mock rows here.
export function readRecentSubmissions() {
	try {
		const raw = localStorage.getItem(RECENT_SUBMISSIONS_KEY);
		if (!raw) return [];
		const parsed = JSON.parse(raw);
		if (!Array.isArray(parsed)) return [];
		return parsed.filter(isRecentSubmission).slice(0, MAX_RECENT_SUBMISSIONS);
	} catch {
		return [];
	}
}

export function pushRecentSubmission(entry) {
	if (!isRecentSubmission(entry)) return readRecentSubmissions();
	const next = [
		{
			id: entry.id,
			video_id: entry.video_id,
			title: entry.title ?? null,
			status: entry.status ?? null,
			ts: typeof entry.ts === "number" ? entry.ts : Date.now(),
		},
		// Dedup by video_id so re-submitting the same video refreshes to the
		// current job id instead of leaving a stale row pointing at an old one.
		...readRecentSubmissions().filter((r) => r.video_id !== entry.video_id),
	].slice(0, MAX_RECENT_SUBMISSIONS);
	try {
		localStorage.setItem(RECENT_SUBMISSIONS_KEY, JSON.stringify(next));
	} catch {
		// Recents are a convenience cache; submission success must not depend on storage.
	}
	return next;
}
