import React from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { PrivateShareLinks } from "../components/PrivateShareLinks";
import {
	IconCards,
	IconFeed,
	IconPlus,
	IconSearch,
	IconTable,
} from "../components/ShellIcons";
import { CMDK_OPEN_EVENT } from "../constants";
import { useAuth } from "../hooks/useAuth";
import { usePoll } from "../hooks/usePoll";
import {
	type Route,
	handleRouteAnchorClick,
	routeToHref,
} from "../hooks/useRoute";
import type { LibraryLayout } from "../hooks/useTweaks";
import { isAuthStatus } from "../lib/auth";
import type { DisplayCurrency } from "../lib/currency";
import { formatUsdCost } from "../lib/currency";
import type { ShareTarget } from "../shareTargets";

type LibraryRow = {
	id: number;
	video_id: string;
	title: string;
	tags: string[] | null;
	lang: string | null;
	duration_seconds: number | null;
	vast_cost: number | null;
	created_at: string;
	source_url: string | null;
	source_label: string | null;
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
	source_url?: string | null;
	source_label?: string | null;
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
	displayCurrency: DisplayCurrency;
	route: Route;
	navigate: (route: Route) => void;
	setLibraryLayout: (layout: LibraryLayout) => void;
};

const terminalStatuses = new Set(["done", "failed", "cancelled", "canceled"]);
const stageLabels = ["queued", "downloading", "transcribing", "summarizing"];
const libraryPageSize = 50;
const pageCopyKinds = new Set<ShareTarget["kind"]>(["page"]);
const completeShareTargetKinds = new Set<ShareTarget["kind"]>([
	"page",
	"summary",
	"transcript",
]);
const partialShareTargetKinds = new Set<ShareTarget["kind"]>([
	"page",
	"transcript",
]);

