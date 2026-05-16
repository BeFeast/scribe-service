import React from "react";

import { ConfirmDialog } from "../components/ConfirmDialog";
import type { DisplayCurrency } from "../lib/currency";
import { formatUsdCost } from "../lib/currency";

type TranscriptRecord = {
	id: number;
	video_id: string;
	title: string;
	tags?: string[] | null;
	duration_seconds?: number | null;
	lang?: string | null;
	summary_shortlink?: string | null;
	transcript_shortlink?: string | null;
	created_at: string;
	job_id: number;
	transcript_md: string;
	summary_md?: string | null;
	vast_cost?: number | null;
};

type TranscriptProps = {
	id?: number;
	displayCurrency: DisplayCurrency;
	navigate: (route: {
		page: "library" | "transcript" | "queue" | "job" | "ops" | "settings";
		params: { id?: number; tag?: string };
	}) => void;
};

type MarkdownBlock =
	| { type: "heading"; level: 1 | 2 | 3 | 4; text: string }
	| { type: "paragraph"; text: string }
	| { type: "quote"; text: string }
	| { type: "code"; text: string }
	| { type: "list"; ordered: boolean; items: string[] };

type InlineToken =
	| { type: "text"; text: string }
	| { type: "code"; text: string }
	| { type: "strong"; text: string }
	| { type: "em"; text: string };

const COPY_RESET_MS = 1400;

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

function parseMd(markdown: string): MarkdownBlock[] {
	const lines = stripFrontmatter(markdown).replace(/\r\n/g, "\n").split("\n");
	const blocks: MarkdownBlock[] = [];
	let index = 0;

	while (index < lines.length) {
		const line = lines[index] ?? "";
		const trimmed = line.trim();
		if (trimmed === "") {
			index += 1;
			continue;
		}

		if (trimmed.startsWith("```")) {
			const codeLines: string[] = [];
			index += 1;
			while (index < lines.length && !lines[index].trim().startsWith("```")) {
				codeLines.push(lines[index]);
				index += 1;
			}
			blocks.push({ type: "code", text: codeLines.join("\n") });
			index += 1;
			continue;
		}

		const heading = /^(#{1,4})\s+(.+)$/.exec(trimmed);
		if (heading !== null) {
			blocks.push({
				type: "heading",
				level: heading[1].length as 1 | 2 | 3 | 4,
				text: heading[2],
			});
			index += 1;
			continue;
		}

		if (trimmed.startsWith(">")) {
			const quoteLines: string[] = [];
			while (index < lines.length && lines[index].trim().startsWith(">")) {
				quoteLines.push(lines[index].trim().replace(/^>\s?/, ""));
				index += 1;
			}
			blocks.push({ type: "quote", text: quoteLines.join(" ") });
			continue;
		}

		const listMatch = /^((?:[-*])|\d+[.)])\s+(.+)$/.exec(trimmed);
		if (listMatch !== null) {
			const ordered = /^\d+[.)]$/.test(listMatch[1]);
			const items: string[] = [];
			while (index < lines.length) {
				const current = lines[index].trim();
				const item = /^((?:[-*])|\d+[.)])\s+(.+)$/.exec(current);
				if (item === null || /^\d+[.)]$/.test(item[1]) !== ordered) {
					break;
				}
				items.push(item[2]);
				index += 1;
			}
			blocks.push({ type: "list", ordered, items });
			continue;
		}

		const paragraph: string[] = [];
		while (index < lines.length) {
			const current = lines[index].trim();
			if (
				current === "" ||
				current.startsWith("```") ||
				/^(#{1,4})\s+/.test(current) ||
				current.startsWith(">") ||
				/^((?:[-*])|\d+[.)])\s+/.test(current)
			) {
				break;
			}
			paragraph.push(current);
			index += 1;
		}
		blocks.push({ type: "paragraph", text: paragraph.join(" ") });
	}

	return blocks;
}

function inline(text: string): InlineToken[] {
	const tokens: InlineToken[] = [];
	const pattern = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g;
	let cursor = 0;
	for (const match of text.matchAll(pattern)) {
		const value = match[0];
		if (match.index > cursor) {
			tokens.push({ type: "text", text: text.slice(cursor, match.index) });
		}
		if (value.startsWith("`")) {
			tokens.push({ type: "code", text: value.slice(1, -1) });
		} else if (value.startsWith("**") || value.startsWith("__")) {
			tokens.push({ type: "strong", text: value.slice(2, -2) });
		} else {
			tokens.push({ type: "em", text: value.slice(1, -1) });
		}
		cursor = match.index + value.length;
	}
	if (cursor < text.length) {
		tokens.push({ type: "text", text: text.slice(cursor) });
	}
	return tokens;
}

