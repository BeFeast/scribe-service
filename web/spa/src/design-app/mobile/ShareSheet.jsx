// Mobile share bottom-sheet — Wave 2a / Issue #277.
//
// Literal port of `openShare(t)` from `Scribe iOS.html` (mobile design
// source, SHA-256
// 421c930d9f2d5c1549dc632760f796992630a27eaa6fa38f28ff25584bc3ebb9) at
// ~1291-1314, plus the bottom-sheet primitive (showSheet/closeSheet ~1220-
// 1237) translated into React state.
//
// Source mapping (Scribe iOS.html → this file):
//   ~1224 function showSheet         → mount + `.shown` toggle in useEffect
//   ~1232 function closeSheet        → onClose() callback (transition then unmount)
//   ~1295 const apps                 → APPS (5-tile share grid: literal port)
//   ~1297 .sheet-hd / .s-title       → <header className="sheet-hd"> markup
//   ~1298 .sheet-body                → <div className="sheet-body"> markup
//   ~1301 .share-grid / .share-app   → <div className="share-grid"> tiles
//   ~1302 .share-list                → <div className="share-list"> rows
//   ~1303 summary_shortlink row      → optional row (hidden until API ships)
//   ~1304 transcript_shortlink row   → optional row (hidden until API ships)
//   ~1305 Copy summary as Markdown   → onclick wired in Wave 2a commit N+1
//   ~1306 Add to RSS feed            → onclick wired in Wave 2a commit N+1
//
// Wave 2a commit N (literal port) keeps the prototype's per-app
// click handlers as `onClose()` stubs only — no fake toasts (the
// prototype's `toast("Shared via …")` is explicitly banned).
// Commit N+1 replaces them with real `navigator.share` / clipboard /
// RSS calls.
//
// The two shortlink rows depend on a backend gap
// (`summary_shortlink` / `transcript_shortlink` on /transcripts/:id);
// they are skipped at render time until that API ships.

import React from "react";
import { fmtDuration, fmtUsd, publicBaseUrl } from "../data.js";
import {
	IconCheckIOS,
	IconCopyIOS,
	IconLinkIOS,
	IconRSSIOS,
	IconShareIOS,
} from "./icons-ios.jsx";

/* ── 5-tile share grid (verbatim port of `apps` ~1293) ─────────────── */
const APPS = [
	["Messages", "#3aa84b"],
	["Mail", "#4a7fd6"],
	["Obsidian", "#7a52c4"],
	["Telegram", "#33a0db"],
	["Notes", "#d6b23a"],
];