function formatDate(value: string): string {
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) {
		return "date n/a";
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

function formatElapsed(seconds: number): string {
	const minutes = Math.floor(seconds / 60);
	const rest = Math.floor(seconds % 60);
	return minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
}

function previewSummary(row: LibraryRow): string {
	if (row.is_partial || row.summary_excerpt.trim() === "") {
		return "Transcription finished. Summary regeneration is pending.";
	}
	return row.summary_excerpt
		.replace(/\*\*(.+?)\*\*/g, "$1")
		.replace(/\*(.+?)\*/g, "$1")
		.replace(/`(.+?)`/g, "$1")
		.replace(/^#+\s*/gm, "")
		.replace(/\n+/g, " ")
		.trim()
		.slice(0, 240);
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

function handleKeyboardOpen(
	event: React.KeyboardEvent,
	open: () => void,
): void {
	if (event.target !== event.currentTarget) {
		return;
	}
	if (event.key === "Enter" || event.key === " ") {
		event.preventDefault();
		open();
	}
}

async function readErrorMessage(response: Response): Promise<string> {
	try {
		const body = (await response.json()) as unknown;
		if (
			typeof body === "object" &&
			body !== null &&
			"detail" in body &&
			typeof body.detail === "string"
		) {
			return body.detail;
		}
	} catch {
		// Fall back to the response status.
	}
	return `HTTP ${response.status} ${response.statusText}`.trim();
}

type LibraryError = { kind: "auth" } | { kind: "service"; message: string };

export function Library({
	layout,
	displayCurrency,
	route,
	navigate,
	setLibraryLayout,
}: LibraryProps) {
	const auth = useAuth();
	const selectedTag = route.params.tag;
	const [query, setQuery] = React.useState("");
	const [debouncedQuery, setDebouncedQuery] = React.useState("");
	const [rows, setRows] = React.useState<LibraryRow[]>([]);
	const [total, setTotal] = React.useState(0);
	const [offset, setOffset] = React.useState(0);
	const [retryTick, setRetryTick] = React.useState(0);
	const [isLoading, setIsLoading] = React.useState(true);
	const [error, setError] = React.useState<LibraryError | null>(null);
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
				const response = await auth.protectedFetch(
					buildLibraryUrl(debouncedQuery, selectedTag, libraryPageSize, offset),
					{ signal },
				);
				if (isAuthStatus(response.status)) {
					setRows([]);
					setTotal(0);
					setError({ kind: "auth" });
					auth.maybeAutoSignIn();
					return;
				}
				if (!response.ok) {
					setRows([]);
					setTotal(0);
					setError({
						kind: "service",
						message: `Service temporarily unavailable (HTTP ${response.status}).`,
					});
					return;
				}
				const body = (await response.json()) as LibraryResponse;
				setRows(body.rows);
				setTotal(body.total);
			} catch (loadError) {
				if (!signal.aborted) {
					setRows([]);
					setTotal(0);
					setError({
						kind: "service",
						message:
							loadError instanceof Error
								? loadError.message
								: "Service temporarily unavailable.",
					});
				}
			} finally {
				if (!signal.aborted) {
					setIsLoading(false);
				}
			}
		},
		[auth, debouncedQuery, offset, selectedTag],
	);

	React.useEffect(() => {
		void retryTick;
		const abort = new AbortController();
		void loadLibrary(abort.signal);
		return () => abort.abort();
	}, [loadLibrary, retryTick]);

	const retry = () => setRetryTick((value) => value + 1);
	const canPageBack = offset > 0;
	const canPageForward = offset + rows.length < total;
	const pageStart = total === 0 ? 0 : offset + 1;
	const pageEnd = offset + rows.length;
	const previousPage = () =>
		setOffset((value) => Math.max(0, value - libraryPageSize));
	const nextPage = () => setOffset((value) => value + libraryPageSize);
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
			const response = await auth.protectedFetch(
				`/admin/transcripts/${row.id}`,
				{ method: "DELETE" },
			);
			if (!response.ok) {
				throw new Error(await readErrorMessage(response));
			}
			setRows((current) => current.filter((item) => item.id !== row.id));
			setTotal((current) => Math.max(0, current - 1));
			setDeleteCandidate(null);
		} catch (deleteError) {
			setError({
				kind: "service",
				message:
					deleteError instanceof Error ? deleteError.message : "Delete failed",
			});
		} finally {
			setDeleteBusyId(null);
		}
	};
	const authRequired = error?.kind === "auth";
	const openCommandPalette = () =>
		document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT));

	return (
		<section className="pane library-page">
			<header className="pane-header">
				<div>
					<h1 className="pane-h1">Library</h1>
					<div className="pane-sub">
						{total} transcript{total === 1 ? "" : "s"}
						{selectedTag !== undefined ? (
							<>
								{" "}
								/ tag <code className="active-filter-code">{selectedTag}</code>
								<a
									className="clear-filter-link"
									href={routeToHref({ page: "library", params: {} })}
									onClick={(event) =>
										handleRouteAnchorClick(
											event,
											{ page: "library", params: {} },
											navigate,
										)
									}
								>
									clear
								</a>
							</>
						) : null}
						{isLoading ? (
							<span className="muted" aria-live="polite">
								{" "}
								/ loading rows...
							</span>
						) : null}
					</div>
				</div>
				<div className="pane-actions">
					<button
						type="button"
						className="btn primary"
						onClick={openCommandPalette}
						disabled={authRequired}
					>
						<IconPlus size={14} /> Submit URL
					</button>
				</div>
			</header>

			<InFlightStrip navigate={navigate} />

			<div className="lib-toolbar">
				<label className="search">
					<IconSearch size={14} />
					<input
						type="search"
						value={query}
						onChange={(event) => setQuery(event.currentTarget.value)}
						placeholder="Search titles + transcripts..."
						disabled={authRequired}
					/>
				</label>
				<div className="seg" role="tablist" aria-label="Layout">
					<LayoutButton
						label="Table layout"
						active={layout === "table"}
						onClick={() => setLibraryLayout("table")}
					>
						<IconTable size={14} />
					</LayoutButton>
					<LayoutButton
						label="Feed layout"
						active={layout === "feed"}
						onClick={() => setLibraryLayout("feed")}
					>
						<IconFeed size={14} />
					</LayoutButton>
					<LayoutButton
						label="Cards layout"
						active={layout === "cards"}
						onClick={() => setLibraryLayout("cards")}
					>
						<IconCards size={14} />
					</LayoutButton>
				</div>
			</div>

			{error?.kind === "auth" ? (
				<div
					className="library-state library-auth-gate"
					data-state="auth-required"
					aria-live="polite"
				>
					<span className="chip info">Sign in required</span>
					<p className="feed-title">Your library is signed-in only</p>
					<p className="feed-excerpt">
						{auth.authBlockedMessage ??
							"Library entries are owner-scoped. Sign in with your Scribe account to see your transcripts."}
					</p>
					<div className="auth-choice-row">
						<button
							type="button"
							className="btn primary"
							onClick={() => void auth.signUp()}
							disabled={
								auth.clerkConfigured &&
								(!auth.clerkReady || auth.authRedirectInFlight)
							}
						>
							Sign up
						</button>
						<button
							type="button"
							className="btn ghost"
							onClick={() => void auth.signIn()}
							disabled={
								auth.clerkConfigured &&
								(!auth.clerkReady || auth.authRedirectInFlight)
							}
						>
							Sign in
						</button>
					</div>
				</div>
			) : null}

			{error?.kind === "service" ? (
				<div className="library-state failure-row error-state">
					<span className="chip err">error</span>
					<p className="err-title">Service temporarily unavailable</p>
					<p className="err-msg">{error.message}</p>
					<button type="button" className="btn" onClick={retry}>
						Retry
					</button>
				</div>
			) : null}

			{!isLoading && error === null && rows.length === 0 ? (
				<div className="library-state empty-state">
					<span className="chip info">0 transcripts</span>
					<p className="feed-title">
						{debouncedQuery || selectedTag
							? "No matching transcripts"
							: "Nothing in the library yet"}
					</p>
					<p className="feed-excerpt">
						{debouncedQuery || selectedTag
							? "Try another search or clear the selected tag."
							: "Submitted video URLs will appear here after transcription starts."}
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
							navigate={navigate}
							onDelete={requestDeleteTranscript}
							deleteBusyId={deleteBusyId}
						/>
					) : null}
					{layout === "feed" ? (
						<LibFeed
							rows={rows}
							displayCurrency={displayCurrency}
							navigate={navigate}
							onDelete={requestDeleteTranscript}
							deleteBusyId={deleteBusyId}
						/>
					) : null}
					{layout === "cards" ? (
						<LibCards
							rows={rows}
							displayCurrency={displayCurrency}
							navigate={navigate}
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
	const auth = useAuth();
	const [jobs, setJobs] = React.useState<ActiveJob[]>([]);
	const [error, setError] = React.useState(false);
	const interval = hasNonTerminalJob(jobs) ? 5000 : 30000;

	const poll = React.useCallback(
		async (signal: AbortSignal) => {
			try {
				const response = await auth.protectedFetch("/api/jobs/active", {
					signal,
				});
				if (isAuthStatus(response.status)) {
					setJobs([]);
					setError(false);
					return;
				}
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
		},
		[auth],
	);

	usePoll(poll, interval);

	if (jobs.length === 0) {
		return null;
	}

	return (
		<section className="inflight-strip" aria-label="In-flight jobs">
			<div className="inflight-head">
				<span className="live-dot" aria-hidden="true" />
				<strong>In flight</strong>
				<span className="muted">
					· {jobs.length} job{jobs.length === 1 ? "" : "s"}
				</span>
				{error ? <span className="chip warn">poll delayed</span> : null}
				<div className="spacer" />
				<a
					href={routeToHref({ page: "queue", params: {} })}
					onClick={(event) =>
						handleRouteAnchorClick(
							event,
							{ page: "queue", params: {} },
							navigate,
						)
					}
				>
					open queue →
				</a>
			</div>
			{jobs.map((job) => (
				<InFlightRow key={job.id} job={job} navigate={navigate} />
			))}
		</section>
	);
}

function InFlightRow({
	job,
	navigate,
}: {
	job: ActiveJob;
	navigate: (route: Route) => void;
}) {
	const jobRoute: Route = { page: "job", params: { id: job.id } };
	const activeStage =
		stageLabels.find((stage) => job.stages[stage]?.state === "active") ??
		job.status;

	return (
		<a
			className="inflight-row"
			href={routeToHref(jobRoute)}
			onClick={(event) => handleRouteAnchorClick(event, jobRoute, navigate)}
		>
			<span className="inflight-copy">
				<strong>{job.title ?? job.source_label ?? job.video_id}</strong>
				<span className="inflight-stages" aria-label="Pipeline stages">
					{stageLabels.map((stage) => {
						const stageState = job.stages[stage]?.state ?? "pending";
						const progress = job.stages[stage]?.progress;
						return (
							<span
								key={stage}
								className={`stage-pill ${stageState}`}
								title={stage}
							>
								{stageState === "active" && typeof progress === "number" ? (
									<span style={{ width: `${Math.round(progress * 100)}%` }} />
								) : null}
							</span>
						);
					})}
				</span>
			</span>
			<span className="mono muted">
				{activeStage === "transcribing"
					? `transcribing · ${Math.round((job.stages.transcribing?.progress ?? 0) * 100)}%`
					: activeStage}
			</span>
			<span className="mono muted">{formatElapsed(job.elapsed_s)}</span>
		</a>
	);
}

function LayoutButton({
	label,
	active,
	onClick,
	children,
}: {
	label: string;
	active: boolean;
	onClick: () => void;
	children: React.ReactNode;
}) {
	return (
		<button
			type="button"
			role="tab"
			aria-selected={active}
			title={label}
			onClick={onClick}
		>
			{children}
		</button>
	);
}

function LibTable({
	rows,
	displayCurrency,
	navigate,
	onDelete,
	deleteBusyId,
}: {
	rows: LibraryRow[];
	displayCurrency: DisplayCurrency;
	navigate: (route: Route) => void;
	onDelete: (row: LibraryRow) => void;
	deleteBusyId: number | null;
}) {
	function openTranscript(row: LibraryRow) {
		navigate({ page: "transcript", params: { id: row.id } });
	}

	return (
		<div className="table-wrap">
			<table className="lib-table">
				<thead>
					<tr>
						<th className="col-num">#</th>
						<th>Title</th>
						<th className="col-tags">Tags</th>
						<th className="col-len">Length</th>
						<th className="col-time">Created</th>
						<th className="col-actions">Actions</th>
					</tr>
				</thead>
				<tbody>
					{rows.map((row) => (
						<tr
							key={row.id}
							onClick={() => openTranscript(row)}
							onKeyDown={(event) =>
								handleKeyboardOpen(event, () => openTranscript(row))
							}
							tabIndex={0}
						>
							<td className="col-num">{row.id}</td>
							<td className="col-title">
								{row.is_partial ? (
									<span className="chip warn">partial</span>
								) : null}
								<a
									className="link-button table-title"
									href={routeToHref({
										page: "transcript",
										params: { id: row.id },
									})}
									onClick={(event) => {
										event.stopPropagation();
										handleRouteAnchorClick(
											event,
											{ page: "transcript", params: { id: row.id } },
											navigate,
										);
									}}
								>
									{row.title}
								</a>
							</td>
							<td className="col-tags">
								<TagList row={row} navigate={navigate} className="row-tags" />
							</td>
							<td className="col-meta col-len">
								{formatDuration(row.duration_seconds)}
							</td>
							<td className="col-meta col-time">
								{formatDate(row.created_at)}
							</td>
							<td className="col-actions">
								<RowLinks
									row={row}
									onDelete={onDelete}
									busy={deleteBusyId === row.id}
								/>
								<span className="sr-only">
									{formatUsdCost(row.vast_cost, displayCurrency)}
								</span>
							</td>
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
	navigate,
	onDelete,
	deleteBusyId,
}: {
	rows: LibraryRow[];
	displayCurrency: DisplayCurrency;
	navigate: (route: Route) => void;
	onDelete: (row: LibraryRow) => void;
	deleteBusyId: number | null;
}) {
	return (
		<div className="lib-feed">
			{rows.map((row) => (
				<div className="feed-item" key={row.id}>
					<div className="feed-num">#{row.id}</div>
					<div className="feed-body">
						<div className="feed-meta-top">
							<span className="tnum">{formatDate(row.created_at)}</span>
							<span className="sep">·</span>
							<span>{formatDuration(row.duration_seconds)}</span>
							<span className="sep">·</span>
							<span>{row.lang ?? "lang n/a"}</span>
							<span className="sep">·</span>
							<span>{formatUsdCost(row.vast_cost, displayCurrency)}</span>
							{row.is_partial ? (
								<>
									<span className="sep">·</span>
									<span className="chip warn">partial</span>
								</>
							) : null}
						</div>
						<h2 className="feed-title">
							<a
								className="link-button"
								href={routeToHref({
									page: "transcript",
									params: { id: row.id },
								})}
								onClick={(event) =>
									handleRouteAnchorClick(
										event,
										{ page: "transcript", params: { id: row.id } },
										navigate,
									)
								}
							>
								{row.title}
							</a>
						</h2>
						<p className="feed-excerpt">{previewSummary(row)}</p>
						<TagList row={row} navigate={navigate} />
						<RowLinks
							row={row}
							onDelete={onDelete}
							busy={deleteBusyId === row.id}
						/>
					</div>
				</div>
			))}
		</div>
	);
}

function LibCards({
	rows,
	displayCurrency,
	navigate,
	onDelete,
	deleteBusyId,
}: {
	rows: LibraryRow[];
	displayCurrency: DisplayCurrency;
	navigate: (route: Route) => void;
	onDelete: (row: LibraryRow) => void;
	deleteBusyId: number | null;
}) {
	return (
		<div className="lib-cards">
			{rows.map((row) => (
				<div className="card" key={row.id}>
					<div className="card-meta-top">
						<span>#{row.id}</span>
						<span className="sep">·</span>
						<span>{formatDate(row.created_at)}</span>
						<span className="sep">·</span>
						<span>{formatDuration(row.duration_seconds)}</span>
						{row.is_partial ? <span className="chip warn">partial</span> : null}
					</div>
					<h3 className="card-title">
						<a
							className="link-button"
							href={routeToHref({
								page: "transcript",
								params: { id: row.id },
							})}
							onClick={(event) =>
								handleRouteAnchorClick(
									event,
									{ page: "transcript", params: { id: row.id } },
									navigate,
								)
							}
						>
							{row.title}
						</a>
					</h3>
					<p className="card-excerpt">{previewSummary(row)}</p>
					<div className="card-foot">
						<TagList row={row} navigate={navigate} />
					</div>
					<div className="card-actions">
						<RowMeta row={row} displayCurrency={displayCurrency} />
						<RowLinks
							row={row}
							onDelete={onDelete}
							busy={deleteBusyId === row.id}
						/>
					</div>
				</div>
			))}
		</div>
	);
}

function TagList({
	row,
	navigate,
	className = "feed-tags",
}: {
	row: LibraryRow;
	navigate: (route: Route) => void;
	className?: string;
}) {
	if (row.tags === null || row.tags.length === 0) {
		return <span className="muted">untagged</span>;
	}
	return (
		<div className={className}>
			{row.tags.map((tag) => {
				const tagRoute: Route = { page: "library", params: { tag } };
				return (
					<a
						className="tag tag-button"
						key={tag}
						href={routeToHref(tagRoute)}
						onClick={(event) => {
							event.stopPropagation();
							handleRouteAnchorClick(event, tagRoute, navigate);
						}}
					>
						{tag}
					</a>
				);
			})}
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
			{row.source_url !== null ? (
				<a
					href={row.source_url}
					target="_blank"
					rel="noreferrer"
					onClick={(event) => event.stopPropagation()}
				>
					{row.source_label ?? "Source"}
				</a>
			) : null}
			<span onClickCapture={(event) => event.stopPropagation()}>
				<PrivateShareLinks
					id={row.id}
					copyKinds={pageCopyKinds}
					targetKinds={
						row.is_partial ? partialShareTargetKinds : completeShareTargetKinds
					}
				/>
			</span>
			<button
				type="button"
				className="link-button danger-link"
				onClick={(event) => {
					event.stopPropagation();
					onDelete(row);
				}}
				disabled={busy}
			>
				{busy ? "Deleting" : "Delete"}
			</button>
		</div>
	);
}
