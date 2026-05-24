import React from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import { Markdown } from "../components/Markdown";
import { copyTextToClipboard } from "../components/PrivateShareLinks";
import { useAuth } from "../hooks/useAuth";
import {
	type Route,
	handleRouteAnchorClick,
	routeToHref,
} from "../hooks/useRoute";
import type { DisplayCurrency } from "../lib/currency";
import { formatUsdCost } from "../lib/currency";

type TranscriptRecord = {
	id: number;
	video_id: string;
	title: string;
	tags?: string[] | null;
	duration_seconds?: number | null;
	lang?: string | null;
	source_url?: string | null;
	source_label?: string | null;
	created_at: string;
	job_id: number;
	transcript_md: string;
	summary_md?: string | null;
	vast_cost?: number | null;
};

type TranscriptProps = {
	id?: number;
	displayCurrency: DisplayCurrency;
	navigate: (route: Route) => void;
};

type ShareLinkTarget = "page" | "summary_markdown" | "transcript_markdown";

type ManagedShareLink = {
	id: number;
	target_kind: ShareLinkTarget;
	token_hint: string;
	label?: string | null;
	revoked_at?: string | null;
	expires_at?: string | null;
};

type CopyState = {
	key: string;
	status: "ok" | "err";
	message: string;
};

type IconProps = {
	size?: number;
	style?: React.CSSProperties;
};

const COPY_RESET_MS = 4500;

function Icon({
	size = 16,
	children,
	style,
}: IconProps & { children: React.ReactNode }) {
	return (
		<svg
			width={size}
			height={size}
			viewBox="0 0 16 16"
			fill="none"
			stroke="currentColor"
			strokeWidth="1.5"
			strokeLinecap="round"
			strokeLinejoin="round"
			style={style}
			aria-hidden="true"
		>
			{children}
		</svg>
	);
}

const IconAlert = (props: IconProps) => (
	<Icon {...props}>
		<path d="M8 2l6 11H2L8 2z" />
		<path d="M8 6v3.2" />
		<path d="M8 12h.01" />
	</Icon>
);

const IconCheck = (props: IconProps) => (
	<Icon {...props}>
		<path d="M3 8.5l3 3L13 4" />
	</Icon>
);

const IconClock = (props: IconProps) => (
	<Icon {...props}>
		<circle cx="8" cy="8" r="5.5" />
		<path d="M8 4.8V8l2.1 1.5" />
	</Icon>
);

const IconCopy = (props: IconProps) => (
	<Icon {...props}>
		<rect x="5.5" y="5.5" width="7" height="7" rx="1" />
		<path d="M3.5 10.5h-.4A1.1 1.1 0 012 9.4V3.1A1.1 1.1 0 013.1 2h6.3a1.1 1.1 0 011.1 1.1v.4" />
	</Icon>
);

const IconDownload = (props: IconProps) => (
	<Icon {...props}>
		<path d="M8 2.5v7" />
		<path d="M5 7l3 3 3-3" />
		<path d="M3 12.5h10" />
	</Icon>
);

const IconExternal = (props: IconProps) => (
	<Icon {...props}>
		<path d="M6 4H3.5A1.5 1.5 0 002 5.5v7A1.5 1.5 0 003.5 14h7a1.5 1.5 0 001.5-1.5V10" />
		<path d="M9 2h5v5" />
		<path d="M8 8l6-6" />
	</Icon>
);

const IconLink = (props: IconProps) => (
	<Icon {...props}>
		<path d="M6.8 10.7l-1.2 1.2a2.7 2.7 0 01-3.8-3.8l1.8-1.8a2.7 2.7 0 013.8 0" />
		<path d="M9.2 5.3l1.2-1.2a2.7 2.7 0 013.8 3.8l-1.8 1.8a2.7 2.7 0 01-3.8 0" />
		<path d="M6 10l4-4" />
	</Icon>
);

