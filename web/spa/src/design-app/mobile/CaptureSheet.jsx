// Mobile Capture bottom sheet — Wave 2f / Issue #281.
//
// Literal port of `Scribe iOS.html openCapture()` (~line 1239-1289):
// paste-URL field, detected card (real video_id echo, NOT the prototype's
// hardcoded "Linus Torvalds — Google Tech Talk" thumbnail), and a single
// primary "Add to queue" .bigbtn.
//
// Source mapping (Scribe iOS.html → this file):
//   ~1239 function openCapture     → <CaptureSheet open onClose />
//   ~1242 .grabber                 → <span className="grabber" />
//   ~1243 .sheet-hd                → header row (Cancel / "New transcript" / Add)
//   ~1248 .url-field + #cap-url   → <label className="url-field"><input/></label>
//   ~1249 #cap-detect             → <div className="detected"> echo
//   ~1250 .opt-row Summarize/...  → DROPPED in v1: POST /jobs only accepts
//                                    {url, source} today. The toggle rows
//                                    are not rendered. (Out-of-scope, will
//                                    be re-added when API supports
//                                    notify/summary/prompt — tracked
//                                    separately.)
//   ~1253 #cap-go .bigbtn         → primary submit button
//   ~1280 submit() prototype mock → real submitJob(auth, url) → POST /jobs
//
// Real-data wiring (HARD):
//   - Submit calls auth.protectedFetch("/jobs", POST {url, source:"manual"}).
//     There is NO mock submission path. Every submit in production hits the
//     real API.
//   - The detected card shows the parsed `videoId` from
//     parseVideoUrl(url).videoId and the literal `source=manual` echo. We
//     do NOT invent a thumbnail/title/duration/cost — those would lie to
//     the user. The prototype's hardcoded "Linus Torvalds — Google Tech
//     Talk" thumbnail is intentionally NOT ported.
//   - On success: navigateDesign("job", {id: result.job_id}) drills into
//     the live job view, the sheet closes, and a `.toast` reads "Added to
//     queue".
//   - On error: an inline error row renders in the sheet body. No
//     window.alert/confirm/prompt (see check-forbidden-primitives.sh).
//
// The sheet uses the `.sheet-layer / .sheet / .sheet-bg / .grabber /
// .sheet-hd / .sheet-body` recipe shipped in Wave 0 (verified against
// `Scribe iOS.html` lines ~420-468). The `.url-field / .detected /
// .bigbtn` recipes ship in this wave as a CSS append (also a verbatim
// port of the iOS source, with literal hex replaced by tokens).

import React from "react";
import { submitJob } from "../api-jobs.js";
import { parseVideoUrl } from "../command-utils.js";
import { IconCheck, IconLink, IconPlus, IconWave } from "../icons.jsx";

