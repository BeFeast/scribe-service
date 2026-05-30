// Mobile page-config helper — Wave 1 / Issue #275.
//
// Maps a Route ({ page, params }) to the shell chrome props that
// <MobileShell /> expects: { title, large, sub, canBack }. The prototype's
// view*** functions returned this shape per route inside buildPage(); in
// the React port the page components stay focused on their own bodies and
// the chrome is computed deterministically here from the route.
//
// Only `canBack` is dynamic in the prototype (navStack.length > 0). With
// the hash router as the source of truth, "can go back" is always the
// drill-in routes (transcript / job) — root tabs (library / queue / ops /
// settings / history) cannot pop back into a tab they don't know about.
// `window.history.back()` is still safe at the root because the hash
// router intercepts the `hashchange` event and re-renders accordingly.

export function pageChrome(route, runtime) {
	switch (route.page) {
		case "library":
			return {
				title: "Library",
				large: "Library",
				sub: librarySub(runtime),
				canBack: false,
			};
		case "transcript":
			return {
				title: "Transcript",
				large: false, // detail page hides large title
				canBack: true,
			};
		case "queue":
			return {
				title: "Queue",
				large: "Queue",
				sub: queueSub(runtime),
				canBack: false,
			};
		case "job":
			return {
				title: "Job",
				large: false,
				canBack: true,
			};
		case "history":
			return {
				title: "History",
				large: "History",
				sub: undefined,
				canBack: false,
			};
		case "ops":
			return {
				title: "Ops",
				large: "Ops",
				sub: opsSub(runtime),
				canBack: false,
			};
		case "settings":
			return {
				title: "Settings",
				large: "Settings",
				sub: undefined,
				canBack: false,
			};
		default:
			return { title: "Library", large: "Library", canBack: false };
	}
}

function librarySub(runtime) {
	if (!runtime) return undefined;
	const total = Math.max(
		runtime.libraryTotal ?? 0,
		(runtime.transcripts ?? []).length,
	);
	if (!total) return undefined;
	return `${total} transcript${total === 1 ? "" : "s"}`;
}

function queueSub(runtime) {
	const n = (runtime?.activeJobs ?? []).length;
	if (!n) return "no active jobs";
	return `${n} active job${n === 1 ? "" : "s"}`;
}

function opsSub(runtime) {
	const stats = runtime?.stats;
	if (!stats?.worker_pool) return undefined;
	const pool = stats.worker_pool;
	if (typeof pool.active !== "number" || typeof pool.total !== "number") {
		return undefined;
	}
	return `${pool.active}/${pool.total} workers`;
}

// Tab-badge construction — real telemetry only (no mock counts).
//
//   Library: total transcript count.
//   Queue:   number of active jobs.
//   Ops:     number of failures within the last 24h.
//   Settings, Capture: no badge.
//
// Matches the existing Sidebar badge selectors in design-app/shell.jsx.
export function tabBadges(runtime) {
	if (!runtime) return {};
	const out = {};
	const transcriptTotal = Math.max(
		runtime.libraryTotal ?? 0,
		(runtime.transcripts ?? []).length,
	);
	if (transcriptTotal > 0) {
		out.library = {
			text: String(transcriptTotal),
			aria: `${transcriptTotal} transcripts`,
		};
	}
	const queueLen = (runtime.activeJobs ?? []).length;
	if (queueLen > 0) {
		out.queue = {
			text: String(queueLen),
			aria: `${queueLen} active jobs`,
		};
	}
	const failuresToday = countFailuresInLastDay(runtime.failures ?? []);
	if (failuresToday > 0) {
		out.ops = {
			text: `${failuresToday}!`,
			aria: `${failuresToday} failures today`,
		};
	}
	return out;
}

// Local copy of the Sidebar's failure-window helper. Kept here rather than
// imported from design-app/data.js so the badge math stays a property of
// the chrome, not of the byte-locked design-source pipe.
function countFailuresInLastDay(failures, now = Date.now()) {
	const cutoff = now - 24 * 60 * 60 * 1000;
	let count = 0;
	for (const failure of failures) {
		if (!failure?.failed_at) continue;
		const ts = Date.parse(failure.failed_at);
		if (Number.isFinite(ts) && ts >= cutoff) count += 1;
	}
	return count;
}
