// Mobile Queue — Wave 2c / Issue #278
//
// Literal port of `viewQueue()` from `Scribe iOS.html` (~lines 1018-1058).
// Renders the active-jobs list as `.jcard` rows with a status chip,
// per-job pipeline progress bar, and a "tap → push job detail" affordance.
// Empty state ports the `class="empty"` recipe verbatim with the
// "Queue is clear" copy from the iOS source.
//
// Real-data wiring (HARD rule, AGENTS.md):
//   - Reads ACTIVE_JOBS / STATS from ./data.js (already populated by
//     useScribeRuntime → setRuntimeData in main.jsx). No fabricated
//     stages, no setInterval fake progress nudge from the prototype.
//   - Tap on a row calls navigate("job", { id }) — the same hash-route
//     the desktop JobCard uses, so MobileJobDetail picks it up.
//   - The "+" affordance routes to navigate(null, { openCmdk: true }),
//     matching the Wave 1 capture-orb placeholder until the Wave 2f
//     CaptureSheet lands.
//
// Translation contract:
//   - Vanilla JS innerHTML / .onclick → JSX onClick handlers.
//   - The wire(page) hook becomes plain React event props.
//   - Inline iOS hex are replaced with var(--*) tokens via the .jcard
//     recipe in styles.css; the markup stays a 1:1 port.

import React from "react";
import { ACTIVE_JOBS, STATS, fmtElapsed } from "../data.js";
import { IconPlus } from "../icons.jsx";

/* Stage order — verbatim port of `stageMeta()` (Scribe iOS.html ~line
   1015). Kept inside this module rather than imported from job-pages.jsx
   so the byte-locked desktop port and the mobile port stay independent. */
const STAGE_ORDER = [
	["queued", "Queued"],
	["downloading", "Downloading"],
	["transcribing", "Transcribing"],
	["summarizing", "Summarizing"],
	["done", "Done"],
];

function activeStage(job) {
	const idx = STAGE_ORDER.findIndex(
		([k]) => job.stages?.[k]?.state === "active",
	);
	return idx >= 0 ? idx : STAGE_ORDER.length - 1;
}

function pipelineFraction(job) {
	const idx = activeStage(job);
	const cur = STAGE_ORDER[idx];
	const curStage = job.stages?.[cur[0]] || {};
	const done = STAGE_ORDER.filter(
		([k]) => job.stages?.[k]?.state === "done",
	).length;
	return (done + (curStage.progress || 0)) / STAGE_ORDER.length;
}

export function MobileQueue({
	navigate,
	loading,
	error,
	onRefresh: _onRefresh,
	onRetryJob: _onRetryJob,
	onDeleteJob: _onDeleteJob,
}) {
	// Eagerly read the live module-level snapshot so re-renders triggered
	// by main.jsx's setRuntimeData() pick up the latest ACTIVE_JOBS.
	const jobs = ACTIVE_JOBS;
	const workers = STATS?.worker_pool;

	const openCapture = React.useCallback(() => {
		navigate(null, { openCmdk: true });
	}, [navigate]);

	if (loading && jobs.length === 0) {
		return (
			<div className="empty">
				<div className="empty-title">Loading queue</div>
				<div>Fetching active jobs.</div>
			</div>
		);
	}
	if (error) {
		return (
			<div className="empty">
				<div className="empty-title">Queue unavailable</div>
				<div>{error}</div>
			</div>
		);
	}

	if (jobs.length === 0) {
		return (
			<>
				<div className="m-queue-actions">
					<button
						type="button"
						className="nb-btn"
						aria-label="Submit URL"
						onClick={openCapture}
					>
						<IconPlus size={20} />
					</button>
				</div>
				<div className="empty">
					<div className="empty-title">Queue is clear</div>
					<div>No jobs in flight. Paste a URL to start one.</div>
				</div>
			</>
		);
	}

	return (
		<>
			<div className="m-queue-actions">
				{workers ? (
					<span className="m-queue-workers">
						{workers.active}/{workers.total} workers busy
					</span>
				) : null}
				<span style={{ flex: 1 }} />
				<button
					type="button"
					className="nb-btn"
					aria-label="Submit URL"
					onClick={openCapture}
				>
					<IconPlus size={20} />
				</button>
			</div>
			<div className="m-queue-list">
				{jobs.map((job) => (
					<MobileQueueCard key={job.id} job={job} navigate={navigate} />
				))}
			</div>
		</>
	);
}

function MobileQueueCard({ job, navigate }) {
	const idx = activeStage(job);
	const cur = STAGE_ORDER[idx];
	const curStage = job.stages?.[cur[0]] || {};
	const done = STAGE_ORDER.filter(
		([k]) => job.stages?.[k]?.state === "done",
	).length;
	const frac = pipelineFraction(job);
	const isQueued = job.status === "queued";
	const open = () => navigate("job", { id: job.id });

	return (
		<button type="button" className="jcard" data-id={job.id} onClick={open}>
			<div className="j-top">
				{isQueued ? (
					<span className="chip muted">
						<span className="dot" />
						queued
					</span>
				) : (
					<span className="chip run">
						<span className="live-dot" />
						{job.status}
					</span>
				)}
				<span className="j-src">{job.source}</span>
				<span style={{ flex: 1 }} />
				<span className="j-src">{fmtElapsed(job.elapsed_s)}</span>
			</div>
			<div className="j-title">{job.title}</div>
			<div className="j-src">{job.url}</div>
			<div className="j-bar">
				<div style={{ width: `${Math.round(frac * 100)}%` }} />
			</div>
			<div className="j-stagetxt">
				<span>
					{cur[1]}
					{curStage.progress
						? ` · ${Math.round(curStage.progress * 100)}%`
						: ""}
				</span>
				<span>
					{done}/{STAGE_ORDER.length} stages
				</span>
			</div>
		</button>
	);
}
