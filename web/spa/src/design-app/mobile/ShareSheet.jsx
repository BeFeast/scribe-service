// Mobile Share bottom sheet — Wave 2b / Issue #277.
//
// Literal port of `openShare(t)` from `Scribe iOS.html` (mobile design source,
// SHA-256 421c930d9f2d5c1549dc632760f796992630a27eaa6fa38f28ff25584bc3ebb9),
// lines ~1290-1320 + recipe CSS lines 467-476 (.share-grid/.share-app/
// .share-list/.share-li/.sl-ic/.sl-label/.sl-val).
//
// Source mapping (Scribe iOS.html → this file):
//   ~1290 function openShare(t) → <ShareSheet />
//   ~1297 sheet header "Share" + Done → <SheetHeader />
//   ~1300 #${t.id} · dur · cost meta  → <SheetMeta />
//   ~1301 .share-grid + apps array    → DROPPED (see "Real-data wiring")
//   ~1302 .share-list                 → ShareList (real actions only)
//
// Real-data wiring (HARD per task spec / AGENTS.md):
//   - DROPPED: the `.share-grid` row of fake colored boxes labeled
//     Messages/Mail/Obsidian/Telegram/Notes that did not actually invoke
//     those apps. Shipping fake UI labelled like real OS apps = fake
//     telemetry. Replaced with a single "Share via system" row that calls
//     the real Web Share API where available.
//   - HIDDEN: `summary_shortlink` / `transcript_shortlink` rows are gated
//     on a backend gap on `/transcripts/:id` and are commented out below
//     with TODO refs (Issue: backend exposing shortlinks on the detail
//     payload). Re-enable when the API ships them.
//   - SHIPPED actions:
//       * Web Share (navigator.share)
//       * Copy summary as Markdown (clipboard)
//       * Add to RSS feed (clipboard)
//       * Copy URL (clipboard)
//       * Open source URL (window.open)

import React from "react";
import {
	IconCheck,
	IconCopy,
	IconExternal,
	IconLink,
	IconRSS,
} from "../icons.jsx";

const TOAST_MS = 1800;

