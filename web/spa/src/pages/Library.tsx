import React from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import type { Route } from "../hooks/useRoute";
import { usePoll } from "../hooks/usePoll";
import type { LibraryLayout } from "../hooks/useTweaks";
import type { DisplayCurrency } from "../lib/currency";
import { formatUsdCost } from "../lib/currency";

type LibraryRow = {
	id: number;
	video_id: string;
	title: string;
	tags: string[] | null;
	lang: string | null;
	duration_seconds: number | null;
	vast_cost: number | null;
	created_at: string;
	summary_shortlink: string | null;
	transcript_shortlink: string | null;
	summary_excerpt: string;
	is_partial: boolean;
};

type LibraryResponse = {
	rows: LibraryRow[];
	total: number;
	limit: number;
	offset: number;
};

type JobStage = {
	state: string;
	started_at?: string;
	finished_at?: string;
	duration_s?: number;
	progress?: number;
	note?: string;
};

type ActiveJob = {
	id: number;
	video_id: string;
	url: string;
	title?: string | null;
	status: string;
	source?: string | null;
	started_at: string;
	elapsed_s: number;
	stages: Record<string, JobStage>;
};

type ActiveJobsResponse = {
	jobs: ActiveJob[];
};

type JobView = {
	job_id: number;
	video_id: string;
	status: string;
	deduplicated?: boolean;
};

type LibraryProps = {
	layout: LibraryLayout;
	displayCurrency: DisplayCurrency;
	route: Route;
	navigate: (route: Route) => void;
};

const terminalStatuses = new Set(["done", "failed", "cancelled", "canceled"]);
const stageLabels = ["queued", "downloading", "transcribing", "summarizing"];
const libraryPageSize = 50;

function formatDate(value: string): string {
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) {
		return "unknown";
	}
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
	}).format(date);
}

