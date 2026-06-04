// Mobile Job detail — Wave 2c / Issue #278
//
// Literal port of `viewJob(id)` from `Scribe iOS.html` (~lines 1059-1110).
// Renders the per-job detail page: heading + meta line + 5-stage pipeline
// rails + "Cancel job" big button. The pipeline visualisation reuses the
// `.pstage` / `.rail` / `.pbody` recipes ported into the mobile @media
// block in styles.css.
//
// Real-data wiring (HARD rule, AGENTS.md):
//   - Reads CURRENT_JOB / CURRENT_JOB_STATE / ACTIVE_JOBS from ./data.js
//     (populated by useScribeRuntime in main.jsx). No fabricated stages,
//     no hardcoded prototype demo data.
//   - Cancel calls onCancelJob(id) which is wired to runtime.cancelJob
//     (real POST /admin/jobs/:id/cancel). Outcome surfaces inline via
//     the `.toast` recipe defined in Wave 0.
//
// Translation contract:
//   - Vanilla JS .onclick → React onClick. The prototype's
//     toast("Job cancel requested", I.warn(15)) becomes a real round-trip
//     to the backend, with success/error reflected in the toast copy.
//   - The iOS source's "spinner / ✓ check / muted dot" rail node mapping
//     is preserved, sourced from icons.jsx (IconCheck) plus a 7px dot.
//
// AGENTS.md anti-patterns avoided:
//   - No window.alert/confirm/prompt — the .toast recipe is the SPA
//     primitive for transient feedback, ConfirmDialog stays on desktop.
//   - No setInterval fake progress nudge from the prototype — the
//     real polling lives in useScribeRuntime (every 2s while in flight).

import React from "react";
import {
	ACTIVE_JOBS,
	CURRENT_JOB,
	CURRENT_JOB_STATE,
	fmtElapsed,
} from "../data.js";
import { IconCheck } from "../icons.jsx";

const STAGE_ORDER = [
	["queued", "Queued"],
	["downloading", "Downloading"],
	["transcribing", "Transcribing"],
	["summarizing", "Summarizing"],
	["done", "Done"],
];

