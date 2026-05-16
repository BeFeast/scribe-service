import React from "react";

import { type FailureJob, FailureRow } from "../components/FailureRow";
import { JobCard, type JobCardJob } from "../components/JobCard";
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

export function Queue({ navigate }: QueueProps) {
	const [active, setActive] = React.useState<JobCardJob[]>([]);
	const [failures, setFailures] = React.useState<FailureJob[]>([]);
	const [error, setError] = React.useState<string | null>(null);
	const [loading, setLoading] = React.useState(true);

	React.useEffect(() => {
		let mounted = true;
		let timer: number | undefined;

		async function load() {
			try {
				const [activeResponse, failuresResponse] = await Promise.all([
					fetch("/api/jobs/active"),
					fetch("/api/jobs/recent-failures?limit=5"),
				]);
				if (!activeResponse.ok || !failuresResponse.ok) {
					throw new Error("queue endpoints unavailable");
				}
				const activeBody = (await activeResponse.json()) as ActiveJobsResponse;
				const failuresBody =
					(await failuresResponse.json()) as RecentFailuresResponse;
				if (!mounted) {
					return;
				}
				setActive(activeBody.jobs ?? []);
				setFailures(failuresBody.jobs ?? []);
				setError(null);
				setLoading(false);
			} catch (queueError) {
				if (!mounted) {
					return;
				}
				setError(
					queueError instanceof Error
						? queueError.message
						: "queue load failed",
				);
				setLoading(false);
			} finally {
				if (mounted) {
					timer = window.setTimeout(load, 2000);
				}
			}
		}

		void load();
		return () => {
			mounted = false;
			window.clearTimeout(timer);
		};
	}, []);

	const openJob = React.useCallback(
		(id: number) => navigate({ page: "job", params: { id } }),
		[navigate],
	);

	return (
		<section className="pane queue-page">
			<header className="pane-header">
				<div>
					<p className="eyebrow">Queue</p>
					<h1 className="pane-h1">Active jobs</h1>
				</div>
				<span className="queue-count">{active.length}</span>
			</header>

			{error ? <p className="error-banner">{error}</p> : null}

			<section className="queue-section">
				{loading ? <p className="muted">Loading queue...</p> : null}
				{!loading && active.length === 0 ? (
					<div className="empty-queue">
						queue is clear &middot; scribe is idle
					</div>
				) : (
					<div className="job-card-list">
						{active.map((job) => (
							<JobCard key={job.id} job={job} onOpen={openJob} />
						))}
					</div>
				)}
			</section>

			<section className="queue-section">
				<div className="section-heading">
					<h2>Recent failures</h2>
				</div>
				{failures.length > 0 ? (
					<div className="failure-list">
						{failures.map((job) => (
							<FailureRow key={job.id} job={job} onOpen={openJob} />
						))}
					</div>
				) : (
					<p className="muted">No recent failures</p>
				)}
			</section>
		</section>
	);
}
