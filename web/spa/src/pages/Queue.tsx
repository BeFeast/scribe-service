import React from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { type FailureJob, FailureRow } from "../components/FailureRow";
import { JobCard, type JobCardJob } from "../components/JobCard";
import { CMDK_OPEN_EVENT } from "../constants";
import { useAuth } from "../hooks/useAuth";
import { usePoll } from "../hooks/usePoll";
import type { Route } from "../hooks/useRoute";

type ActiveJobsResponse = {
	jobs: JobCardJob[];
};

type RecentFailuresResponse = {
	jobs: FailureJob[];
};

type QueueProps = {
	navigate: (route: Route) => void;
};

async function readErrorMessage(response: Response): Promise<string> {
	try {
		const body = await response.json();
		if (typeof body?.detail === "string") {
			return body.detail;
		}
		if (Array.isArray(body?.detail)) {
			return body.detail
				.map((entry: { msg?: string }) =>
					typeof entry?.msg === "string" ? entry.msg : JSON.stringify(entry),
				)
				.join("; ");
		}
	} catch {
		// fall through to the status line
	}
	return `HTTP ${response.status} ${response.statusText}`.trim();
}

export function Queue({ navigate }: QueueProps) {
	const auth = useAuth();
	const [active, setActive] = React.useState<JobCardJob[]>([]);
	const [failures, setFailures] = React.useState<FailureJob[]>([]);
	const [error, setError] = React.useState<string | null>(null);
	const [loading, setLoading] = React.useState(true);
	const [clearingId, setClearingId] = React.useState<number | null>(null);
	const [cancelCandidate, setCancelCandidate] =
		React.useState<JobCardJob | null>(null);
	const [cancelBusyId, setCancelBusyId] = React.useState<number | null>(null);
	// IDs cancelled locally but still possibly returned by an in-flight or
	// lagging /api/jobs/active poll. Filter them out until the backend drops
	// them from the active list, then forget them.
	const cancelledIdsRef = React.useRef<Set<number>>(new Set());

	const load = React.useCallback(
		async (signal: AbortSignal) => {
			try {
				const [activeResponse, failuresResponse] = await Promise.all([
					auth.protectedFetch("/api/jobs/active", { signal }),
					auth.protectedFetch("/api/jobs/recent-failures?limit=5", { signal }),
				]);
				if (!activeResponse.ok || !failuresResponse.ok) {
					throw new Error("queue endpoints unavailable");
				}
				const activeBody = (await activeResponse.json()) as ActiveJobsResponse;
				const failuresBody =
					(await failuresResponse.json()) as RecentFailuresResponse;
				const fetchedActive = activeBody.jobs ?? [];
				const cancelled = cancelledIdsRef.current;
				if (cancelled.size > 0) {
					const next = new Set<number>();
					for (const id of cancelled) {
						if (fetchedActive.some((job) => job.id === id)) {
							next.add(id);
						}
					}
					cancelledIdsRef.current = next;
				}
				setActive(
					fetchedActive.filter((job) => !cancelledIdsRef.current.has(job.id)),
				);
				setFailures(failuresBody.jobs ?? []);
				setError(null);
				setLoading(false);
			} catch (queueError) {
				if (!signal.aborted) {
					setError(
						queueError instanceof Error
							? queueError.message
							: "queue load failed",
					);
					setLoading(false);
				}
			}
		},
		[auth],
	);

	usePoll(load, 2000);

	const clearFailure = React.useCallback(
		async (id: number) => {
			if (clearingId !== null) {
				return;
			}
			setClearingId(id);
			setError(null);
			try {
				const response = await auth.protectedFetch(`/admin/jobs/${id}`, {
					method: "DELETE",
				});
				if (!response.ok) {
					throw new Error(`clear failed: ${response.status}`);
				}
				setFailures((current) => current.filter((job) => job.id !== id));
			} catch (clearError) {
				setError(
					clearError instanceof Error ? clearError.message : "clear failed",
				);
			} finally {
				setClearingId(null);
			}
		},
		[auth, clearingId],
	);

	const requestCancel = React.useCallback(
		(id: number) => {
			if (cancelBusyId !== null) {
				return;
			}
			const job = active.find((candidate) => candidate.id === id) ?? null;
			if (job === null) {
				return;
			}
			setError(null);
			setCancelCandidate(job);
		},
		[active, cancelBusyId],
	);

	const confirmCancel = React.useCallback(async () => {
		if (cancelCandidate === null || cancelBusyId !== null) {
			return;
		}
		const job = cancelCandidate;
		setCancelBusyId(job.id);
		setError(null);
		try {
			const response = await auth.protectedFetch(
				`/admin/jobs/${job.id}/cancel`,
				{ method: "POST" },
			);
			if (!response.ok) {
				const message = await readErrorMessage(response);
				throw new Error(message);
			}
			cancelledIdsRef.current.add(job.id);
			setActive((current) => current.filter((entry) => entry.id !== job.id));
		} catch (cancelError) {
			setError(
				cancelError instanceof Error ? cancelError.message : "cancel failed",
			);
		} finally {
			setCancelBusyId(null);
			setCancelCandidate(null);
		}
	}, [auth, cancelBusyId, cancelCandidate]);

	return (
		<section className="pane queue-page">
			<header className="pane-header">
				<div>
					<h1 className="pane-h1">Queue</h1>
					<div className="pane-sub">
						{active.length} in flight &middot;{" "}
						<span className="live-dot" aria-hidden="true" /> live
					</div>
				</div>
				<div className="pane-actions">
					<button
						type="button"
						className="btn"
						onClick={() => void load(new AbortController().signal)}
					>
						Poll now
					</button>
					<button
						type="button"
						className="btn primary"
						onClick={() =>
							document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT))
						}
					>
						Submit URL
					</button>
				</div>
			</header>

			{error ? (
				<p className="error-banner" data-testid="queue-error">
					{error}
				</p>
			) : null}

			<section className="queue-section">
				{loading ? <p className="muted">Loading queue...</p> : null}
				{!loading && active.length === 0 ? (
					<div className="empty-queue">
						<div>
							<strong>Queue is idle</strong>
							<span>No active jobs are currently in flight.</span>
						</div>
					</div>
				) : (
					<div className="job-card-list">
						{active.map((job) => (
							<JobCard
								key={job.id}
								job={job}
								navigate={navigate}
								onCancel={requestCancel}
								cancelBusy={cancelBusyId === job.id}
								cancelDisabled={
									cancelBusyId !== null && cancelBusyId !== job.id
								}
							/>
						))}
					</div>
				)}
			</section>

			<section className="queue-section">
				<div className="section-label split">
					<span>Recent terminal jobs &middot; failed</span>
					<button
						type="button"
						className="text-link"
						onClick={() => navigate({ page: "ops", params: {} })}
					>
						all failures
					</button>
				</div>
				{failures.length > 0 ? (
					<div className="failure-list">
						{failures.map((job) => (
							<FailureRow
								key={job.id}
								job={job}
								navigate={navigate}
								onDismiss={clearFailure}
								busy={clearingId === job.id}
							/>
						))}
					</div>
				) : (
					<p className="muted">No recent failures</p>
				)}
			</section>

			{cancelCandidate !== null ? (
				<ConfirmDialog
					title="Cancel job"
					body={`Cancel job ${cancelCandidate.id} (${cancelCandidate.title ?? cancelCandidate.video_id})? The current pipeline stage may still finish, but the job will be marked failed.`}
					confirmLabel="Cancel job"
					busyLabel="Cancelling"
					busy={cancelBusyId === cancelCandidate.id}
					onCancel={() => setCancelCandidate(null)}
					onConfirm={confirmCancel}
				/>
			) : null}
		</section>
	);
}