function formatDuration(seconds: number | null): string {
	if (seconds === null) {
		return "duration n/a";
	}
	const minutes = Math.floor(seconds / 60);
	const rest = Math.floor(seconds % 60);
	return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function buildLibraryUrl(
	query: string,
	tag: string | undefined,
	limit: number,
	offset: number,
): string {
	const params = new URLSearchParams([
		["q", query],
		["tag", tag ?? ""],
		["limit", String(limit)],
		["offset", String(offset)],
	]);
	return `/api/library?${params.toString()}`;
}

function hasNonTerminalJob(jobs: ActiveJob[]): boolean {
	return jobs.some((job) => !terminalStatuses.has(job.status));
}

function errorMessage(status: number, body: unknown): string {
	if (
		typeof body === "object" &&
		body !== null &&
		"detail" in body &&
		typeof body.detail === "string"
	) {
		return body.detail;
	}
	return `Submit failed: ${status}`;
}

export function Library({ layout, displayCurrency, route, navigate }: LibraryProps) {
	const selectedTag = route.params.tag;
	const [query, setQuery] = React.useState("");
	const [submitUrl, setSubmitUrl] = React.useState("");
	const [submitState, setSubmitState] = React.useState<
		| { state: "idle" }
		| { state: "submitting" }
		| { state: "success"; job: JobView }
		| { state: "error"; message: string }
	>({ state: "idle" });
	const [debouncedQuery, setDebouncedQuery] = React.useState("");
	const [rows, setRows] = React.useState<LibraryRow[]>([]);
	const [total, setTotal] = React.useState(0);
	const [offset, setOffset] = React.useState(0);
	const [retryTick, setRetryTick] = React.useState(0);
	const [isLoading, setIsLoading] = React.useState(true);
	const [error, setError] = React.useState<string | null>(null);
	const [deleteBusyId, setDeleteBusyId] = React.useState<number | null>(null);
	const [deleteCandidate, setDeleteCandidate] =
		React.useState<LibraryRow | null>(null);

	React.useEffect(() => {
		const timer = window.setTimeout(() => setDebouncedQuery(query), 200);
		return () => window.clearTimeout(timer);
	}, [query]);

	React.useEffect(() => {
		void debouncedQuery;
		void selectedTag;
		setOffset(0);
	}, [debouncedQuery, selectedTag]);

	const loadLibrary = React.useCallback(
		async (signal: AbortSignal) => {
			setIsLoading(true);
			setError(null);
			try {
				const response = await fetch(
					buildLibraryUrl(debouncedQuery, selectedTag, libraryPageSize, offset),
					{
						signal,
					},
				);
				if (!response.ok) {
					throw new Error(`Library request failed: ${response.status}`);
				}
				const body = (await response.json()) as LibraryResponse;
				setRows(body.rows);
				setTotal(body.total);
			} catch (loadError) {
				if (!signal.aborted) {
					setRows([]);
					setTotal(0);
					setError(
						loadError instanceof Error
							? loadError.message
							: "Library request failed",
					);
				}
			} finally {
				if (!signal.aborted) {
					setIsLoading(false);
				}
			}
		},
		[debouncedQuery, offset, selectedTag],
	);

	React.useEffect(() => {
		void retryTick;
		const abort = new AbortController();
		void loadLibrary(abort.signal);
		return () => abort.abort();
	}, [loadLibrary, retryTick]);

	const clearTag = () => navigate({ page: "library", params: {} });
	const retry = () => setRetryTick((value) => value + 1);
	const canPageBack = offset > 0;
	const canPageForward = offset + rows.length < total;
	const pageStart = total === 0 ? 0 : offset + 1;
	const pageEnd = offset + rows.length;
	const previousPage = () =>
		setOffset((value) => Math.max(0, value - libraryPageSize));
	const nextPage = () => setOffset((value) => value + libraryPageSize);
	const tagClick = (tag: string) =>
		navigate({ page: "library", params: { tag } });
	const transcriptClick = (id: number) =>
		navigate({ page: "transcript", params: { id } });
	const requestDeleteTranscript = (row: LibraryRow) => {
		if (deleteBusyId === null) {
			setDeleteCandidate(row);
		}
	};
	const confirmDeleteTranscript = async () => {
		if (deleteCandidate === null || deleteBusyId !== null) {
			return;
		}
		const row = deleteCandidate;
		setDeleteBusyId(row.id);
		setError(null);
		try {
			const response = await fetch(`/admin/transcripts/${row.id}`, {
				method: "DELETE",
			});
			if (!response.ok) {
				throw new Error(`Delete failed: ${response.status}`);
			}
			setRows((current) => current.filter((item) => item.id !== row.id));
			setTotal((current) => Math.max(0, current - 1));
			setDeleteCandidate(null);
		} catch (deleteError) {
			setError(
				deleteError instanceof Error ? deleteError.message : "Delete failed",
			);
		} finally {
			setDeleteBusyId(null);
		}
	};
	const submitTrimmed = submitUrl.trim();
	const canSubmitUrl = submitTrimmed.length > 0 && submitState.state !== "submitting";
	const submitMessage =
		submitState.state === "success"
			? `Queued job #${submitState.job.job_id}`
			: submitState.state === "error"
				? submitState.message
				: null;
	const submitClass =
		submitState.state === "error"
			? "library-submit-status err-msg"
			: "library-submit-status muted";
	const submitJob = async (event: React.FormEvent<HTMLFormElement>) => {
		event.preventDefault();
		if (!canSubmitUrl) {
			return;
		}
		setSubmitState({ state: "submitting" });
		try {
			const response = await fetch("/jobs", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ url: submitTrimmed, source: "manual" }),
			});
			const body = (await response.json()) as unknown;
			if (!response.ok) {
				setSubmitState({
					state: "error",
					message: errorMessage(response.status, body),
				});
				return;
			}
			setSubmitUrl("");
			setSubmitState({ state: "success", job: body as JobView });
			retry();
		} catch (submitError) {
			setSubmitState({
				state: "error",
				message:
					submitError instanceof Error ? submitError.message : "Submit failed",
			});
		}
	};

	return (
		<section className="library-page">
			<InFlightStrip navigate={navigate} />
			<header className="library-hero">
				<div className="library-title">
					<p className="section-label">Library</p>
					<h1 className="pane-h1">Transcripts</h1>
					<div className="library-meta">
						<span className="chip info">{total} transcripts</span>
						{selectedTag !== undefined ? (
							<button type="button" className="chip" onClick={clearTag}>
								tag: {selectedTag}
							</button>
						) : null}
						{isLoading ? (
							<span className="spinner" aria-label="Loading" />
						) : null}
					</div>
				</div>
				<div className="library-actions">
					<label className="library-search">
						<span>Search</span>
						<input
							type="search"
							value={query}
							onChange={(event) => setQuery(event.currentTarget.value)}
							placeholder="Title or summary"
						/>
					</label>
					<form className="library-submit" onSubmit={submitJob}>
						<label>
							<span>Submit URL</span>
							<input
								type="url"
								value={submitUrl}
								onChange={(event) => {
									setSubmitUrl(event.currentTarget.value);
									if (submitState.state !== "idle") {
										setSubmitState({ state: "idle" });
									}
								}}
								placeholder="YouTube URL"
							/>
						</label>
						<button type="submit" className="btn primary" disabled={!canSubmitUrl}>
							Submit
						</button>
						{submitMessage !== null ? (
							<p className={submitClass}>{submitMessage}</p>
						) : null}
					</form>
				</div>
			</header>

			{error !== null ? (
				<div className="library-state failure-row">
					<span className="chip err">error</span>
					<p className="err-title">Library unavailable</p>
					<p className="err-msg">{error}</p>
					<button type="button" className="btn" onClick={retry}>
						Retry
					</button>
				</div>
			) : null}

			{!isLoading && error === null && rows.length === 0 ? (
				<div className="library-state">
					<span className="chip info">0 transcripts</span>
					<p className="feed-title">Nothing in the library yet</p>
					<p className="feed-excerpt">
						Submitted YouTube URLs will appear here after transcription starts.
					</p>
				</div>
			) : null}

			{rows.length > 0 ? (
				<div className="library-results">
					<div className="library-pager">
						<span className="chip info">
							{pageStart}-{pageEnd} of {total}
						</span>
						<div className="pager-actions">
							<button
								type="button"
								className="btn"
								onClick={previousPage}
								disabled={!canPageBack || isLoading}
							>
								Previous
							</button>
							<button
								type="button"
								className="btn"
								onClick={nextPage}
								disabled={!canPageForward || isLoading}
							>
								Next
							</button>
						</div>
					</div>
					{layout === "table" ? (
						<LibTable
							rows={rows}
							displayCurrency={displayCurrency}
							onTagClick={tagClick}
							onOpen={transcriptClick}
							onDelete={requestDeleteTranscript}
							deleteBusyId={deleteBusyId}
						/>
					) : null}
					{layout === "feed" ? (
						<LibFeed
							rows={rows}
							displayCurrency={displayCurrency}
							onTagClick={tagClick}
							onOpen={transcriptClick}
							onDelete={requestDeleteTranscript}
							deleteBusyId={deleteBusyId}
						/>
					) : null}
					{layout === "cards" ? (
						<LibCards
							rows={rows}
							displayCurrency={displayCurrency}
							onTagClick={tagClick}
							onOpen={transcriptClick}
							onDelete={requestDeleteTranscript}
							deleteBusyId={deleteBusyId}
						/>
					) : null}
				</div>
			) : null}

			{deleteCandidate !== null ? (
				<ConfirmDialog
					title="Delete transcript"
					body={`Delete "${deleteCandidate.title}"? This also removes its job record.`}
					confirmLabel="Delete"
					busyLabel="Deleting"
					busy={deleteBusyId === deleteCandidate.id}
					onCancel={() => setDeleteCandidate(null)}
					onConfirm={confirmDeleteTranscript}
				/>
			) : null}
		</section>
	);
}

