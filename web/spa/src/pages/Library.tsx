import React from "react";

import { CMDK_OPEN_EVENT } from "../constants";
import type { Route } from "../hooks/useRoute";
import type { LibraryLayout } from "../hooks/useTweaks";

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

type LibraryProps = {
	layout: LibraryLayout;
	route: Route;
	navigate: (route: Route) => void;
};

const terminalStatuses = new Set(["done", "failed", "cancelled", "canceled"]);
const stageLabels = ["queued", "downloading", "transcribing", "summarizing"];
const libraryPageSize = 50;

function publishCmdkOpen(): void {
	document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT));
}

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

function formatCost(value: number | null): string {
	if (value === null) {
		return "cost n/a";
	}
	return new Intl.NumberFormat(undefined, {
		style: "currency",
		currency: "USD",
		maximumFractionDigits: 3,
	}).format(value);
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

export function Library({ layout, route, navigate }: LibraryProps) {
	const selectedTag = route.params.tag;
	const [query, setQuery] = React.useState("");
	const [debouncedQuery, setDebouncedQuery] = React.useState("");
	const [rows, setRows] = React.useState<LibraryRow[]>([]);
	const [total, setTotal] = React.useState(0);
	const [offset, setOffset] = React.useState(0);
	const [retryTick, setRetryTick] = React.useState(0);
	const [isLoading, setIsLoading] = React.useState(true);
	const [error, setError] = React.useState<string | null>(null);

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
					<button
						type="button"
						className="btn primary"
						onClick={publishCmdkOpen}
					>
						Submit URL
					</button>
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
					<button
						type="button"
						className="btn primary"
						onClick={publishCmdkOpen}
					>
						Submit URL
					</button>
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
							onTagClick={tagClick}
							onOpen={transcriptClick}
						/>
					) : null}
					{layout === "feed" ? (
						<LibFeed
							rows={rows}
							onTagClick={tagClick}
							onOpen={transcriptClick}
						/>
					) : null}
					{layout === "cards" ? (
						<LibCards
							rows={rows}
							onTagClick={tagClick}
							onOpen={transcriptClick}
						/>
					) : null}
				</div>
			) : null}
		</section>
	);
}

function InFlightStrip({ navigate }: { navigate: (route: Route) => void }) {
	const [jobs, setJobs] = React.useState<ActiveJob[]>([]);
	const [error, setError] = React.useState(false);

	React.useEffect(() => {
		let abort: AbortController | null = null;
		let timer: number | undefined;
		let stopped = false;
		let isPolling = false;

		function schedulePoll(delayMs: number) {
			window.clearTimeout(timer);
			if (!stopped) {
				timer = window.setTimeout(poll, delayMs);
			}
		}

		async function poll() {
			if (stopped || isPolling) {
				return;
			}
			if (document.hidden) {
				schedulePoll(30000);
				return;
			}
			isPolling = true;
			const controller = new AbortController();
			abort = controller;
			try {
				const response = await fetch("/api/jobs/active", {
					signal: controller.signal,
				});
				if (!response.ok) {
					throw new Error("active jobs request failed");
				}
				const body = (await response.json()) as ActiveJobsResponse;
				setJobs(body.jobs);
				setError(false);
				schedulePoll(hasNonTerminalJob(body.jobs) ? 5000 : 30000);
			} catch (loadError) {
				if (!controller.signal.aborted) {
					setError(true);
					schedulePoll(30000);
				}
			} finally {
				if (abort === controller) {
					abort = null;
				}
				isPolling = false;
			}
		}

		function resumeWhenVisible() {
			if (!document.hidden) {
				window.clearTimeout(timer);
				void poll();
			}
		}

		void poll();
		document.addEventListener("visibilitychange", resumeWhenVisible);
		return () => {
			stopped = true;
			window.clearTimeout(timer);
			abort?.abort();
			document.removeEventListener("visibilitychange", resumeWhenVisible);
		};
	}, []);

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
	onTagClick,
	onOpen,
}: {
	rows: LibraryRow[];
	onTagClick: (tag: string) => void;
	onOpen: (id: number) => void;
}) {
	return (
		<div className="table-wrap">
			<table className="lib-table">
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
							</td>
							<td>
								<TagList row={row} onTagClick={onTagClick} />
							</td>
							<td className="muted">
								<span>{formatDuration(row.duration_seconds)}</span>
								<span>{formatCost(row.vast_cost)}</span>
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
	onTagClick,
	onOpen,
}: {
	rows: LibraryRow[];
	onTagClick: (tag: string) => void;
	onOpen: (id: number) => void;
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
					<RowMeta row={row} />
					<TagList row={row} onTagClick={onTagClick} />
					<RowLinks row={row} />
				</article>
			))}
		</div>
	);
}

function LibCards({
	rows,
	onTagClick,
	onOpen,
}: {
	rows: LibraryRow[];
	onTagClick: (tag: string) => void;
	onOpen: (id: number) => void;
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
					<RowMeta row={row} />
					<RowLinks row={row} />
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

function RowMeta({ row }: { row: LibraryRow }) {
	return (
		<div className="detail-meta">
			<span className="tnum">{formatDate(row.created_at)}</span>
			<span>{formatDuration(row.duration_seconds)}</span>
			<span>{row.lang ?? "lang n/a"}</span>
			<span>{formatCost(row.vast_cost)}</span>
		</div>
	);
}

function RowLinks({ row }: { row: LibraryRow }) {
	return (
		<div className="row-links">
			<a href={`https://youtu.be/${row.video_id}`}>YouTube</a>
			{row.summary_shortlink !== null ? (
				<a href={row.summary_shortlink}>Summary</a>
			) : null}
			{row.transcript_shortlink !== null ? (
				<a href={row.transcript_shortlink}>Transcript</a>
			) : null}
		</div>
	);
}
