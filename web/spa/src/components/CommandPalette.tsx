import React from "react";

import { CMDK_OPEN_EVENT } from "../constants";
import type { Route, RoutePage } from "../hooks/useRoute";

type Navigate = (route: Route) => void;

type CommandPaletteProps = {
	navigate: Navigate;
};

type LibraryRow = {
	id: number;
	video_id: string;
	title: string;
	tags: string[] | null;
	created_at: string;
};

type LibraryResponse = {
	rows: LibraryRow[];
};

type ActiveJob = {
	id: number;
	video_id: string;
	url: string;
	title?: string | null;
	status: string;
	source?: string | null;
};

type ActiveJobsResponse = {
	jobs: ActiveJob[];
};

type OpsResponse = {
	vast_spend_24h?: number;
	daily_spend_cap_usd?: number;
};

type JobView = {
	job_id: number;
	video_id: string;
	status: string;
	deduplicated?: boolean;
};

type RecentSubmission = {
	jobId: number;
	videoId: string;
	title: string;
	createdAt: string;
};

type PaletteItem = {
	id: string;
	label: string;
	meta: string;
	kind: "navigate" | "transcript" | "job" | "recent";
	route: Route;
	keywords: string;
};

type SubmitState =
	| { state: "idle" }
	| { state: "submitting" }
	| { state: "success"; job: JobView }
	| { state: "error"; message: string };

const RECENTS_KEY = "scribe.cmdk.recentSubmissions";
const MAX_RECENTS = 5;
const SHORTS_PATH_PREFIX = "/shorts/";

const NAV_ITEMS: Array<{
	page: RoutePage;
	label: string;
	meta: string;
	keywords: string;
}> = [
	{
		page: "library",
		label: "Library",
		meta: "Browse transcripts",
		keywords: "library transcripts notes",
	},
	{
		page: "queue",
		label: "Queue",
		meta: "Active pipeline",
		keywords: "queue jobs pipeline",
	},
	{
		page: "ops",
		label: "Ops",
		meta: "Spend and workers",
		keywords: "ops metrics spend workers",
	},
	{
		page: "settings",
		label: "Settings",
		meta: "Runtime controls",
		keywords: "settings config preferences",
	},
];

function route(page: RoutePage, id?: number): Route {
	return { page, params: id === undefined ? {} : { id } };
}

function parseYouTubeVideoId(value: string): string | null {
	const trimmed = value.trim();
	if (trimmed.length === 0) {
		return null;
	}

	try {
		const candidate = /^https?:\/\//i.test(trimmed)
			? trimmed
			: `https://${trimmed}`;
		const url = new URL(candidate);
		const host = url.hostname.replace(/^www\./, "");
		if (host === "youtu.be") {
			return url.pathname.split("/").filter(Boolean)[0] ?? null;
		}
		if (host === "youtube.com" || host === "m.youtube.com") {
			if (url.pathname === "/watch") {
				return url.searchParams.get("v");
			}
			if (url.pathname.startsWith(SHORTS_PATH_PREFIX)) {
				return (
					url.pathname.slice(SHORTS_PATH_PREFIX.length).split("/")[0] || null
				);
			}
		}
	} catch {
		return null;
	}

	return null;
}

function readRecents(): RecentSubmission[] {
	try {
		const raw = localStorage.getItem(RECENTS_KEY);
		if (!raw) {
			return [];
		}
		const parsed = JSON.parse(raw);
		return Array.isArray(parsed) ? parsed.slice(0, MAX_RECENTS) : [];
	} catch {
		return [];
	}
}

function writeRecents(recents: RecentSubmission[]): void {
	localStorage.setItem(
		RECENTS_KEY,
		JSON.stringify(recents.slice(0, MAX_RECENTS)),
	);
}

function normalize(value: string): string {
	return value.toLowerCase().trim();
}

function fuzzyMatch(text: string, query: string): boolean {
	const haystack = normalize(text);
	const needle = normalize(query);
	if (needle.length === 0) {
		return true;
	}
	if (haystack.includes(needle)) {
		return true;
	}
	let index = 0;
	for (const char of haystack) {
		if (char === needle[index]) {
			index += 1;
			if (index === needle.length) {
				return true;
			}
		}
	}
	return false;
}

function errorMessage(status: number, body: unknown): string {
	const detail =
		typeof body === "object" && body !== null && "detail" in body
			? (body as { detail?: unknown }).detail
			: undefined;
	if (typeof detail === "string") {
		return detail;
	}
	if (status === 422) {
		return "Bad YouTube URL.";
	}
	if (status === 429) {
		return "Daily spend cap reached.";
	}
	return "Could not submit job.";
}