export function CaptureSheet({ open, onClose, auth, navigateDesign }) {
	const [url, setUrl] = React.useState("");
	const [submitting, setSubmitting] = React.useState(false);
	const [error, setError] = React.useState(null);
	const [toast, setToast] = React.useState(null);
	const inputRef = React.useRef(null);
	const toastTimerRef = React.useRef(null);

	// `shown` controls the slide-in transform. We mount the layer first
	// (`open`), then flip `shown` on the next frame so the CSS transition
	// runs. On close we reverse: clear `shown`, wait for the transition,
	// then unmount via `onClose`.
	const [shown, setShown] = React.useState(false);

	React.useEffect(() => {
		if (!open) {
			setShown(false);
			return undefined;
		}
		// Reset state every time the sheet opens.
		setUrl("");
		setError(null);
		setSubmitting(false);
		const raf = requestAnimationFrame(() => setShown(true));
		const focusTimer = setTimeout(() => {
			if (inputRef.current) inputRef.current.focus();
		}, 200);
		return () => {
			cancelAnimationFrame(raf);
			clearTimeout(focusTimer);
		};
	}, [open]);

	React.useEffect(() => {
		return () => {
			if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
		};
	}, []);

	const parsed = React.useMemo(() => parseVideoUrl(url), [url]);
	const videoId = parsed ? parsed.videoId : null;
	const valid = Boolean(videoId) && !submitting;

	const close = React.useCallback(() => {
		setShown(false);
		// Match the .sheet transition (340ms). Then actually unmount.
		setTimeout(() => onClose(), 340);
	}, [onClose]);

	const flashToast = React.useCallback((message) => {
		setToast(message);
		if (toastTimerRef.current) clearTimeout(toastTimerRef.current);
		toastTimerRef.current = setTimeout(() => setToast(null), 1800);
	}, []);

	const submit = React.useCallback(async () => {
		if (!valid || !parsed) return;
		setSubmitting(true);
		setError(null);
		try {
			const result = await submitJob(auth, parsed.url);
			close();
			// Navigate after the sheet finishes sliding out so the page
			// transition runs against a settled chrome (mirrors the
			// prototype's `setTimeout(..., 280)` after closeSheet()).
			setTimeout(() => {
				navigateDesign("job", { id: result.job_id });
				flashToast("Added to queue");
			}, 280);
		} catch (err) {
			setSubmitting(false);
			setError(err instanceof Error ? err.message : String(err));
		}
	}, [valid, parsed, auth, navigateDesign, close, flashToast]);

	function onKey(event) {
		if (event.key === "Escape") {
			event.preventDefault();
			close();
			return;
		}
		if (event.key === "Enter" && valid) {
			event.preventDefault();
			void submit();
		}
	}

	if (!open && !toast) return null;

	return (
		<>
			{open ? (
				<div
					className={shown ? "sheet-layer open shown" : "sheet-layer open"}
					// biome-ignore lint/a11y/useSemanticElements: the <dialog>
					// element does not honor the .sheet-layer / .sheet
					// transform-on-`.shown` slide-in recipe shipped in Wave 0;
					// the design-source DOM shape (div + role=dialog) must be
					// preserved so the bottom-sheet animation works.
					role="dialog"
					aria-modal="true"
					aria-label="New transcript"
				>
					<div
						className="sheet-bg"
						onClick={close}
						onKeyDown={(event) => {
							if (event.key === "Enter" || event.key === " ") close();
						}}
						role="button"
						tabIndex={-1}
						aria-label="Close capture sheet"
					/>
					<div className="sheet">
						<span className="grabber" aria-hidden="true" />
						<div className="sheet-hd">
							<button
								type="button"
								className="s-cancel"
								onClick={close}
								disabled={submitting}
							>
								Cancel
							</button>
							<span className="s-title">New transcript</span>
							<button
								type="button"
								className="s-done"
								onClick={submit}
								disabled={!valid}
							>
								{submitting ? "Adding…" : "Add"}
							</button>
						</div>
						<div className="sheet-body">
							<label className="url-field">
								<IconLink size={18} />
								<input
									ref={inputRef}
									type="url"
									inputMode="url"
									autoComplete="off"
									autoCapitalize="off"
									autoCorrect="off"
									spellCheck={false}
									placeholder="Paste a YouTube URL"
									value={url}
									onChange={(event) => setUrl(event.target.value)}
									onKeyDown={onKey}
								/>
							</label>

							{videoId ? (
								<div className="detected">
									<div className="thumb" aria-hidden="true">
										<IconWave size={20} />
									</div>
									<div style={{ minWidth: 0, flex: 1 }}>
										<div className="d-title">video_id {videoId}</div>
										<div className="d-meta">source=manual</div>
									</div>
								</div>
							) : null}

							{error ? (
								<div
									className="detected"
									role="alert"
									style={{
										borderColor:
											"color-mix(in oklab, var(--err) 32%, transparent)",
										background:
											"color-mix(in oklab, var(--err) 10%, var(--bg))",
									}}
								>
									<div style={{ minWidth: 0, flex: 1 }}>
										<div className="d-title" style={{ color: "var(--err)" }}>
											Could not submit job
										</div>
										<div className="d-meta">{error}</div>
									</div>
								</div>
							) : null}

							<button
								type="button"
								className="bigbtn"
								onClick={submit}
								disabled={!valid}
							>
								<IconPlus size={18} />
								{submitting ? "Adding to queue…" : "Add to queue"}
							</button>
						</div>
					</div>
				</div>
			) : null}
			{toast ? (
				<output className="toast show" aria-live="polite">
					<span className="t-ic" aria-hidden="true">
						<IconCheck size={15} />
					</span>
					{toast}
				</output>
			) : null}
		</>
	);
}
