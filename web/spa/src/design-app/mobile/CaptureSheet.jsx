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
//   ~1250 .opt-row Summarize/...  → RE-ENABLED (#296): POST /jobs now accepts
//                                    optional {summarize, notify,
//                                    summary_prompt}. The three opt rows drive
//                                    submitJob(auth, url, opts); the custom
//                                    prompt row reveals a textarea when on.
//   ~1253 #cap-go .bigbtn         → primary submit button
//   ~1280 submit() prototype mock → real submitJob(auth, url, opts) → POST /jobs
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
//     browser-native dialog primitives; see check-forbidden-primitives.sh.
//
// The sheet uses the `.sheet-layer / .sheet / .sheet-bg / .grabber /
// .sheet-hd / .sheet-body` recipe shipped in Wave 0 (verified against
// `Scribe iOS.html` lines ~420-468). The `.url-field / .detected /
// .bigbtn` recipes ship in this wave as a CSS append (also a verbatim
// port of the iOS source, with literal hex replaced by tokens).

import React from "react";
import { submitJob, submitUploadJob } from "../api-jobs.js";
import { parseVideoUrl } from "../command-utils.js";
import { IconCheck, IconLink, IconPlus, IconWave } from "../icons.jsx";

// iOS-style switch (port of the `.toggle` recipe used by the mobile settings
// rows). Drives the boolean Capture toggles below.
function Toggle({ on, onClick, ariaLabel }) {
	return (
		<button
			type="button"
			className={on ? "toggle on" : "toggle"}
			aria-pressed={on}
			aria-label={ariaLabel}
			onClick={onClick}
		/>
	);
}

export function CaptureSheet({ open, onClose, auth, navigateDesign }) {
	const [url, setUrl] = React.useState("");
	const [submitting, setSubmitting] = React.useState(false);
	const [error, setError] = React.useState(null);
	const [toast, setToast] = React.useState(null);
	// Per-job Capture toggles (#296). Defaults mirror POST /jobs defaults:
	// summarize on, notify on, no custom prompt.
	const [summarize, setSummarize] = React.useState(true);
	const [notify, setNotify] = React.useState(true);
	const [promptOn, setPromptOn] = React.useState(false);
	const [summaryPrompt, setSummaryPrompt] = React.useState("");
	// Upload-your-own-video (#408): a selected local file takes precedence over
	// the URL field and submits via POST /jobs/upload instead of POST /jobs.
	const [file, setFile] = React.useState(null);
	const inputRef = React.useRef(null);
	const fileInputRef = React.useRef(null);
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
		setSummarize(true);
		setNotify(true);
		setPromptOn(false);
		setSummaryPrompt("");
		setFile(null);
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
	// A selected file OR a valid URL is submittable; the file path wins.
	const valid = (Boolean(file) || Boolean(videoId)) && !submitting;

	const onPickFile = React.useCallback((event) => {
		const picked = event.target.files?.[0];
		if (picked) {
			setFile(picked);
			// A file supersedes a typed URL — clear it so intent is unambiguous.
			setUrl("");
			setError(null);
		}
	}, []);

	const clearFile = React.useCallback(() => {
		setFile(null);
		if (fileInputRef.current) fileInputRef.current.value = "";
	}, []);

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
		if (!valid) return;
		if (!file && !parsed) return;
		setSubmitting(true);
		setError(null);
		try {
			const opts = {
				summarize,
				notify,
				// Only send the custom prompt when the row is toggled on; an
				// empty/whitespace string falls back to the active template.
				summaryPrompt: promptOn ? summaryPrompt : "",
			};
			const result = file
				? await submitUploadJob(auth, file, opts)
				: await submitJob(auth, parsed.url, opts);
			close();
			// Navigate after the sheet finishes sliding out so the page
			// transition runs against a settled chrome (mirrors the
			// prototype's `setTimeout(..., 280)` after closeSheet()).
			setTimeout(() => {
				navigateDesign("job", { id: result.job_id });
				flashToast(file ? "Uploading…" : "Added to queue");
			}, 280);
		} catch (err) {
			setSubmitting(false);
			setError(err instanceof Error ? err.message : String(err));
		}
	}, [
		valid,
		parsed,
		file,
		auth,
		navigateDesign,
		close,
		flashToast,
		summarize,
		notify,
		promptOn,
		summaryPrompt,
	]);

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
									onChange={(event) => {
										setUrl(event.target.value);
										if (file) clearFile();
									}}
									onKeyDown={onKey}
								/>
							</label>

							{/* Upload-your-own-video (#408): attach a local file
							    instead of a URL. The label opens the hidden file
							    input; a picked file supersedes the URL field. */}
							<label className="url-field">
								<IconPlus size={18} />
								<span style={{ flex: 1, minWidth: 0, color: "var(--muted)" }}>
									{file ? file.name : "Or upload a video / audio file"}
								</span>
								<input
									ref={fileInputRef}
									type="file"
									accept="video/*,audio/*"
									onChange={onPickFile}
									style={{ display: "none" }}
								/>
							</label>

							{file ? (
								<div className="detected">
									<div className="thumb" aria-hidden="true">
										<IconWave size={20} />
									</div>
									<div style={{ minWidth: 0, flex: 1 }}>
										<div className="d-title">{file.name}</div>
										<div className="d-meta">
											{Math.round(file.size / 1024)} KB · upload
										</div>
									</div>
									<button
										type="button"
										className="s-cancel"
										onClick={clearFile}
										disabled={submitting}
									>
										Remove
									</button>
								</div>
							) : videoId ? (
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

							<div className="opt-row">
								<span className="o-label">
									Summarize
									<small>AI summary after transcription</small>
								</span>
								<Toggle
									on={summarize}
									onClick={() => setSummarize((value) => !value)}
									ariaLabel="Toggle AI summary"
								/>
							</div>

							<div className="opt-row">
								<span className="o-label">
									Notify when done
									<small>Push the result to your callback on done/failed</small>
								</span>
								<Toggle
									on={notify}
									onClick={() => setNotify((value) => !value)}
									ariaLabel="Toggle notify when done"
								/>
							</div>

							<div className="opt-row">
								<span className="o-label">
									Custom summary prompt
									<small>Override the default prompt for this job</small>
								</span>
								<Toggle
									on={promptOn}
									onClick={() => setPromptOn((value) => !value)}
									ariaLabel="Toggle custom summary prompt"
								/>
							</div>

							{promptOn ? (
								<textarea
									className="cap-prompt"
									placeholder="Summarize the key points as concise bullets…"
									value={summaryPrompt}
									onChange={(event) => setSummaryPrompt(event.target.value)}
									rows={4}
								/>
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
								{submitting
									? file
										? "Uploading…"
										: "Adding to queue…"
									: file
										? "Upload file"
										: "Add to queue"}
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