export function ShareSheet({ t, open, onClose, onAction }) {
	const layerRef = React.useRef(null);
	const [shown, setShown] = React.useState(false);
	const [toast, setToast] = React.useState(null);

	// Port of showSheet(): mount then transition `.shown` on the next
	// frame so the slide-up + backdrop fade animate from rest.
	React.useEffect(() => {
		if (!open) {
			setShown(false);
			return undefined;
		}
		const handle = requestAnimationFrame(() => setShown(true));
		return () => cancelAnimationFrame(handle);
	}, [open]);

	// Port of closeSheet(): drop `.shown`, then unmount after the 340ms
	// transition completes. The transition duration matches `.sheet`
	// in styles.css (`cubic-bezier(.32,.72,0,1)` 340ms).
	const requestClose = React.useCallback(() => {
		setShown(false);
		setTimeout(() => onClose(), 340);
	}, [onClose]);

	// Escape key closes the sheet (iOS swipe-down has no web analog).
	React.useEffect(() => {
		if (!open) return undefined;
		function onKey(event) {
			if (event.key === "Escape") requestClose();
		}
		document.addEventListener("keydown", onKey);
		return () => document.removeEventListener("keydown", onKey);
	}, [open, requestClose]);

	// Auto-dismiss the toast after ~1900ms — matches the prototype's
	// `toast()` recipe (Scribe iOS.html ~1325).
	React.useEffect(() => {
		if (!toast) return undefined;
		const timer = setTimeout(() => setToast(null), 1900);
		return () => clearTimeout(timer);
	}, [toast]);

	function showToast(message) {
		setToast({ message, key: Date.now() });
	}

	function handleAppShare(name) {
		// Commit N stub — replaced in commit N+1 with `navigator.share`.
		if (onAction) onAction({ kind: "share-app", app: name, showToast });
		requestClose();
	}

	function handleCopyShortlink(url, kind) {
		if (onAction) onAction({ kind, url, showToast });
		requestClose();
	}

	function handleCopyMarkdown() {
		if (onAction) onAction({ kind: "copy-markdown", showToast });
		requestClose();
	}

	function handleRss() {
		if (onAction) onAction({ kind: "rss", showToast });
		requestClose();
	}

	if (!open) return null;

	const sheetTitle = t.title ?? "";
	const sheetMeta = `#${t.id} · ${fmtDuration(t.duration_seconds)} · ${fmtUsd(
		t.vast_cost,
	)}`;
	// Shortlink rows are gated on a backend gap (see issue notes); render
	// only when the adapter surfaces them.
	const summaryShortlink = t.summary_shortlink ?? null;
	const transcriptShortlink = t.transcript_shortlink ?? null;

	return (
		<>
			<div
				ref={layerRef}
				className={shown ? "sheet-layer open shown" : "sheet-layer open"}
			>
				<button
					type="button"
					className="sheet-bg"
					aria-label="Close share sheet"
					onClick={requestClose}
				/>
				<section className="sheet" aria-label="Share">
					<div className="grabber" />
					<div className="sheet-hd">
						<span style={{ width: 54 }} />
						<span className="s-title">Share</span>
						<button
							type="button"
							className="s-done"
							data-act="close"
							onClick={requestClose}
						>
							Done
						</button>
					</div>
					<div className="sheet-body">
						<div
							style={{
								fontSize: 14,
								fontWeight: 600,
								margin: "0 2px 4px",
							}}
						>
							{sheetTitle}
						</div>
						<div
							className="mono"
							style={{
								fontSize: 12,
								margin: "0 2px 12px",
								color: "var(--muted)",
							}}
						>
							{sheetMeta}
						</div>
						<div className="share-grid">
							{APPS.map(([name, color]) => (
								<button
									type="button"
									key={name}
									className="share-app"
									data-share={name}
									onClick={() => handleAppShare(name)}
								>
									<span className="sa-ic" style={{ background: color }}>
										<IconShareIOS size={22} />
									</span>
									<span className="sa-name">{name}</span>
								</button>
							))}
						</div>
						<div className="share-list">
							{summaryShortlink ? (
								<button
									type="button"
									className="share-li"
									data-copy={summaryShortlink}
									onClick={() =>
										handleCopyShortlink(summaryShortlink, "copy-summary-link")
									}
								>
									<span className="sl-ic">
										<IconLinkIOS size={18} />
									</span>
									<span className="sl-label">Copy summary link</span>
									<span className="sl-val">{summaryShortlink}</span>
								</button>
							) : null}
							{transcriptShortlink ? (
								<button
									type="button"
									className="share-li"
									data-copy={transcriptShortlink}
									onClick={() =>
										handleCopyShortlink(
											transcriptShortlink,
											"copy-transcript-link",
										)
									}
								>
									<span className="sl-ic">
										<IconLinkIOS size={18} />
									</span>
									<span className="sl-label">Copy transcript link</span>
									<span className="sl-val">{transcriptShortlink}</span>
								</button>
							) : null}
							<button
								type="button"
								className="share-li"
								data-copy="markdown"
								onClick={handleCopyMarkdown}
							>
								<span className="sl-ic">
									<IconCopyIOS size={18} />
								</span>
								<span className="sl-label">Copy summary as Markdown</span>
								<span className="sl-val" />
							</button>
							<button
								type="button"
								className="share-li"
								data-copy="rss"
								onClick={handleRss}
							>
								<span className="sl-ic">
									<IconRSSIOS size={18} />
								</span>
								<span className="sl-label">Add to RSS feed</span>
								<span className="sl-val" />
							</button>
						</div>
					</div>
				</section>
			</div>
			{toast ? (
				<output className="toast show">
					<span className="t-ic">
						<IconCheckIOS size={15} />
					</span>
					<span>{toast.message}</span>
				</output>
			) : null}
		</>
	);
}

// Computes the public-facing RSS feed URL for the share row + the
// canonical /transcripts/:id URL used by navigator.share. Kept here so
// the action handlers in `<TranscriptDetail/>` mobile branch can share
// the same base-URL normalization the desktop ShareSheet uses.
export function shareUrlsFor(t) {
	const base = publicBaseUrl().replace(/\/+$/, "");
	return {
		canonical: `${base}/transcripts/${t.id}`,
		rss: `${base}/transcripts/${t.id}/feed.xml`,
	};
}