export function ShareSheet({ transcript, onClose }) {
	const [shown, setShown] = React.useState(false);
	const [toast, setToast] = React.useState(null);
	const toastTimer = React.useRef(null);

	// Slide-in transition: open class on mount, then `shown` class one
	// frame later so the CSS transform/opacity transition fires (port of
	// the prototype's two-step `.open` then `.shown` toggle ~line 1240).
	React.useEffect(() => {
		const id = window.requestAnimationFrame(() => setShown(true));
		return () => window.cancelAnimationFrame(id);
	}, []);

	React.useEffect(() => {
		return () => {
			if (toastTimer.current) {
				window.clearTimeout(toastTimer.current);
				toastTimer.current = null;
			}
		};
	}, []);

	const dismiss = React.useCallback(() => {
		setShown(false);
		// Match the .sheet 0.34s transform transition; unmount after it
		// completes so the next open replays the slide.
		window.setTimeout(() => onClose?.(), 340);
	}, [onClose]);

	const flashToast = React.useCallback((label) => {
		setToast(label);
		if (toastTimer.current) window.clearTimeout(toastTimer.current);
		toastTimer.current = window.setTimeout(() => setToast(null), TOAST_MS);
	}, []);

	const onCopy = React.useCallback(
		async (text, label) => {
			try {
				if (navigator.clipboard?.writeText) {
					await navigator.clipboard.writeText(text);
					flashToast(label);
				} else {
					flashToast("Copy failed");
				}
			} catch {
				flashToast("Copy failed");
			}
		},
		[flashToast],
	);

	const onWebShare = React.useCallback(async () => {
		const payload = {
			title: transcript.title || `Transcript #${transcript.id}`,
			text: transcript.summary_md
				? truncate(transcript.summary_md, 280)
				: transcript.title || "",
			url: window.location.href,
		};
		try {
			if (navigator.share) {
				await navigator.share(payload);
				dismiss();
				return;
			}
			// No Web Share support: fall back to copying the URL.
			await onCopy(window.location.href, "Link copied");
		} catch (error) {
			// User cancelled or permission denied — silent dismiss.
			if (error?.name !== "AbortError") flashToast("Share failed");
		}
	}, [transcript, onCopy, dismiss, flashToast]);

	const onCopyMarkdown = React.useCallback(() => {
		if (!transcript.summary_md) {
			flashToast("No summary to copy");
			return;
		}
		void onCopy(transcript.summary_md, "Summary copied");
	}, [transcript.summary_md, onCopy, flashToast]);

	const onCopyRss = React.useCallback(() => {
		const rssUrl = `${window.location.origin}/feed.xml`;
		void onCopy(rssUrl, "RSS link copied");
	}, [onCopy]);

	const onCopyUrl = React.useCallback(() => {
		void onCopy(window.location.href, "URL copied");
	}, [onCopy]);

	const onOpenSource = React.useCallback(() => {
		if (transcript.source_url) {
			window.open(transcript.source_url, "_blank", "noopener,noreferrer");
			dismiss();
		}
	}, [transcript.source_url, dismiss]);

	const hasWebShare = typeof navigator !== "undefined" && "share" in navigator;

	return (
		<div
			className={`sheet-layer open${shown ? " shown" : ""}`}
			// biome-ignore lint/a11y/useSemanticElements: bottom-sheet primitive uses CSS .sheet-layer recipe with manual open/dismiss; native <dialog> implies showModal() lifecycle which differs
			role="dialog"
			aria-modal="true"
			aria-label="Share transcript"
		>
			<button
				type="button"
				className="sheet-bg"
				aria-label="Dismiss"
				onClick={dismiss}
			/>
			<div className="sheet">
				<div className="grabber" />
				<div className="sheet-hd">
					<span style={{ width: 54 }} />
					<span className="s-title">Share</span>
					<button type="button" className="s-done" onClick={dismiss}>
						Done
					</button>
				</div>
				<div className="sheet-body">
					<SheetMeta transcript={transcript} />
					<div className="share-list">
						{hasWebShare ? (
							<button type="button" className="share-li" onClick={onWebShare}>
								<span className="sl-ic">
									<IconExternal size={18} />
								</span>
								<span className="sl-label">Share via system</span>
								<span className="sl-val" />
							</button>
						) : null}
						{/* TODO(api-gap): re-enable when /transcripts/:id exposes
						    `summary_shortlink`. Issue: backend shortlink API gap
						    blocks the redesigned share-list. */}
						{/*
						{transcript.summary_shortlink ? (
							<button
								type="button"
								className="share-li"
								onClick={() =>
									onCopy(transcript.summary_shortlink, "Summary link copied")
								}
							>
								<span className="sl-ic"><IconLink size={18} /></span>
								<span className="sl-label">Copy summary link</span>
								<span className="sl-val">{transcript.summary_shortlink}</span>
							</button>
						) : null}
						*/}
						{/* TODO(api-gap): same as above for `transcript_shortlink`. */}
						{/*
						{transcript.transcript_shortlink ? (
							<button
								type="button"
								className="share-li"
								onClick={() =>
									onCopy(transcript.transcript_shortlink, "Transcript link copied")
								}
							>
								<span className="sl-ic"><IconLink size={18} /></span>
								<span className="sl-label">Copy transcript link</span>
								<span className="sl-val">{transcript.transcript_shortlink}</span>
							</button>
						) : null}
						*/}
						<button
							type="button"
							className="share-li"
							onClick={onCopyMarkdown}
							disabled={!transcript.summary_md}
						>
							<span className="sl-ic">
								<IconCopy size={18} />
							</span>
							<span className="sl-label">Copy summary as Markdown</span>
							<span className="sl-val" />
						</button>
						<button type="button" className="share-li" onClick={onCopyUrl}>
							<span className="sl-ic">
								<IconLink size={18} />
							</span>
							<span className="sl-label">Copy page URL</span>
							<span className="sl-val" />
						</button>
						<button type="button" className="share-li" onClick={onCopyRss}>
							<span className="sl-ic">
								<IconRSS size={18} />
							</span>
							<span className="sl-label">Copy RSS feed link</span>
							<span className="sl-val" />
						</button>
						{transcript.source_url ? (
							<button type="button" className="share-li" onClick={onOpenSource}>
								<span className="sl-ic">
									<IconExternal size={18} />
								</span>
								<span className="sl-label">Open source URL</span>
								<span className="sl-val">
									{shortHost(transcript.source_url)}
								</span>
							</button>
						) : null}
					</div>
				</div>
			</div>
			{toast ? (
				<div
					className="toast show"
					// biome-ignore lint/a11y/useSemanticElements: native <output> doesnt fit the .toast fixed/positioned recipe
					role="status"
					aria-live="polite"
				>
					<span className="t-ic">
						<IconCheck size={15} />
					</span>
					<span>{toast}</span>
				</div>
			) : null}
		</div>
	);
}

function SheetMeta({ transcript }) {
	const id = transcript.id;
	const dur = formatDur(transcript.duration_seconds);
	const cost = formatUsdShort(transcript.vast_cost);
	return (
		<>
			<div
				style={{
					fontSize: 14,
					fontWeight: 600,
					margin: "0 2px 4px",
				}}
			>
				{transcript.title || `Transcript #${id}`}
			</div>
			<div
				style={{
					font: "500 12px/1 var(--font-mono)",
					color: "var(--muted)",
					margin: "0 2px 12px",
				}}
			>
				#{id} · {dur} · {cost}
			</div>
		</>
	);
}

function truncate(text, max) {
	if (!text) return "";
	if (text.length <= max) return text;
	return `${text.slice(0, max - 1).trimEnd()}…`;
}

function shortHost(url) {
	try {
		return new URL(url).hostname.replace(/^www\./, "");
	} catch {
		return "";
	}
}

function formatDur(seconds) {
	if (!Number.isFinite(seconds) || seconds <= 0) return "—";
	const m = Math.floor(seconds / 60);
	const s = Math.floor(seconds % 60);
	return `${m}:${String(s).padStart(2, "0")}`;
}

function formatUsdShort(value) {
	if (!Number.isFinite(value) || value < 0) return "—";
	if (value < 0.01) return "<$0.01";
	return `$${value.toFixed(2)}`;
}
