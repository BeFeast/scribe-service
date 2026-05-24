import React from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { LogTail } from "../components/LogTail";
import { PipelineDiagram, type StageMap } from "../components/PipelineDiagram";
import {
	IconClock,
	IconCopy,
	IconExternal,
	IconRefresh,
	IconX,
} from "../components/ShellIcons";
import { StatusChip } from "../components/StatusChip";
import { useAuth } from "../hooks/useAuth";
import { usePoll } from "../hooks/usePoll";
import {
	type Route,
	handleRouteAnchorClick,
	routeToHref,
} from "../hooks/useRoute";

type TranscriptBrief = {
	id: number;
	title: string;
	video_id: string;
};

type JobDetailPayload = {
	job_id: number;
	url: string;
	video_id: string;
	title?: string | null;
	source_url?: string | null;
	source_label?: string | null;
	status: string;
	error?: string | null;
	callback_url?: string | null;
	transcript?: TranscriptBrief | null;
	started_at?: string | null;
	elapsed_s?: number | null;
	stages?: StageMap | null;
};

type OpsResponse = {
	vast_spend_24h?: number;
	daily_spend_cap_usd?: number;
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

function formatUsd(value?: number | null) {
	return new Intl.NumberFormat(undefined, {
		style: "currency",
		currency: "USD",
		maximumFractionDigits: 2,
	}).format(value ?? 0);
}

export function JobDetail({ id, navigate }: JobDetailProps) {
	const auth = useAuth();
	const [job, setJob] = React.useState<JobDetailPayload | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [ops, setOps] = React.useState<OpsResponse | null>(null);
	const [busy, setBusy] = React.useState<string | null>(null);
	const [copied, setCopied] = React.useState(false);
	const [cancelCandidate, setCancelCandidate] =
		React.useState<JobDetailPayload | null>(null);
	const isTerminal = job !== null && TERMINAL.has(job.status);

	// biome-ignore lint/correctness/useExhaustiveDependencies: id changes should clear the previous job detail.
	React.useEffect(() => {
		setJob(null);
		setError(null);
	}, [id]);

	const load = React.useCallback(
		async (signal: AbortSignal) => {
			if (id === undefined) {
				return;
			}
			try {
				const response = await auth.protectedFetch(`/jobs/${id}`, { signal });
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
		},
		[auth, id],
	);

	usePoll(load, 2000, { enabled: id !== undefined && !isTerminal });

	const loadOps = React.useCallback(
		async (signal: AbortSignal) => {
			try {
				const response = await auth.protectedFetch("/api/ops", { signal });
				if (response.ok) {
					setOps((await response.json()) as OpsResponse);
				}
			} catch {
				// Runtime notes are informational; job polling owns page errors.
			}
		},
		[auth],
	);

	usePoll(loadOps, 10000, { enabled: id !== undefined });

	async function cancelJob() {
		if (!job) {
			return;
		}
		setBusy("cancel");
		try {
			const response = await auth.protectedFetch(
				`/admin/jobs/${job.job_id}/cancel`,
				{
					method: "POST",
				},
			);
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
			const response = await auth.protectedFetch(
				`/admin/jobs/${job.job_id}/retry`,
				{
					method: "POST",
				},
			);
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

	async function clearFailedJob() {
		if (!job || job.status !== "failed") {
			return;
		}
		setBusy("clear");
		try {
			const response = await auth.protectedFetch(`/admin/jobs/${job.job_id}`, {
				method: "DELETE",
			});
			if (!response.ok) {
				throw new Error(`clear returned ${response.status}`);
			}
			navigate({ page: "queue", params: {} });
		} catch (clearError) {
			setError(
				clearError instanceof Error ? clearError.message : "clear failed",
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

	const linkedTranscript = job?.transcript ?? null;
	const title =
		linkedTranscript?.title ?? job?.title ?? job?.video_id ?? "Loading job";
	const sourceHref = job ? (job.source_url ?? job.url) : undefined;
	const sourceLabel = job ? (job.source_label ?? job.url) : "Source";
	const transcriptRoute: Route | null = linkedTranscript
		? { page: "transcript", params: { id: linkedTranscript.id } }
		: null;

	return (
		<section className="pane job-detail-page" data-testid="job-detail-pane">
			<a
				className="queue-back"
				href={routeToHref({ page: "queue", params: {} })}
				onClick={(event) =>
					handleRouteAnchorClick(event, { page: "queue", params: {} }, navigate)
				}
			>
				← Queue
			</a>
			<div className="row job-detail-meta-row">
				<div className="mono muted">
					job_id <span className="soft">{id}</span>
					{job ? ` · via ${job.source_label ?? "direct"}` : ""}
				</div>
				<div className="spacer" />
				{job ? <StatusChip status={job.status} /> : null}
				{job ? (
					<span className="mono muted job-card-elapsed">
						<IconClock size={11} />
						{formatElapsed(job.elapsed_s)} elapsed
					</span>
				) : null}
			</div>
			<h1 className="detail-h1">
				{transcriptRoute && linkedTranscript ? (
					<a
						href={routeToHref(transcriptRoute)}
						onClick={(event) =>
							handleRouteAnchorClick(event, transcriptRoute, navigate)
						}
					>
						{title}
					</a>
				) : (
					title
				)}
			</h1>
			{job && sourceHref ? (
				<div className="detail-meta">
					<a href={sourceHref} target="_blank" rel="noopener noreferrer">
						<IconExternal size={11} />
						{sourceLabel}
					</a>
				</div>
			) : null}

			{error ? <p className="error-banner">{error}</p> : null}

			{job ? (
				<>
					<PipelineDiagram stages={job.stages} />
					{job.error ? <p className="error-banner">{job.error}</p> : null}

					<LogTail jobId={job.job_id} status={job.status} error={job.error} />

					<div className="section-label">Job actions</div>
					<div className="job-actions">
						<button type="button" className="btn" onClick={copyJson}>
							<IconCopy size={14} />
							{copied ? "Copied" : "Copy job JSON"}
						</button>
						<button
							type="button"
							className="btn"
							onClick={retryJob}
							disabled={!TERMINAL.has(job.status) || busy !== null}
						>
							<IconRefresh size={14} />
							{busy === "retry" ? "Retrying" : "Retry job"}
						</button>
						<button
							type="button"
							className="btn ghost"
							onClick={clearFailedJob}
							disabled={job.status !== "failed" || busy !== null}
						>
							{busy === "clear" ? "Clearing" : "Clear failure"}
						</button>
						<button
							type="button"
							className="btn ghost danger"
							onClick={() => setCancelCandidate(job)}
							disabled={TERMINAL.has(job.status) || busy !== null}
						>
							<IconX size={14} />
							{busy === "cancel" ? "Cancelling" : "Cancel job"}
						</button>
					</div>
					<div className="hr" />
					<div className="runtime-notes">
						<div>
							This page polls <code>GET /jobs/{job.job_id}</code> every 2s while
							the job is in flight.
						</div>
						<div>
							Webhooks fire on terminal status (<code>done</code> |{" "}
							<code>failed</code>).
						</div>
						<div>
							Daily Vast spend cap:{" "}
							<span className="tnum">
								{formatUsd(ops?.daily_spend_cap_usd)}
							</span>{" "}
							· used{" "}
							<span className="tnum">{formatUsd(ops?.vast_spend_24h)}</span> in
							the last 24h.
						</div>
					</div>
				</>
			) : (
				<p className="muted">Loading job...</p>
			)}

			{cancelCandidate !== null ? (
				<ConfirmDialog
					title="Cancel job"
					body={`Cancel job ${cancelCandidate.job_id} (${cancelCandidate.transcript?.title ?? cancelCandidate.video_id})? The current pipeline stage may still finish, but the job will be marked failed.`}
					confirmLabel="Cancel job"
					busyLabel="Cancelling"
					busy={busy === "cancel"}
					onCancel={() => setCancelCandidate(null)}
					onConfirm={async () => {
						await cancelJob();
						setCancelCandidate(null);
					}}
				/>
			) : null}
		</section>
	);
}