const IconRefresh = (props: IconProps) => (
	<Icon {...props}>
		<path d="M13 5a5 5 0 00-8.7-1.9L3 4.5" />
		<path d="M3 2v2.5h2.5" />
		<path d="M3 11a5 5 0 008.7 1.9L13 11.5" />
		<path d="M13 14v-2.5h-2.5" />
	</Icon>
);

const IconSparkle = (props: IconProps) => (
	<Icon {...props}>
		<path d="M8 1.8l1.2 3.3L12.5 6.3 9.2 7.5 8 10.8 6.8 7.5 3.5 6.3l3.3-1.2L8 1.8z" />
		<path d="M12.5 10.2l.5 1.2 1.2.5-1.2.5-.5 1.2-.5-1.2-1.2-.5 1.2-.5.5-1.2z" />
	</Icon>
);

const IconWave = (props: IconProps) => (
	<Icon {...props}>
		<path d="M2 8c1.2 0 1.2-3.5 2.4-3.5S5.6 12 6.8 12 8 5.2 9.2 5.2s1.2 5.3 2.4 5.3S12.8 8 14 8" />
	</Icon>
);

const IconX = (props: IconProps) => (
	<Icon {...props}>
		<path d="M4 4l8 8" />
		<path d="M12 4l-8 8" />
	</Icon>
);