function InFlightStrip({ navigate }: { navigate: (route: Route) => void }) {
	const [jobs, setJobs] = React.useState<ActiveJob[]>([]);
	const [error, setError] = React.useState(false);
	const interval = hasNonTerminalJob(jobs) ? 5000 : 30000;

	const poll = React.useCallback(async (signal: AbortSignal) => {
		try {
			const response = await fetch("/api/jobs/active", { signal });
			if (!response.ok) {
				throw new Error("active jobs request failed");
			}
			const body = (await response.json()) as ActiveJobsResponse;
			setJobs(body.jobs);
			setError(false);
		} catch (loadError) {
			if (!signal.aborted) {
				setError(true);
			}
		}
	}, []);

	usePoll(poll, interval);

	if (jobs.length === 0) {
		return null;
	}

	return (
		<section className="inflight-strip" aria-label="In-flight jobs">
			<div className="inflight-head">
				<span className="live-dot" aria-hidden="true" />
				<strong>In flight</strong>
				{error ? <span className="chip warn">poll delayed</span> : null}
			</div>
			{jobs.map((job) => (
				<InFlightRow
					key={job.id}
					job={job}
					onOpen={() => navigate({ page: "job", params: { id: job.id } })}
				/>
			))}
		</section>
	);
}

function InFlightRow({ job, onOpen }: { job: ActiveJob; onOpen: () => void }) {
	const activeStage =
		stageLabels.find((stage) => job.stages[stage]?.state === "active") ??
		job.status;
	const doneCount = stageLabels.filter(
		(stage) => job.stages[stage]?.state === "done",
	).length;
	const progress = Math.max(
		8,
		Math.min(100, ((doneCount + 0.5) / stageLabels.length) * 100),
	);

	return (
		<button type="button" className="inflight-row" onClick={onOpen}>
			<span className="inflight-copy">
				<strong>{job.title ?? job.video_id}</strong>
				<span>
					{activeStage} / {job.elapsed_s}s
				</span>
			</span>
			<span className="bar-track" aria-label={`${activeStage} progress`}>
				<span style={{ width: `${progress}%` }} />
			</span>
			<span className="chip run">{job.status}</span>
		</button>
	);
}

