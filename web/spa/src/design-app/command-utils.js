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

export function isCommandPaletteShortcut(event) {
	const key = event.key?.toLowerCase?.() ?? "";
	return (
		(event.metaKey || event.ctrlKey) && (key === "k" || event.code === "KeyK")
	);
}
