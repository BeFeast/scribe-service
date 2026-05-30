// Mobile CaptureSheet — Wave 2f / Issue #281
//
// Byte-for-byte port of `openCapture()` markup from `Scribe iOS.html`
// (mobile design source, SHA-256
// 421c930d9f2d5c1549dc632760f796992630a27eaa6fa38f28ff25584bc3ebb9, ~1239).
//
// Source mapping (Scribe iOS.html → this file):
//   ~1239 function openCapture()           → <CaptureSheet open onClose />
//   ~1242 <div class="grabber">            → <div className="grabber" />
//   ~1243 <div class="sheet-hd">           → <div className="sheet-hd">
//   ~1244 <button class="s-cancel">        → <button className="s-cancel">
//   ~1245 <span class="s-title">           → <span className="s-title">
//   ~1246 <button class="s-done">          → <button className="s-done">
//   ~1248 <div class="sheet-body">         → <div className="sheet-body">
//   ~1249 <div class="url-field">          → <div className="url-field">
//   ~1250 <div id="cap-detect">            → conditional .detected card
//   ~1251 <div class="opt-row">  *3        → omitted in v1 (see Notes)
//   ~1254 <button class="bigbtn">          → <button className="bigbtn">
//   ~1255 paste-sample link                → ported verbatim
//
// Submit wiring: see Wave 2f commit N+1 (shared submitJob helper).
// This commit is the literal markup + CSS port only; submit / detect logic
// is local component state. `onSubmit` is provided by the caller so the
// markup port lands without coupling to the integration layer.
//
// Notes — Optional toggles (Summarize / Notify / Prompt):
//   The source includes three `.opt-row` toggles at lines ~1251-1253.
//   `POST /jobs` does NOT support these flags today (body is
//   `{url, source}` only). Per Issue #281, recommendation (a) is to HIDE
//   the toggles in v1 and ship them behind a separate `blocked` `api`
//   issue once the endpoint accepts `summarize`/`notify`/`prompt`.
//   The markup port preserves the url-field, detected card, and bigbtn
//   submit row; the three opt-rows are intentionally not rendered.
//
// Notes — Detected card:
//   The source renders a fabricated thumbnail/title/duration/cost ("Linus
//   Torvalds on Git — Google Tech Talk · 1:08:54 · est. $0.04"). Per
//   Issue #281, NO fabricated metadata may ship: the production detected
//   card only echoes the parsed `video_id` and the literal
//   `source: "manual"` body field, mirroring exactly what the desktop
//   command palette shows.

import React from "react";
import { parseVideoUrl } from "../command-utils.js";
import { IconCheck, IconLink, IconPlus } from "../icons.jsx";

const SAMPLE_URL = "https://youtu.be/kxopViU98Xo";

export function CaptureSheet({ open, onClose, onSubmit }) {
	const [url, setUrl] = React.useState("");
	const [submitting, setSubmitting] = React.useState(false);
	const [error, setError] = React.useState(null);
	const [shown, setShown] = React.useState(false);
	const inputRef = React.useRef(null);

	// Port of `showSheet` / `closeSheet`: the prototype toggles `.open`
	// (display) then `.shown` (transform/opacity) on the next frame so the
	// CSS transitions fire. We mirror that via a two-phase state: `open`
	// drives `.open` (mount), `shown` drives `.shown` (animate in).
	React.useEffect(() => {
		if (!open) {
			setShown(false);
			return undefined;
		}
		setUrl("");
		setError(null);
		setSubmitting(false);
		const raf = requestAnimationFrame(() => setShown(true));
		const focus = setTimeout(() => inputRef.current?.focus(), 200);
		return () => {
			cancelAnimationFrame(raf);
			clearTimeout(focus);
		};
	}, [open]);

	const parsed = parseVideoUrl(url);
	const valid = Boolean(parsed);

	const handleSubmit = React.useCallback(async () => {
		if (!valid || submitting) {
			inputRef.current?.focus();
			return;
		}
		setSubmitting(true);
		setError(null);
		try {
			await onSubmit?.(parsed.url);
		} catch (e) {
			setError(e instanceof Error ? e.message : String(e));
			setSubmitting(false);
		}
	}, [valid, submitting, parsed, onSubmit]);

	if (!open) return null;

	return (
		<div
			className={shown ? "sheet-layer open shown" : "sheet-layer open"}
			aria-label="New transcript"
		>
			<button
				type="button"
				className="sheet-bg"
				aria-label="Close"
				onClick={onClose}
			/>
			<div className="sheet">
				<div className="grabber" />
				<div className="sheet-hd">
					<button type="button" className="s-cancel" onClick={onClose}>
						Cancel
					</button>
					<span className="s-title">New transcript</span>
					<button
						type="button"
						className="s-done"
						disabled={!valid || submitting}
						onClick={handleSubmit}
					>
						Add
					</button>
				</div>
				<div className="sheet-body">
					<div className="url-field">
						<IconLink size={18} />
						<input
							ref={inputRef}
							id="cap-url"
							type="url"
							inputMode="url"
							autoComplete="off"
							autoCorrect="off"
							autoCapitalize="off"
							spellCheck={false}
							placeholder="Paste a YouTube URL"
							value={url}
							onChange={(e) => {
								setUrl(e.target.value);
								if (error) setError(null);
							}}
							onKeyDown={(e) => {
								if (e.key === "Enter") {
									e.preventDefault();
									void handleSubmit();
								} else if (e.key === "Escape") {
									e.preventDefault();
									onClose();
								}
							}}
						/>
					</div>
					{valid ? (
						<div className="detected" id="cap-detect">
							<div className="thumb">
								<IconCheck size={20} />
							</div>
							<div style={{ minWidth: 0, flex: 1 }}>
								<div className="d-title">
									video_id <span className="tnum">{parsed.videoId}</span>
								</div>
								<div className="d-meta">youtu.be · source=manual</div>
							</div>
						</div>
					) : null}
					{error ? (
						<div className="cap-error" role="alert">
							Submit failed: {error}
						</div>
					) : null}
					{/*
					   Optional toggles (Summarize / Notify / Summary prompt) from
					   source ~1251-1253 are intentionally hidden in v1 — `POST
					   /jobs` does not yet accept those flags. Tracked via the
					   separate blocked api issue (see Issue #281 notes).
					*/}
					<button
						type="button"
						className="bigbtn"
						id="cap-go"
						disabled={!valid || submitting}
						onClick={handleSubmit}
					>
						<IconPlus size={18} />
						{submitting ? "Submitting…" : "Add to queue"}
					</button>
					<div style={{ textAlign: "center", marginTop: 14 }}>
						<button
							type="button"
							className="s-cancel cap-sample"
							onClick={() => {
								setUrl(SAMPLE_URL);
								setError(null);
								inputRef.current?.focus();
							}}
						>
							Paste sample · youtu.be/kxop…
						</button>
					</div>
				</div>
			</div>
		</div>
	);
}