export function MobileJobDetail({
	id,
	navigate,
	log: _log,
	onRefresh: _onRefresh,
	onCancelJob,
	onRetryJob,
	onDeleteJob: _onDeleteJob,
}) {
	const job =
		CURRENT_JOB && CURRENT_JOB.id === id
			? CURRENT_JOB
			: ACTIVE_JOBS.find((row) => row.id === id) || CURRENT_JOB;
	const [toast, setToast] = React.useState(null);
	const [pending, setPending] = React.useState(null);
	const actionAbortRef = React.useRef(null);
	React.useEffect(() => () => actionAbortRef.current?.abort(), []);

	const showToast = React.useCallback((kind, message) => {
		setToast({ kind, message });
	}, []);

	React.useEffect(() => {
		if (!toast) return undefined;
		const t = window.setTimeout(() => setToast(null), 2400);
		return () => window.clearTimeout(t);
	}, [toast]);

	const cancel = React.useCallback(async () => {
		if (!job || !onCancelJob || pending) return;
		actionAbortRef.current?.abort();
		const controller = new AbortController();
		actionAbortRef.current = controller;
		setPending("cancel");
		try {
			await onCancelJob(job.id, controller.signal);
			if (!controller.signal.aborted) {
				showToast("ok", "Job cancel requested");
			}
		} catch (error) {
			if (!controller.signal.aborted) {
				const message = error instanceof Error ? error.message : String(error);
				showToast("err", `Cancel failed · ${message}`);
			}
		} finally {
			if (actionAbortRef.current === controller) actionAbortRef.current = null;
			if (!controller.signal.aborted) setPending(null);
		}
	}, [job, onCancelJob, pending, showToast]);

	const retry = React.useCallback(async () => {
		if (!job || !onRetryJob || pending) return;
		actionAbortRef.current?.abort();
		const controller = new AbortController();
		actionAbortRef.current = controller;
		setPending("retry");
		try {
			await onRetryJob(job.id, controller.signal);
			if (!controller.signal.aborted) {
				showToast("ok", "Retry queued");
			}
		} catch (error) {
			if (!controller.signal.aborted) {
				const message = error instanceof Error ? error.message : String(error);
				showToast("err", `Retry failed · ${message}`);
			}
		} finally {
			if (actionAbortRef.current === controller) actionAbortRef.current = null;
			if (!controller.signal.aborted) setPending(null);
		}
	}, [job, onRetryJob, pending, showToast]);

	if (CURRENT_JOB_STATE.loading && !job) {
		return (
			<div className="empty">
				<div className="empty-title">Loading job</div>
				<div>Fetching /jobs/{id}.</div>
			</div>
		);
	}
	if (!job || CURRENT_JOB_STATE.error) {
		return (
			<div className="empty">
				<div className="empty-title">Job unavailable</div>
				<div>{CURRENT_JOB_STATE.error || "No job is loaded."}</div>
			</div>
		);
	}

	const inFlight = job.status !== "done" && job.status !== "failed";
	const isFailed = job.status === "failed";

	return (
		<>
			<div className="detail-head">
				<h1 className="detail-title">{job.title}</h1>
				<div className="detail-meta">
					<span className="chip run">
						<span className="live-dot" />
						{job.status}
					</span>
					<span>{job.source}</span>
					<span className="sep">·</span>
					<span className="m-detail-url">{job.url}</span>
					<span className="sep">·</span>
					<span>{fmtElapsed(job.elapsed_s)} elapsed</span>
				</div>
			</div>
			<div className="sec-label">Pipeline</div>
			<div className="pipe">
				{STAGE_ORDER.map(([k, label], i) => {
					const stage = job.stages?.[k] || { state: "pending" };
					const cls =
						stage.state === "done"
							? "done"
							: stage.state === "active"
								? "active"
								: "pending";
					return (
						<div key={k} className={`pstage ${cls}`}>
							<div className="rail">
								<div className="node">
									{stage.state === "done" ? (
										<IconCheck size={14} />
									) : stage.state === "active" ? (
										<span className="spinner" />
									) : (
										<span className="m-rail-dot" />
									)}
								</div>
								{i < STAGE_ORDER.length - 1 ? <div className="line" /> : null}
							</div>
							<div className="pbody">
								<div className="pname">
									{label}
									{stage.state === "active" ? (
										<span className="chip run m-stage-live">
											<span className="live-dot" />
											live
										</span>
									) : null}
								</div>
								{stage.note ? <div className="pnote">{stage.note}</div> : null}
								{stage.state === "active" && stage.progress != null ? (
									<div className="pbar">
										<div
											style={{
												width: `${Math.round(stage.progress * 100)}%`,
											}}
										/>
									</div>
								) : null}
							</div>
						</div>
					);
				})}
			</div>
			{isFailed && job.error ? (
				<>
					<div className="sec-label">Failure</div>
					<pre className="m-job-error">{job.error}</pre>
				</>
			) : null}
			<div className="m-cancel-wrap">
				{isFailed ? (
					<button
						type="button"
						className="bigbtn"
						onClick={retry}
						disabled={pending !== null || !onRetryJob}
					>
						{pending === "retry" ? "Retrying…" : "Retry job"}
					</button>
				) : (
					<button
						type="button"
						className="bigbtn sec"
						onClick={cancel}
						disabled={!inFlight || pending !== null || !onCancelJob}
					>
						{pending === "cancel" ? "Cancelling…" : "Cancel job"}
					</button>
				)}
			</div>
			{toast ? (
				<output
					className={`toast show${toast.kind === "err" ? " m-toast-err" : ""}`}
				>
					<span>{toast.message}</span>
				</output>
			) : null}
		</>
	);
}
