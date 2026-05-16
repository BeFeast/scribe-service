import React from "react";

import { LogTail } from "../components/LogTail";
import { PipelineDiagram, type StageMap } from "../components/PipelineDiagram";
import { StatusChip } from "../components/StatusChip";

type JobView = {
	job_id: number;
	url: string;
	video_id: string;
	status: string;
	error?: string | null;
	callback_url?: string | null;
	transcript?: {
		id: number;
		title: string;
		summary_shortlink?: string | null;
		transcript_shortlink?: string | null;
	} | null;
	stages?: StageMap | null;
};

const EMPTY_STAGES: StageMap = {
	queued: { state: "pending" },
	downloading: { state: "pending" },
	transcribing: { state: "pending" },
	summarizing: { state: "pending" },
	done: { state: "pending" },
};

function isTerminal(status: string): boolean {
	return status === "done" || status === "failed";
}

async function postJobAction(path: string): Promise<JobView> {
	const response = await fetch(path, { method: "POST" });
	if (!response.ok) {
		const text = await response.text();
		throw new Error(text || `request failed: ${response.status}`);
	}
	return (await response.json()) as JobView;
}

export function JobDetailPage({
	id,
	navigateToJob,
}: {
	id?: number;
	navigateToJob: (id: number) => void;
}) {
	const [job, setJob] = React.useState<JobView | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [busy, setBusy] = React.useState<string | null>(null);

	const load = React.useCallback(
		async (signal?: AbortSignal) => {
			if (id === undefined) {
				return;
			}
			const response = await fetch(`/jobs/${id}`, { signal });
			if (!response.ok) {
				throw new Error(`job request failed: ${response.status}`);
			}
			const body = (await response.json()) as JobView;
			setJob(body);
			setError(null);
		},
		[id],
	);

	React.useEffect(() => {
		const abort = new AbortController();
		void load(abort.signal).catch((err: unknown) => {
			if (!abort.signal.aborted) {
				setError(err instanceof Error ? err.message : "job request failed");
			}
		});
		const timer = window.setInterval(() => {
			if (job === null || isTerminal(job.status)) {
				return;
			}
			void load().catch((err: unknown) => {
				setError(err instanceof Error ? err.message : "job request failed");
			});
		}, 2000);
		return () => {
			abort.abort();
			window.clearInterval(timer);
		};
	}, [job, load]);

	if (id === undefined) {
		return (
			<section className="pane-narrow">
				<h1 className="pane-h1">Job not found</h1>
				<p className="pane-sub">Missing job id.</p>
			</section>
		);
	}

	const stages = job?.stages ?? EMPTY_STAGES;

	async function cancelJob() {
		if (!job) {
			return;
		}
		setBusy("cancel");
		try {
			const next = await postJobAction(`/admin/jobs/${job.job_id}/cancel`);
			setJob(next);
			setError(null);
		} catch (err) {
			setError(err instanceof Error ? err.message : "cancel failed");
		} finally {
			setBusy(null);
		}
	}

	async function retryJob() {
		if (!job) {
			return;
		}
		setBusy("retry");
		try {
			const next = await postJobAction(`/admin/jobs/${job.job_id}/retry`);
			navigateToJob(next.job_id);
		} catch (err) {
			setError(err instanceof Error ? err.message : "retry failed");
		} finally {
			setBusy(null);
		}
	}

	async function copyJson() {
		if (!job) {
			return;
		}
		await navigator.clipboard.writeText(JSON.stringify(job, null, 2));
	}

	return (
		<section className="pane job-detail-page">
			<header className="pane-header">
				<div>
					<p className="eyebrow">Job detail</p>
					<h1 className="detail-h1">
						{job?.transcript?.title ?? job?.video_id ?? `job ${id}`}
					</h1>
					<p className="detail-meta">
						<span className="mono">job {id}</span>
						{job ? <span>{job.url}</span> : null}
					</p>
				</div>
				<div className="action-row">
					{job ? (
						<StatusChip status={job.status} />
					) : (
						<span className="chip info">loading</span>
					)}
					<button
						type="button"
						className="btn ghost"
						onClick={cancelJob}
						disabled={!job || isTerminal(job.status) || busy !== null}
					>
						Cancel
					</button>
					<button
						type="button"
						className="btn ghost"
						onClick={retryJob}
						disabled={!job || !isTerminal(job.status) || busy !== null}
					>
						Retry
					</button>
					<button
						type="button"
						className="btn ghost"
						onClick={copyJson}
						disabled={!job}
					>
						Copy JSON
					</button>
					<a
						className="btn ghost"
						href="/metrics"
						target="_blank"
						rel="noreferrer"
					>
						Open in Prometheus
					</a>
				</div>
			</header>

			{error ? <p className="failure-row err-msg">{error}</p> : null}

			<section className="queue-section">
				<h2 className="section-label">Pipeline</h2>
				<PipelineDiagram stages={stages} />
			</section>

			<section className="queue-section">
				<h2 className="section-label">Log tail</h2>
				<LogTail
					jobId={job?.job_id ?? id}
					status={job?.status ?? "queued"}
					error={job?.error}
					stages={stages}
				/>
			</section>
		</section>
	);
}