function LibTable({
	rows,
	displayCurrency,
	onTagClick,
	onOpen,
	onDelete,
	deleteBusyId,
}: {
	rows: LibraryRow[];
	displayCurrency: DisplayCurrency;
	onTagClick: (tag: string) => void;
	onOpen: (id: number) => void;
	onDelete: (row: LibraryRow) => void;
	deleteBusyId: number | null;
}) {
	return (
		<div className="table-wrap">
			<table className="lib-table">
				<colgroup>
					<col className="lib-table-title-col" />
					<col className="lib-table-tags-col" />
					<col className="lib-table-meta-col" />
					<col className="lib-table-created-col" />
				</colgroup>
				<thead>
					<tr>
						<th>Title</th>
						<th>Tags</th>
						<th>Meta</th>
						<th>Created</th>
					</tr>
				</thead>
				<tbody>
					{rows.map((row) => (
						<tr key={row.id}>
							<td>
								<button
									type="button"
									className="link-button table-title"
									onClick={() => onOpen(row.id)}
								>
									{row.title}
								</button>
								<p className="feed-excerpt">{row.summary_excerpt}</p>
								{row.is_partial ? (
									<span className="chip warn">partial</span>
								) : null}
								<RowLinks row={row} onDelete={onDelete} busy={deleteBusyId === row.id} />
							</td>
							<td>
								<TagList row={row} onTagClick={onTagClick} />
							</td>
							<td className="muted">
								<div className="table-meta-stack">
									<span>{formatDuration(row.duration_seconds)}</span>
									<span>{formatUsdCost(row.vast_cost, displayCurrency)}</span>
								</div>
							</td>
							<td className="tnum">{formatDate(row.created_at)}</td>
						</tr>
					))}
				</tbody>
			</table>
		</div>
	);
}