function formatDuration(seconds?: number | null): string {
	if (seconds === null || seconds === undefined) {
		return "duration unknown";
	}
	const hours = Math.floor(seconds / 3600);
	const minutes = Math.floor((seconds % 3600) / 60);
	const rest = seconds % 60;
	if (hours > 0) {
		return `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`;
	}
	return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function formatDate(value: string): string {
	return new Intl.DateTimeFormat(undefined, {
		dateStyle: "medium",
		timeStyle: "short",
	}).format(new Date(value));
}

function stripFrontmatter(text: string): string {
	if (!text.startsWith("---")) {
		return text;
	}
	const end = text.indexOf("\n---", 3);
	if (end === -1) {
		return text;
	}
	return text.slice(end + 4).replace(/^\n+/, "");
}

function shareTargetLabel(target: ShareLinkTarget): string {
	switch (target) {
		case "summary_markdown":
			return "Summary .md";
		case "transcript_markdown":
			return "Transcript .md";
		default:
			return "Page";
	}
}

function targetCopyKey(target: ShareLinkTarget): string {
	switch (target) {
		case "summary_markdown":
			return "summary-link";
		case "transcript_markdown":
			return "transcript-link";
		default:
			return "page-link";
	}
}

function useCopyState() {
	const [copyState, setCopyState] = React.useState<CopyState | null>(null);
	const timer = React.useRef<number | null>(null);

	React.useEffect(() => {
		return () => {
			if (timer.current !== null) {
				window.clearTimeout(timer.current);
			}
		};
	}, []);

	const setTimedState = React.useCallback((state: CopyState) => {
		setCopyState(state);
		if (timer.current !== null) {
			window.clearTimeout(timer.current);
		}
		timer.current = window.setTimeout(() => setCopyState(null), COPY_RESET_MS);
	}, []);

	const copy = React.useCallback(
		async (key: string, text: string, okMessage = "Copied") => {
			const copied = await copyTextToClipboard(text);
			setTimedState({
				key,
				status: copied ? "ok" : "err",
				message: copied
					? okMessage
					: "Clipboard blocked. Allow access, then try again.",
			});
			return copied;
		},
		[setTimedState],
	);

	return { copyState, copy, setTimedState };
}

function CopyStatus({
	state,
	stateKey,
	okLabel = "copied",
}: {
	state: CopyState | null;
	stateKey: string;
	okLabel?: string;
}) {
	if (state?.key !== stateKey) {
		return null;
	}
	return (
		<output
			className={`sh-status ${state.status}`}
			aria-live="polite"
			aria-label={state.status === "ok" ? okLabel : "Copy failed"}
		>
			{state.status === "ok" ? <IconCheck size={11} /> : <IconX size={11} />}
			{state.message}
		</output>
	);
}

function ShareSheet({
	transcript,
	summaryBody,
	copyState,
	copy,
	setTimedState,
	onClose,
}: {
	transcript: TranscriptRecord;
	summaryBody: string | null;
	copyState: CopyState | null;
	copy: (key: string, text: string, okMessage?: string) => Promise<boolean>;
	setTimedState: (state: CopyState) => void;
	onClose: () => void;
}) {
	const auth = useAuth();
	const ref = React.useRef<HTMLDivElement>(null);
	const [links, setLinks] = React.useState<ManagedShareLink[]>([]);
	const [busyKey, setBusyKey] = React.useState<string | null>(null);
	const activeLinks = links.filter((link) => link.revoked_at == null);
	const activePageLink = activeLinks.find(
		(link) => link.target_kind === "page",
	);

	const loadLinks = React.useCallback(async () => {
		const response = await auth.protectedFetch(
			`/api/transcripts/${transcript.id}/share-links`,
			{ cache: "no-store" },
		);
		if (!response.ok) {
			throw new Error(`Share links failed (HTTP ${response.status})`);
		}
		setLinks((await response.json()) as ManagedShareLink[]);
	}, [auth, transcript.id]);

	React.useEffect(() => {
		loadLinks().catch(() => setLinks([]));
	}, [loadLinks]);

	React.useEffect(() => {
		function onDoc(event: MouseEvent) {
			if (ref.current !== null && !ref.current.contains(event.target as Node)) {
				onClose();
			}
		}
		function onKey(event: KeyboardEvent) {
			if (event.key === "Escape") {
				onClose();
			}
		}
		const timeout = window.setTimeout(
			() => document.addEventListener("click", onDoc),
			0,
		);
		document.addEventListener("keydown", onKey);
		return () => {
			window.clearTimeout(timeout);
			document.removeEventListener("click", onDoc);
			document.removeEventListener("keydown", onKey);
		};
	}, [onClose]);

	async function createAndCopy(target: ShareLinkTarget) {
		const key = targetCopyKey(target);
		setBusyKey(key);
		try {
			const response = await auth.protectedFetch(
				`/api/transcripts/${transcript.id}/share-links`,
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						target_kind: target,
						label: shareTargetLabel(target),
					}),
				},
			);
			if (!response.ok) {
				throw new Error(`HTTP ${response.status}`);
			}
			const created = (await response.json()) as ManagedShareLink & {
				share_url: string;
			};
			await copy(key, created.share_url, "link copied");
			await loadLinks();
		} catch {
			setTimedState({
				key,
				status: "err",
				message: "Could not create link. Try again.",
			});
		} finally {
			setBusyKey(null);
		}
	}

	async function revoke(link: ManagedShareLink) {
		const key = `revoke:${link.id}`;
		setBusyKey(key);
		try {
			const response = await auth.protectedFetch(
				`/api/share-links/${link.id}/revoke`,
				{ method: "POST" },
			);
			if (!response.ok) {
				throw new Error(`HTTP ${response.status}`);
			}
			setTimedState({ key, status: "ok", message: "revoked" });
			await loadLinks();
		} catch {
			setTimedState({
				key,
				status: "err",
				message: "Revoke failed. Try again.",
			});
		} finally {
			setBusyKey(null);
		}
	}

	function markDownload(kind: "summary" | "transcript") {
		setTimedState({
			key: `dl:${kind}`,
			status: "ok",
			message: "download started",
		});
	}

	return (
		<div className="share-sheet" ref={ref}>
			<div className="sh-hd">
				<span className="lbl">Share</span>
				<div className="spacer" />
				<span className="vis public">
					<span className="vis-dot" />
					managed links
				</span>
			</div>

			<div className="sh-url">
				<span className="scheme">private link</span>
				<span className="path">
					{activePageLink === undefined
						? "create a page link"
						: `...${activePageLink.token_hint}`}
				</span>
				<button
					className="btn primary"
					type="button"
					onClick={() => void createAndCopy("page")}
					disabled={busyKey !== null}
				>
					{busyKey === "page-link" ? (
						<span className="spinner" aria-hidden="true" />
					) : copyState?.key === "page-link" && copyState.status === "ok" ? (
						<IconCheck size={12} />
					) : (
						<IconCopy size={12} />
					)}
					{copyState?.key === "page-link" && copyState.status === "ok"
						? "Copied"
						: "Copy link"}
				</button>
			</div>
			<CopyStatus state={copyState} stateKey="page-link" />

			<div className="sh-section">
				<div className="sh-section-label">Copy as Markdown</div>
				<button
					className="sh-item"
					type="button"
					onClick={() => void copy("summary", summaryBody ?? "")}
					disabled={summaryBody === null}
				>
					<div className="sh-glyph">
						<IconSparkle size={14} />
					</div>
					<div className="sh-text">
						<div className="sh-title">Summary</div>
						<div className="sh-sub">
							{summaryBody === null
								? "summary not available"
								: `~${Math.round(summaryBody.length / 4)} tokens`}
						</div>
					</div>
					<CopyStatus
						state={copyState}
						stateKey="summary"
						okLabel="Summary copied"
					/>
					{copyState?.key !== "summary" ? (
						<span className="sh-keys">
							<span className="kbd">Copy</span>
						</span>
					) : null}
				</button>
				<button
					className="sh-item"
					type="button"
					onClick={() => void copy("transcript", transcript.transcript_md)}
				>
					<div className="sh-glyph">
						<IconWave size={14} />
					</div>
					<div className="sh-text">
						<div className="sh-title">Transcript</div>
						<div className="sh-sub">
							{formatDuration(transcript.duration_seconds)} /{" "}
							{transcript.lang ?? "lang unknown"}
						</div>
					</div>
					<CopyStatus
						state={copyState}
						stateKey="transcript"
						okLabel="Transcript copied"
					/>
					{copyState?.key !== "transcript" ? (
						<span className="sh-keys">
							<span className="kbd">Copy</span>
						</span>
					) : null}
				</button>
			</div>

			<div className="sh-section">
				<div className="sh-section-label">Download</div>
				<a
					className="sh-item"
					href={`/transcripts/${transcript.id}/summary.md`}
					download
					onClick={() => markDownload("summary")}
					aria-disabled={summaryBody === null}
				>
					<div className="sh-glyph">
						<IconDownload size={13} />
					</div>
					<div className="sh-text">
						<div className="sh-title">summary.md</div>
						<div className="sh-sub">
							/transcripts/{transcript.id}/summary.md
						</div>
					</div>
					<CopyStatus state={copyState} stateKey="dl:summary" />
				</a>
				<a
					className="sh-item"
					href={`/transcripts/${transcript.id}/transcript.md`}
					download
					onClick={() => markDownload("transcript")}
				>
					<div className="sh-glyph">
						<IconDownload size={13} />
					</div>
					<div className="sh-text">
						<div className="sh-title">transcript.md</div>
						<div className="sh-sub">
							/transcripts/{transcript.id}/transcript.md
						</div>
					</div>
					<CopyStatus state={copyState} stateKey="dl:transcript" />
				</a>
			</div>

			<div className="sh-section">
				<div className="sh-section-label">Managed links</div>
				{activeLinks.length === 0 ? (
					<div className="sh-empty">No active share links yet.</div>
				) : null}
				{activeLinks.map((link) => (
					<div className="sh-item" key={link.id}>
						<div className="sh-glyph">
							<IconLink size={13} />
						</div>
						<div className="sh-text">
							<div className="sh-title">
								{link.label ?? shareTargetLabel(link.target_kind)}
							</div>
							<div className="sh-sub">...{link.token_hint}</div>
						</div>
						<button
							className="btn ghost"
							type="button"
							onClick={() => void revoke(link)}
							disabled={busyKey !== null}
						>
							{busyKey === `revoke:${link.id}` ? "Revoking" : "Revoke"}
						</button>
						<CopyStatus state={copyState} stateKey={`revoke:${link.id}`} />
					</div>
				))}
				<div className="share-link-actions">
					<button
						className="btn ghost"
						type="button"
						onClick={() => void createAndCopy("summary_markdown")}
						disabled={busyKey !== null || summaryBody === null}
					>
						<IconSparkle size={12} />
						Summary link
					</button>
					<button
						className="btn ghost"
						type="button"
						onClick={() => void createAndCopy("transcript_markdown")}
						disabled={busyKey !== null}
					>
						<IconWave size={12} />
						Transcript link
					</button>
				</div>
				<CopyStatus state={copyState} stateKey="summary-link" />
				<CopyStatus state={copyState} stateKey="transcript-link" />
			</div>
		</div>
	);
}