function Inline({ text }: { text: string }) {
	return inline(text).map((token, index) => {
		const key = `${token.type}-${index}-${token.text.slice(0, 8)}`;
		if (token.type === "code") {
			return <code key={key}>{token.text}</code>;
		}
		if (token.type === "strong") {
			return <strong key={key}>{token.text}</strong>;
		}
		if (token.type === "em") {
			return <em key={key}>{token.text}</em>;
		}
		return <React.Fragment key={key}>{token.text}</React.Fragment>;
	});
}

export function Markdown({ body }: { body: string }) {
	const blocks = React.useMemo(() => parseMd(body), [body]);
	return (
		<div className="prose transcript-markdown">
			{blocks.map((block, index) => {
				const key = `${block.type}-${index}`;
				if (block.type === "heading") {
					const Heading = `h${block.level}` as "h1" | "h2" | "h3" | "h4";
					return (
						<Heading key={key}>
							<Inline text={block.text} />
						</Heading>
					);
				}
				if (block.type === "quote") {
					return (
						<blockquote key={key}>
							<Inline text={block.text} />
						</blockquote>
					);
				}
				if (block.type === "code") {
					return (
						<pre key={key}>
							<code>{block.text}</code>
						</pre>
					);
				}
				if (block.type === "list") {
					const ListTag = block.ordered ? "ol" : "ul";
					return (
						<ListTag key={key}>
							{block.items.map((item) => (
								<li key={`${key}-${item}`}>
									<Inline text={item} />
								</li>
							))}
						</ListTag>
					);
				}
				return (
					<p key={key}>
						<Inline text={block.text} />
					</p>
				);
			})}
		</div>
	);
}

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

function transcriptExcerpt(markdown: string): string {
	const stripped = stripFrontmatter(markdown).trim();
	if (stripped.length <= 3200) {
		return stripped;
	}
	return `${stripped.slice(0, 3200).trimEnd()}\n\n...`;
}

function useCopy() {
	const [copied, setCopied] = React.useState<string | null>(null);
	const timer = React.useRef<number | null>(null);

	React.useEffect(() => {
		return () => {
			if (timer.current !== null) {
				window.clearTimeout(timer.current);
			}
		};
	}, []);

	return {
		copied,
		copy: React.useCallback(async (key: string, text: string) => {
			await navigator.clipboard.writeText(text);
			setCopied(key);
			if (timer.current !== null) {
				window.clearTimeout(timer.current);
			}
			timer.current = window.setTimeout(() => setCopied(null), COPY_RESET_MS);
		}, []),
	};
}

export function PartialNotice({
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
			<div>
				<p className="section-label">Partial transcript</p>
				<h2>Summary is not available yet</h2>
				<p>
					Whisper finished and saved the transcript, but the summarizer did not
					produce a summary for this row.
				</p>
				{error !== null ? <p className="inline-error">{error}</p> : null}
			</div>
			<button
				className="btn primary"
				type="button"
				onClick={onRun}
				disabled={busy}
			>
				{busy ? <span className="spinner" aria-hidden="true" /> : null}
				{busy ? "Running" : "Run summarizer"}
			</button>
		</section>
	);
}