function LibFeed({
	rows,
	displayCurrency,
	onTagClick,
	onOpen,
	onDelete,
	deleteBusyId,
}: {
	rows: LibraryRow[];
	displayCurrency: DisplayCurrency;
	onTagClick: (tag: string) => void;
	onOpen: (id: number) => void;
	onDelete: (row: LibraryRow) => void;
	deleteBusyId: number | null;
}) {
	return (
		<div className="lib-feed">
			{rows.map((row) => (
				<article className="feed-item" key={row.id}>
					<div className="feed-head">
						<button
							type="button"
							className="link-button feed-title"
							onClick={() => onOpen(row.id)}
						>
							{row.title}
						</button>
						{row.is_partial ? <span className="chip warn">partial</span> : null}
					</div>
					<p className="feed-excerpt">{row.summary_excerpt}</p>
					<RowMeta row={row} displayCurrency={displayCurrency} />
					<TagList row={row} onTagClick={onTagClick} />
					<RowLinks row={row} onDelete={onDelete} busy={deleteBusyId === row.id} />
				</article>
			))}
		</div>
	);
}

function LibCards({
	rows,
	displayCurrency,
	onTagClick,
	onOpen,
	onDelete,
	deleteBusyId,
}: {
	rows: LibraryRow[];
	displayCurrency: DisplayCurrency;
	onTagClick: (tag: string) => void;
	onOpen: (id: number) => void;
	onDelete: (row: LibraryRow) => void;
	deleteBusyId: number | null;
}) {
	return (
		<div className="lib-cards">
			{rows.map((row) => (
				<article className="card lib-card" key={row.id}>
					<div>
						<button
							type="button"
							className="link-button feed-title"
							onClick={() => onOpen(row.id)}
						>
							{row.title}
						</button>
						{row.is_partial ? <span className="chip warn">partial</span> : null}
					</div>
					<p className="feed-excerpt">{row.summary_excerpt}</p>
					<TagList row={row} onTagClick={onTagClick} />
					<RowMeta row={row} displayCurrency={displayCurrency} />
					<RowLinks row={row} onDelete={onDelete} busy={deleteBusyId === row.id} />
				</article>
			))}
		</div>
	);
}

function TagList({
	row,
	onTagClick,
}: {
	row: LibraryRow;
	onTagClick: (tag: string) => void;
}) {
	if (row.tags === null || row.tags.length === 0) {
		return <span className="muted">untagged</span>;
	}
	return (
		<div className="detail-tags">
			{row.tags.map((tag) => (
				<button
					type="button"
					className="tag tag-button"
					key={tag}
					onClick={() => onTagClick(tag)}
				>
					{tag}
				</button>
			))}
		</div>
	);
}

function RowMeta({
	row,
	displayCurrency,
}: {
	row: LibraryRow;
	displayCurrency: DisplayCurrency;
}) {
	return (
		<div className="detail-meta">
			<span className="tnum">{formatDate(row.created_at)}</span>
			<span>{formatDuration(row.duration_seconds)}</span>
			<span>{row.lang ?? "lang n/a"}</span>
			<span>{formatUsdCost(row.vast_cost, displayCurrency)}</span>
		</div>
	);
}

function RowLinks({
	row,
	onDelete,
	busy,
}: {
	row: LibraryRow;
	onDelete: (row: LibraryRow) => void;
	busy: boolean;
}) {
	return (
		<div className="row-links">
			<a href={`https://youtu.be/${row.video_id}`}>YouTube</a>
			{row.summary_shortlink !== null ? (
				<a href={row.summary_shortlink}>Summary</a>
			) : null}
			{row.transcript_shortlink !== null ? (
				<a href={row.transcript_shortlink}>Transcript</a>
			) : null}
			<button
				type="button"
				className="link-button danger-link"
				onClick={() => onDelete(row)}
				disabled={busy}
			>
				{busy ? "Deleting" : "Delete"}
			</button>
		</div>
	);
}