function capRemainingLabel(ops: OpsResponse | null): string {
	if (
		!ops ||
		ops.daily_spend_cap_usd === undefined ||
		ops.daily_spend_cap_usd <= 0
	) {
		return "Cap not enforced";
	}
	const spent = ops.vast_spend_24h ?? 0;
	const remaining = Math.max(0, ops.daily_spend_cap_usd - spent);
	return `$${remaining.toFixed(2)} cap remaining`;
}

export function useCommandPalette() {
	const [isOpen, setIsOpen] = React.useState(false);

	React.useEffect(() => {
		const open = () => setIsOpen(true);
		const keydown = (event: KeyboardEvent) => {
			if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
				event.preventDefault();
				setIsOpen(true);
			}
		};
		document.addEventListener(CMDK_OPEN_EVENT, open);
		document.addEventListener("keydown", keydown);
		return () => {
			document.removeEventListener(CMDK_OPEN_EVENT, open);
			document.removeEventListener("keydown", keydown);
		};
	}, []);

	return {
		isOpen,
		open: React.useCallback(() => setIsOpen(true), []),
		close: React.useCallback(() => setIsOpen(false), []),
	};
}

export function CommandPalette({ navigate }: CommandPaletteProps) {
	const { isOpen, close } = useCommandPalette();
	const [query, setQuery] = React.useState("");
	const [library, setLibrary] = React.useState<LibraryRow[]>([]);
	const [jobs, setJobs] = React.useState<ActiveJob[]>([]);
	const [ops, setOps] = React.useState<OpsResponse | null>(null);
	const [recents, setRecents] = React.useState<RecentSubmission[]>([]);
	const [selectedIndex, setSelectedIndex] = React.useState(0);
	const [submitState, setSubmitState] = React.useState<SubmitState>({
		state: "idle",
	});
	const inputRef = React.useRef<HTMLInputElement>(null);
	const dialogRef = React.useRef<HTMLDialogElement>(null);
	const videoId = parseYouTubeVideoId(query);

	React.useEffect(() => {
		if (!isOpen) {
			return;
		}
		setRecents(readRecents());
		setSubmitState({ state: "idle" });
		setSelectedIndex(0);
		window.setTimeout(() => inputRef.current?.focus(), 0);
		void Promise.all([
			fetch("/api/library?limit=100").then(
				(response) => response.json() as Promise<LibraryResponse>,
			),
			fetch("/api/jobs/active").then(
				(response) => response.json() as Promise<ActiveJobsResponse>,
			),
			fetch("/api/ops").then(
				(response) => response.json() as Promise<OpsResponse>,
			),
		])
			.then(([libraryBody, jobsBody, opsBody]) => {
				setLibrary(libraryBody.rows ?? []);
				setJobs(jobsBody.jobs ?? []);
				setOps(opsBody);
			})
			.catch(() => {
				setLibrary([]);
				setJobs([]);
				setOps(null);
			});
	}, [isOpen]);

	const items = React.useMemo<PaletteItem[]>(() => {
		const navigation = NAV_ITEMS.map((item) => ({
			id: `nav:${item.page}`,
			label: item.label,
			meta: item.meta,
			kind: "navigate" as const,
			route: route(item.page),
			keywords: `${item.label} ${item.meta} ${item.keywords}`,
		}));
		const transcriptItems = library.map((item) => ({
			id: `transcript:${item.id}`,
			label: item.title,
			meta: `Transcript · ${item.tags?.join(", ") || item.video_id}`,
			kind: "transcript" as const,
			route: route("transcript", item.id),
			keywords: `${item.title} ${item.video_id} ${(item.tags ?? []).join(" ")}`,
		}));
		const jobItems = jobs.map((job) => ({
			id: `job:${job.id}`,
			label: job.title || job.video_id,
			meta: `Job · ${job.status}`,
			kind: "job" as const,
			route: route("job", job.id),
			keywords: `${job.title ?? ""} ${job.video_id} ${job.status} ${job.source ?? ""}`,
		}));
		const recentItems = recents.map((recent) => ({
			id: `recent:${recent.jobId}`,
			label: recent.title,
			meta: `Recent submission · job #${recent.jobId}`,
			kind: "recent" as const,
			route: route("job", recent.jobId),
			keywords: `${recent.title} ${recent.videoId} job ${recent.jobId}`,
		}));
		const allItems = [
			...navigation,
			...recentItems,
			...jobItems,
			...transcriptItems,
		];
		if (videoId !== null) {
			return allItems;
		}
		return allItems.filter((item) => fuzzyMatch(item.keywords, query));
	}, [jobs, library, query, recents, videoId]);

	const submitUrl = async () => {
		if (videoId === null || submitState.state === "submitting") {
			return;
		}
		setSubmitState({ state: "submitting" });
		try {
			const response = await fetch("/jobs", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ url: query.trim(), source: "manual" }),
			});
			const body = (await response.json()) as unknown;
			if (!response.ok) {
				setSubmitState({
					state: "error",
					message: errorMessage(response.status, body),
				});
				return;
			}
			const job = body as JobView;
			const nextRecent: RecentSubmission = {
				jobId: job.job_id,
				videoId: job.video_id,
				title: `Queued as job #${job.job_id}`,
				createdAt: new Date().toISOString(),
			};
			const nextRecents = [
				nextRecent,
				...recents.filter((recent) => recent.jobId !== job.job_id),
			].slice(0, MAX_RECENTS);
			setRecents(nextRecents);
			writeRecents(nextRecents);
			setSubmitState({ state: "success", job });
		} catch {
			setSubmitState({ state: "error", message: "Could not submit job." });
		}
	};

	const onQueryChange = (event: React.ChangeEvent<HTMLInputElement>) => {
		setQuery(event.target.value);
		setSelectedIndex(0);
	};

	if (!isOpen) {
		return null;
	}

	const openItem = (item: PaletteItem | undefined) => {
		if (!item) {
			return;
		}
		navigate(item.route);
		close();
	};

	const watchPipeline = (jobId: number) => {
		navigate(route("job", jobId));
		close();
	};

	const onKeyDown = (event: React.KeyboardEvent<HTMLDialogElement>) => {
		if (event.key === "Escape") {
			event.preventDefault();
			close();
			return;
		}
		if (event.key === "Tab") {
			const focusable = Array.from(
				dialogRef.current?.querySelectorAll<HTMLElement>(
					'button, [href], input, [tabindex]:not([tabindex="-1"])',
				) ?? [],
			).filter((node) => !node.hasAttribute("disabled"));
			if (focusable.length === 0) {
				return;
			}
			const first = focusable[0];
			const last = focusable[focusable.length - 1];
			if (event.shiftKey && document.activeElement === first) {
				event.preventDefault();
				last.focus();
			} else if (!event.shiftKey && document.activeElement === last) {
				event.preventDefault();
				first.focus();
			}
			return;
		}
		if (event.key === "ArrowDown") {
			event.preventDefault();
			setSelectedIndex((value) =>
				Math.min(value + 1, Math.max(0, items.length - 1)),
			);
			return;
		}
		if (event.key === "ArrowUp") {
			event.preventDefault();
			setSelectedIndex((value) => Math.max(0, value - 1));
			return;
		}
		if (event.key === "Enter") {
			event.preventDefault();
			if (videoId !== null) {
				void submitUrl();
			} else {
				openItem(items[selectedIndex]);
			}
		}
	};

	return (
		<div className="cmdk-overlay" onMouseDown={close}>
			<dialog
				open
				className="cmdk"
				aria-modal="true"
				aria-label="Command palette"
				ref={dialogRef}
				onKeyDown={onKeyDown}
				onMouseDown={(event) => event.stopPropagation()}
			>
				<input
					ref={inputRef}
					className="cmdk-input"
					value={query}
					onChange={onQueryChange}
					placeholder="Search, jump, or paste a YouTube URL"
					aria-label="Search commands and transcripts"
				/>
				{videoId !== null ? (
					<div className="cmdk-submit">
						<div>
							<span className="cmdk-label">Submit job</span>
							<strong>{videoId}</strong>
							<small>source manual · {capRemainingLabel(ops)}</small>
						</div>
						<button
							type="button"
							className="primary-button"
							onClick={() => void submitUrl()}
							disabled={submitState.state === "submitting"}
						>
							{submitState.state === "submitting" ? "Submitting" : "Submit"}
						</button>
					</div>
				) : null}
				{submitState.state === "error" ? (
					<p className="cmdk-error" role="alert">
						{submitState.message}
					</p>
				) : null}
				{submitState.state === "success" ? (
					<output className="cmdk-success">
						<span>Queued as job #{submitState.job.job_id}</span>
						<button
							type="button"
							onClick={() => watchPipeline(submitState.job.job_id)}
						>
							Watch pipeline →
						</button>
					</output>
				) : null}
				<div className="cmdk-list" aria-label="Command results">
					{items.map((item, index) => (
						<button
							type="button"
							className="cmdk-item"
							key={item.id}
							aria-selected={index === selectedIndex}
							onMouseEnter={() => setSelectedIndex(index)}
							onClick={() => openItem(item)}
						>
							<span>
								<strong>{item.label}</strong>
								<small>{item.meta}</small>
							</span>
							<em>{item.kind}</em>
						</button>
					))}
					{items.length === 0 ? <p className="cmdk-empty">No matches</p> : null}
				</div>
			</dialog>
		</div>
	);
}