function PartialNotice({
	onRun,
	busy,
	error,
}: {
	onRun: () => void;
	busy: boolean;
	error: string | null;
}) {
	return (
		<section className="partial-notice" aria-live="polite">
			<IconAlert size={18} />
			<div>
				<div className="partial-title">Partial transcript - summary failed</div>
				<p>
					Whisper transcribed this video successfully but the summarizer did not
					produce a summary. The transcript is preserved; rerunning only
					re-summarizes it.
				</p>
				{error !== null ? <p className="inline-error">{error}</p> : null}
			</div>
			<button
				className="btn primary"
				type="button"
				onClick={onRun}
				disabled={busy}
			>
				{busy ? (
					<span className="spinner" aria-hidden="true" />
				) : (
					<IconRefresh size={14} />
				)}
				{busy ? "Summarizing" : "Run summarizer"}
			</button>
		</section>
	);
}

export function Transcript({ id, displayCurrency, navigate }: TranscriptProps) {
	const auth = useAuth();
	const [record, setRecord] = React.useState<TranscriptRecord | null>(null);
	const [loading, setLoading] = React.useState(true);
	const [error, setError] = React.useState<string | null>(null);
	const [regenerating, setRegenerating] = React.useState(false);
	const [deleting, setDeleting] = React.useState(false);
	const [shareOpen, setShareOpen] = React.useState(false);
	const [confirmDeleteOpen, setConfirmDeleteOpen] = React.useState(false);
	const [regenerateError, setRegenerateError] = React.useState<string | null>(
		null,
	);
	const { copyState, copy, setTimedState } = useCopyState();

	const load = React.useCallback(
		async (signal?: AbortSignal) => {
			if (id === undefined) {
				setError("Transcript id is missing.");
				setLoading(false);
				return;
			}
			setLoading(true);
			setError(null);
			const response = await auth.protectedFetch(`/transcripts/${id}`, {
				headers: { Accept: "application/json" },
				cache: "no-store",
				signal,
			});
			if (!response.ok) {
				throw new Error(`Transcript load failed (HTTP ${response.status})`);
			}
			setRecord((await response.json()) as TranscriptRecord);
			setLoading(false);
		},
		[auth, id],
	);

	React.useEffect(() => {
		const abort = new AbortController();
		load(abort.signal).catch((caught: unknown) => {
			if (!abort.signal.aborted) {
				setError(caught instanceof Error ? caught.message : String(caught));
				setLoading(false);
			}
		});
		return () => abort.abort();
	}, [load]);

	const regenerate = React.useCallback(async () => {
		if (id === undefined) {
			return;
		}
		setRegenerating(true);
		setRegenerateError(null);
		try {
			const response = await auth.protectedFetch(
				`/transcripts/${id}/resummarize`,
				{
					method: "POST",
					headers: { Accept: "application/json" },
				},
			);
			if (!response.ok) {
				let detail = `Regenerate failed (HTTP ${response.status})`;
				try {
					const body = (await response.json()) as { detail?: string };
					if (body.detail !== undefined) {
						detail = body.detail;
					}
				} catch (_error) {
					// Non-JSON error body; keep the status-based message.
				}
				throw new Error(detail);
			}
			await load();
		} catch (caught) {
			setRegenerateError(
				caught instanceof Error ? caught.message : String(caught),
			);
		} finally {
			setRegenerating(false);
		}
	}, [auth, id, load]);

	const deleteTranscript = React.useCallback(async () => {
		if (record === null || deleting) {
			return;
		}
		setDeleting(true);
		setError(null);
		try {
			const response = await auth.protectedFetch(
				`/admin/transcripts/${record.id}`,
				{ method: "DELETE" },
			);
			if (!response.ok) {
				throw new Error(`Delete failed (HTTP ${response.status})`);
			}
			setConfirmDeleteOpen(false);
			navigate({ page: "library", params: {} });
		} catch (caught) {
			setError(caught instanceof Error ? caught.message : String(caught));
		} finally {
			setDeleting(false);
		}
	}, [auth, deleting, navigate, record]);

	if (loading) {
		return (
			<section className="pane pane-narrow transcript-detail">
				<div className="loading-row">
					<span className="spinner" aria-hidden="true" />
					<span>Loading transcript</span>
				</div>
			</section>
		);
	}

	if (error !== null || record === null) {
		return (
			<section className="pane pane-narrow transcript-detail">
				<div className="failure-row">
					<p className="err-title">Transcript unavailable</p>
					<p className="err-msg">{error ?? "No transcript loaded."}</p>
					<a
						className="btn"
						href={routeToHref({ page: "library", params: {} })}
						onClick={(event) =>
							handleRouteAnchorClick(
								event,
								{ page: "library", params: {} },
								navigate,
							)
						}
					>
						Back to library
					</a>
				</div>
			</section>
		);
	}

	const created = formatDate(record.created_at);
	const summaryBody = record.summary_md ?? null;
	const libraryRoute: Route = { page: "library", params: {} };

	return (
		<section className="pane pane-narrow transcript-detail">
			<div className="row transcript-top-row">
				<a
					className="back-link"
					href={routeToHref(libraryRoute)}
					onClick={(event) =>
						handleRouteAnchorClick(event, libraryRoute, navigate)
					}
				>
					&larr; Library
				</a>
				<div className="spacer" />
				<div className="share-wrap">
					<button
						className="btn primary"
						type="button"
						onClick={() => setShareOpen((open) => !open)}
						aria-expanded={shareOpen}
					>
						<IconLink size={13} />
						Share
					</button>
					{shareOpen ? (
						<ShareSheet
							transcript={record}
							summaryBody={summaryBody}
							copyState={copyState}
							copy={copy}
							setTimedState={setTimedState}
							onClose={() => setShareOpen(false)}
						/>
					) : null}
				</div>
			</div>

			<div className="mono muted transcript-kicker">
				#{record.id} / transcript
			</div>
			<h1 className="detail-h1">{record.title}</h1>

			<div className="detail-meta">
				<span>{record.lang ?? "lang unknown"}</span>
				<span className="sep">/</span>
				<span>
					<IconClock
						size={12}
						style={{ verticalAlign: "-0.12em", marginRight: "0.2rem" }}
					/>
					{formatDuration(record.duration_seconds)}
				</span>
				<span className="sep">/</span>
				<span>{created}</span>
				{record.source_url ? (
					<>
						<span className="sep">/</span>
						<a href={record.source_url} target="_blank" rel="noreferrer">
							<IconExternal
								size={11}
								style={{ verticalAlign: "-0.12em", marginRight: "0.2rem" }}
							/>
							{record.source_label ?? "Source"}
						</a>
					</>
				) : null}
			</div>

			{record.tags && record.tags.length > 0 ? (
				<nav className="detail-tags" aria-label="Transcript tags">
					{record.tags.map((tag) => {
						const tagRoute: Route = { page: "library", params: { tag } };
						return (
							<a
								className="chip"
								key={tag}
								href={routeToHref(tagRoute)}
								onClick={(event) =>
									handleRouteAnchorClick(event, tagRoute, navigate)
								}
							>
								#{tag}
							</a>
						);
					})}
				</nav>
			) : null}

			{summaryBody === null ? (
				<PartialNotice
					onRun={regenerate}
					busy={regenerating}
					error={regenerateError}
				/>
			) : (
				<>
					<div className="section-label">
						<span>Summary</span>
						<div className="row section-actions">
							<button
								className="btn ghost"
								type="button"
								onClick={() => void copy("summary-main", summaryBody)}
							>
								<IconCopy size={12} />
								{copyState?.key === "summary-main" && copyState.status === "ok"
									? "Copied"
									: "Copy"}
							</button>
							<button
								className="btn ghost"
								type="button"
								onClick={regenerate}
								disabled={regenerating}
							>
								{regenerating ? (
									<span className="spinner" aria-hidden="true" />
								) : (
									<IconRefresh size={12} />
								)}
								{regenerating ? "Regenerating" : "Regenerate"}
							</button>
						</div>
					</div>
					{copyState?.key === "summary-main" && copyState.status === "err" ? (
						<output className="copy-state err" aria-live="polite">
							{copyState.message}
						</output>
					) : null}
					{regenerateError !== null ? (
						<p className="inline-error">{regenerateError}</p>
					) : null}
					<div className={regenerating ? "body-dimmed" : undefined}>
						<Markdown body={summaryBody} />
					</div>
				</>
			)}

			<div className="section-label">
				<span>Transcript</span>
				<div className="row section-actions">
					<span className="mono muted transcript-stat">
						~{Math.max(1, Math.round((record.duration_seconds ?? 60) / 60))} min
						/ {record.lang ?? "lang unknown"}
					</span>
					<a
						className="btn ghost"
						href={`/transcripts/${record.id}/transcript.md`}
						download
					>
						<IconDownload size={12} />
						Download .md
					</a>
				</div>
			</div>
			<div className="transcript-body">
				<Markdown body={stripFrontmatter(record.transcript_md)} />
			</div>

			<div className="hr" />
			<div className="detail-footer">
				<span>
					job_id: <span className="tnum">{record.job_id}</span>
				</span>
				<span>video_id: {record.video_id}</span>
				<span>
					vast_cost: {formatUsdCost(record.vast_cost, displayCurrency)}
				</span>
				<span>created: {record.created_at.replace("T", " ")}</span>
				<a href={`/transcripts/${record.id}/summary.md`}>summary.md</a>
				<a href={`/transcripts/${record.id}/transcript.md`}>transcript.md</a>
			</div>

			<div className="danger-zone">
				<IconAlert size={20} />
				<div className="dz-text">
					<div className="dz-title">Delete transcript</div>
					<div className="dz-sub">
						Removes the transcript and its share links. The owning job (
						{record.job_id}) is kept; resubmitting the same video reruns the
						pipeline.
					</div>
				</div>
				<button
					className="btn danger-button"
					type="button"
					onClick={() => setConfirmDeleteOpen(true)}
					disabled={deleting}
				>
					Delete
				</button>
			</div>

			{confirmDeleteOpen ? (
				<ConfirmDialog
					title="Delete transcript"
					body={`Delete "${record.title}"? This removes the transcript row and its share links.`}
					confirmLabel="Delete"
					busyLabel="Deleting"
					busy={deleting}
					onCancel={() => setConfirmDeleteOpen(false)}
					onConfirm={deleteTranscript}
				/>
			) : null}
		</section>
	);
}
