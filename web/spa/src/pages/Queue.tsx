import React from "react";

import { FailureRow } from "../components/FailureRow";
import { type ActiveJob, JobCard } from "../components/JobCard";

type ActiveJobsResponse = {
	jobs: ActiveJob[];
};

export function QueuePage({
	navigateToJob,
}: {
	navigateToJob: (id: number) => void;
}) {
	const [jobs, setJobs] = React.useState<ActiveJob[]>([]);
	const recentFailures: Array<{
		id: number;
		videoId: string;
		error?: string | null;
	}> = [];
	const [error, setError] = React.useState<string | null>(null);
	const [loading, setLoading] = React.useState(true);

	const load = React.useCallback(async (signal?: AbortSignal) => {
		const response = await fetch("/api/jobs/active", { signal });
		if (!response.ok) {
			throw new Error(`active jobs request failed: ${response.status}`);
		}
		const body = (await response.json()) as ActiveJobsResponse;
		setJobs(body.jobs ?? []);
		setError(null);
		setLoading(false);
	}, []);

	React.useEffect(() => {
		const abort = new AbortController();
		void load(abort.signal).catch((err: unknown) => {
			if (!abort.signal.aborted) {
				setError(err instanceof Error ? err.message : "queue request failed");
				setLoading(false);
			}
		});
		const timer = window.setInterval(() => {
			void load().catch((err: unknown) => {
				setError(err instanceof Error ? err.message : "queue request failed");
			});
		}, 2000);
		return () => {
			abort.abort();
			window.clearInterval(timer);
		};
	}, [load]);

	return (
		<section className="pane queue-page">
			<header className="pane-header">
				<div>
					<p className="eyebrow">Queue</p>
					<h1 className="pane-h1">Pipeline jobs</h1>
					<p className="pane-sub">
						In-flight YouTube ingestion and summary work.
					</p>
				</div>
				<span className="chip info">
					{loading ? "loading" : `${jobs.length} active`}
				</span>
			</header>

			{error ? <p className="failure-row err-msg">{error}</p> : null}

			<section className="queue-section">
				<h2 className="section-label">Active jobs</h2>
				{jobs.length > 0 ? (
					<div className="job-list">
						{jobs.map((job) => (
							<JobCard key={job.id} job={job} onOpen={navigateToJob} />
						))}
					</div>
				) : (
					<div className="empty-state">queue is clear · scribe is idle</div>
				)}
			</section>

			<section className="queue-section">
				<h2 className="section-label">Recent failures</h2>
				{recentFailures.length > 0 ? (
					<div className="job-list">
						{recentFailures.map((failure) => (
							<FailureRow
								key={failure.id}
								id={failure.id}
								videoId={failure.videoId}
								error={failure.error}
								onRetry={navigateToJob}
							/>
						))}
					</div>
				) : (
					<div className="empty-state">No recent failures</div>
				)}
			</section>
		</section>
	);
}
