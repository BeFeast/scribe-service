import React from "react";

import { LogTail } from "../components/LogTail";
import { PipelineDiagram, type StageMap } from "../components/PipelineDiagram";
import { StatusChip } from "../components/StatusChip";
import type { Route } from "../hooks/useRoute";
import { usePoll } from "../hooks/usePoll";

type TranscriptBrief = {
	id: number;
	title: string;
	video_id: string;
};

type JobDetailPayload = {
	job_id: number;
	url: string;
	video_id: string;
	status: string;
	error?: string | null;
	callback_url?: string | null;
	transcript?: TranscriptBrief | null;
	started_at?: string | null;
	elapsed_s?: number | null;
	stages?: StageMap | null;
};

type JobDetailProps = {
	id?: number;
	navigate: (route: Route) => void;
};

const TERMINAL = new Set(["done", "failed"]);

function formatElapsed(seconds?: number | null) {
	if (seconds === null || seconds === undefined) {
		return "0s";
	}
	const minutes = Math.floor(seconds / 60);
	const rest = seconds % 60;
	return minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
}

export function JobDetail({ id, navigate }: JobDetailProps) {
	const [job, setJob] = React.useState<JobDetailPayload | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [busy, setBusy] = React.useState<string | null>(null);
	const [copied, setCopied] = React.useState(false);
	const isTerminal = job !== null && TERMINAL.has(job.status);

	const load = React.useCallback(async (signal: AbortSignal) => {
		if (id === undefined) {
			return;
		}
		try {
			const response = await fetch(`/jobs/${id}`, { signal });
			if (!response.ok) {
				throw new Error(`job ${id} returned ${response.status}`);
			}
			const body = (await response.json()) as JobDetailPayload;
			setJob(body);
			setError(null);
		} catch (jobError) {
			if (!signal.aborted) {
				setError(
					jobError instanceof Error ? jobError.message : "job load failed",
				);
			}
		}
	}, [id]);

	usePoll(load, 2000, { enabled: id !== undefined && !isTerminal });

	async function cancelJob() {
		if (!job) {
			return;
		}
		setBusy("cancel");
		try {
			const response = await fetch(`/admin/jobs/${job.job_id}/cancel`, {
				method: "POST",
			});
			if (!response.ok) {
				throw new Error(`cancel returned ${response.status}`);
			}
			setJob((await response.json()) as JobDetailPayload);
			setError(null);
		} catch (cancelError) {
			setError(
				cancelError instanceof Error ? cancelError.message : "cancel failed",
			);
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
			const response = await fetch(`/admin/jobs/${job.job_id}/retry`, {
				method: "POST",
			});
			if (!response.ok) {
				throw new Error(`retry returned ${response.status}`);
			}
			const body = (await response.json()) as JobDetailPayload;
			navigate({ page: "job", params: { id: body.job_id } });
		} catch (retryError) {
			setError(
				retryError instanceof Error ? retryError.message : "retry failed",
			);
		} finally {
			setBusy(null);
		}
	}

	async function copyJson() {
		if (!job) {
			return;
		}
		await navigator.clipboard.writeText(JSON.stringify(job, null, 2));
		setCopied(true);
		window.setTimeout(() => setCopied(false), 1300);
	}

	if (id === undefined) {
		return (
			<section className="placeholder-pane">
				<p className="eyebrow">Job</p>
				<h1>Missing job id</h1>
			</section>
		);
	}

	return (
		<section className="pane job-detail-page">
			<header className="pane-header">
				<div>
					<p className="eyebrow">Job {id}</p>
					<h1 className="pane-h1">
						{job?.transcript?.title ?? job?.video_id ?? "Loading job"}
					</h1>
					{job ? (
						<p className="detail-meta">
							<span>{job.video_id}</span>
							<span>{formatElapsed(job.elapsed_s)}</span>
							<a href={job.url} target="_blank" rel="noreferrer">
								YouTube
							</a>
						</p>
					) : null}
				</div>
				{job ? <StatusChip status={job.status} /> : null}
			</header>

			{error ? <p className="error-banner">{error}</p> : null}

			{job ? (
				<>
					<div className="job-actions">
						<button
							type="button"
							className="btn"
							onClick={cancelJob}
							disabled={TERMINAL.has(job.status) || busy !== null}
						>
							Cancel
						</button>
						<button
							type="button"
							className="btn"
							onClick={retryJob}
							disabled={!TERMINAL.has(job.status) || busy !== null}
						>
							Retry
						</button>
						<button type="button" className="btn ghost" onClick={copyJson}>
							{copied ? "Copied" : "Copy JSON"}
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

					<section className="job-panel">
						<div className="section-heading">
							<h2>Pipeline</h2>
						</div>
						<PipelineDiagram stages={job.stages} />
						{job.error ? <p className="error-banner">{job.error}</p> : null}
					</section>

					<LogTail
						jobId={job.job_id}
						status={job.status}
						error={job.error}
					/>
				</>
			) : (
				<p className="muted">Loading job...</p>
			)}
		</section>
	);
}