export function Transcript({ id, displayCurrency, navigate }: TranscriptProps) {
	const [record, setRecord] = React.useState<TranscriptRecord | null>(null);
	const [loading, setLoading] = React.useState(true);
	const [error, setError] = React.useState<string | null>(null);
	const [regenerating, setRegenerating] = React.useState(false);
	const [deleting, setDeleting] = React.useState(false);
	const [confirmDeleteOpen, setConfirmDeleteOpen] = React.useState(false);
	const [regenerateError, setRegenerateError] = React.useState<string | null>(
		null,
	);
	const { copied, copy } = useCopy();

	const load = React.useCallback(
		async (signal?: AbortSignal) => {
			if (id === undefined) {
				setError("Transcript id is missing.");
				setLoading(false);
				return;
			}
			setLoading(true);
			setError(null);
			const response = await fetch(`/transcripts/${id}`, {
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
		[id],
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
			const response = await fetch(`/transcripts/${id}/resummarize`, {
				method: "POST",
				headers: { Accept: "application/json" },
			});
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
	}, [id, load]);

	const deleteTranscript = React.useCallback(async () => {
		if (record === null || deleting) {
			return;
		}
		setDeleting(true);
		setError(null);
		try {
			const response = await fetch(`/admin/transcripts/${record.id}`, {
				method: "DELETE",
			});
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
	}, [deleting, navigate, record]);

	if (loading) {
		return (
			<section className="pane transcript-detail">
				<div className="loading-row">
					<span className="spinner" aria-hidden="true" />
					<span>Loading transcript</span>
				</div>
			</section>
		);
	}

	if (error !== null || record === null) {
		return (
			<section className="pane transcript-detail">
				<div className="failure-row">
					<p className="err-title">Transcript unavailable</p>
					<p className="err-msg">{error ?? "No transcript loaded."}</p>
					<button
						className="btn"
						type="button"
						onClick={() => navigate({ page: "library", params: {} })}
					>
						Back to library
					</button>
				</div>
			</section>
		);
	}

	const created = formatDate(record.created_at);
	const summaryBody = record.summary_md ?? "";
	const excerpt = transcriptExcerpt(record.transcript_md);

	return (
		<section className="pane transcript-detail">
			<header className="pane-header transcript-hero">
				<div>
					<p className="eyebrow">Transcript #{record.id}</p>
					<h1 className="detail-h1">{record.title}</h1>
					<div className="detail-meta meta-strip">
						<span>{record.lang ?? "lang unknown"}</span>
						<span>{formatDuration(record.duration_seconds)}</span>
						<span>{created}</span>
						<a href={`https://youtu.be/${record.video_id}`}>youtube</a>
						<a href={`/transcripts/${record.id}/summary.md`}>summary.md</a>
						<a href={`/transcripts/${record.id}/transcript.md`}>
							transcript.md
						</a>
						{record.summary_shortlink ? (
							<a href={record.summary_shortlink}>shortlink</a>
						) : null}
					</div>
				</div>
				<button
					type="button"
					className="btn ghost danger-button"
					onClick={() => setConfirmDeleteOpen(true)}
					disabled={deleting}
				>
					{deleting ? "Deleting" : "Delete"}
				</button>
			</header>

			{record.tags && record.tags.length > 0 ? (
				<nav className="detail-tags" aria-label="Transcript tags">
					{record.tags.map((tag) => (
						<button
							className="tag"
							key={tag}
							type="button"
							onClick={() => navigate({ page: "library", params: { tag } })}
						>
							{tag}
						</button>
					))}
				</nav>
			) : null}

			{record.summary_md === null || record.summary_md === undefined ? (
				<PartialNotice
					onRun={regenerate}
					busy={regenerating}
					error={regenerateError}
				/>
			) : (
				<section className="detail-section">
					<div className="section-head">
						<div>
							<p className="section-label">Summary</p>
							{regenerateError !== null ? (
								<p className="inline-error">{regenerateError}</p>
							) : null}
						</div>
						<div className="section-actions">
							<button
								className="btn ghost"
								type="button"
								onClick={() => void copy("summary", summaryBody)}
							>
								{copied === "summary" ? "copied!" : "Copy"}
							</button>
							<button
								className="btn"
								type="button"
								onClick={regenerate}
								disabled={regenerating}
							>
								{regenerating ? (
									<span className="spinner" aria-hidden="true" />
								) : null}
								{regenerating ? "Regenerating" : "Regenerate"}
							</button>
						</div>
					</div>
					<div className={regenerating ? "body-dimmed" : undefined}>
						<Markdown body={summaryBody} />
					</div>
				</section>
			)}

			<section className="detail-section">
				<div className="section-head">
					<div>
						<p className="section-label">Transcript excerpt</p>
					</div>
					<button
						className="btn ghost"
						type="button"
						onClick={() => void copy("transcript", record.transcript_md)}
					>
						{copied === "transcript" ? "copied!" : "Copy"}
					</button>
				</div>
				<div className="transcript-body">
					<Markdown body={excerpt} />
				</div>
			</section>

			<footer className="detail-footer">
				<span>job_id: {record.job_id}</span>
				<span>video_id: {record.video_id}</span>
				<span>vast_cost: {formatUsdCost(record.vast_cost, displayCurrency)}</span>
				<span>created: {created}</span>
			</footer>

			{confirmDeleteOpen ? (
				<ConfirmDialog
					title="Delete transcript"
					body={`Delete "${record.title}"? This also removes its job record.`}
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
