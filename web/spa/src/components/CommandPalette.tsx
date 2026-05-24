import React from "react";

import { CMDK_OPEN_EVENT } from "../constants";
import { useAuth } from "../hooks/useAuth";
import type { Route, RoutePage } from "../hooks/useRoute";
import { isAuthStatus } from "../lib/auth";

type Navigate = (route: Route) => void;

type CommandPaletteProps = {
	navigate: Navigate;
};

type IconProps = {
	size?: number;
};

type LibraryRow = {
	id: number;
	video_id: string;
	title: string;
	tags: string[] | null;
	duration_seconds: number | null;
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

type JobView = {
	job_id: number;
	video_id: string;
	status: string;
	deduplicated?: boolean;
};

type SubmitState =
	| { state: "idle" }
	| { state: "submitting" }
	| { state: "success"; job: JobView }
	| { state: "error"; message: string };

type LoadState =
	| { state: "idle" }
	| { state: "loading" }
	| { state: "auth" }
	| { state: "error"; message: string };

type SectionItem = {
	section: string;
};

type ResultItem = {
	type: "transcript" | "job" | "cmd";
	key: string;
	title: string;
	hint: string;
	glyph?: string;
	keywords: string;
	onPick: () => void;
};

type EmptyItem = {
	type: "empty";
	key: string;
	title: string;
	hint: string;
	keywords: string;
};

type PaletteItem = SectionItem | ResultItem | EmptyItem;

const YOUTUBE_VIDEO_ID_RE = /^[A-Za-z0-9_-]{11}$/;
const YOUTUBE_HOSTS = new Set([
	"youtube.com",
	"www.youtube.com",
	"m.youtube.com",
	"music.youtube.com",
]);

const NAV_ITEMS: Array<{
	page: RoutePage;
	title: string;
	glyph: string;
	hint: string;
	keywords: string;
}> = [
	{
		page: "library",
		title: "Go to library",
		glyph: "L",
		hint: "G L",
		keywords: "library transcripts notes",
	},
	{
		page: "queue",
		title: "Go to queue",
		glyph: "Q",
		hint: "G Q",
		keywords: "queue jobs pipeline",
	},
	{
		page: "ops",
		title: "Go to ops dashboard",
		glyph: "O",
		hint: "G O",
		keywords: "ops metrics spend workers",
	},
	{
		page: "settings",
		title: "Go to settings",
		glyph: "S",
		hint: "G S",
		keywords: "settings config preferences",
	},
];

function Icon({
	size = 16,
	children,
}: React.PropsWithChildren<IconProps>): React.ReactElement {
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
			aria-hidden="true"
		>
			{children}
		</svg>
	);
}

function IconSearch(props: IconProps): React.ReactElement {
	return (
		<Icon {...props}>
			<circle cx="7" cy="7" r="4.5" />
			<path d="M10.5 10.5l3 3" />
		</Icon>
	);
}

function IconPlus(props: IconProps): React.ReactElement {
	return (
		<Icon {...props}>
			<path d="M8 3v10M3 8h10" />
		</Icon>
	);
}

function IconCheck(props: IconProps): React.ReactElement {
	return (
		<Icon {...props}>
			<path d="M3 8l3.5 3.5L13 4.5" />
		</Icon>
	);
}

function IconArrow(props: IconProps): React.ReactElement {
	return (
		<Icon {...props}>
			<path d="M3 8h10M9 4l4 4-4 4" />
		</Icon>
	);
}

function IconClock(props: IconProps): React.ReactElement {
	return (
		<Icon {...props}>
			<circle cx="8" cy="8" r="5.5" />
			<path d="M8 5v3l2 1.5" />
		</Icon>
	);
}

function IconWave(props: IconProps): React.ReactElement {
	return (
		<Icon {...props}>
			<path d="M2 8h1M4 5v6M6 6v4M8 3v10M10 5v6M12 6v4M14 8h-1" />
		</Icon>
	);
}

function route(page: RoutePage, id?: number): Route {
	return { page, params: id === undefined ? {} : { id } };
}

function pathSegment(url: URL, index: number): string | null {
	const segment = url.pathname.split("/").filter(Boolean)[index];
	return segment === undefined ? null : decodeURIComponent(segment);
}

function validVideoId(value: string | null): string | null {
	if (value === null || !YOUTUBE_VIDEO_ID_RE.test(value)) {
		return null;
	}
	return value;
}

export function parseVideoUrl(
	value: string,
): { url: string; videoId: string } | null {
	const trimmed = value.trim();

	try {
		const candidate = /^https?:\/\//i.test(trimmed)
			? trimmed
			: `https://${trimmed}`;
		const url = new URL(candidate);
		if (url.protocol !== "http:" && url.protocol !== "https:") {
			return null;
		}
		const host = url.hostname.toLowerCase();
		let videoId: string | null = null;
		if (host === "youtu.be") {
			videoId = validVideoId(pathSegment(url, 0));
		} else if (YOUTUBE_HOSTS.has(host)) {
			const [kind = ""] = url.pathname.split("/").filter(Boolean);
			if (kind === "watch") {
				videoId = validVideoId(url.searchParams.get("v"));
			} else if (kind === "shorts" || kind === "live" || kind === "embed") {
				videoId = validVideoId(pathSegment(url, 1));
			}
		}
		if (videoId === null) {
			return null;
		}
		return { url: url.href, videoId };
	} catch {
		return null;
	}
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

function fmtDuration(seconds: number | null): string {
	if (seconds === null) {
		return "duration n/a";
	}
	const minutes = Math.floor(seconds / 60);
	const rest = Math.floor(seconds % 60);
	return `${minutes}:${String(rest).padStart(2, "0")}`;
}

function fmtRelative(value: string): string {
	const timestamp = new Date(value).getTime();
	if (Number.isNaN(timestamp)) {
		return "date n/a";
	}
	const elapsedSeconds = Math.max(
		0,
		Math.floor((Date.now() - timestamp) / 1000),
	);
	if (elapsedSeconds < 60) {
		return "just now";
	}
	const elapsedMinutes = Math.floor(elapsedSeconds / 60);
	if (elapsedMinutes < 60) {
		return `${elapsedMinutes}m ago`;
	}
	const elapsedHours = Math.floor(elapsedMinutes / 60);
	if (elapsedHours < 24) {
		return `${elapsedHours}h ago`;
	}
	return `${Math.floor(elapsedHours / 24)}d ago`;
}

async function safeJson(response: Response): Promise<unknown> {
	try {
		return await response.json();
	} catch {
		return null;
	}
}

export function isJobView(value: unknown): value is JobView {
	if (typeof value !== "object" || value === null) {
		return false;
	}
	const candidate = value as Partial<JobView>;
	return (
		typeof candidate.job_id === "number" &&
		Number.isFinite(candidate.job_id) &&
		typeof candidate.video_id === "string" &&
		candidate.video_id.length > 0 &&
		typeof candidate.status === "string" &&
		candidate.status.length > 0
	);
}

function submitErrorMessage(status: number, body: unknown): string {
	if (status === 401) {
		return "Sign in is required before submitting from this network.";
	}
	if (status === 403) {
		return "Your account does not have permission to submit jobs.";
	}
	if (status === 422) {
		return "That YouTube URL could not be accepted. Check the link and try again.";
	}
	if (status === 429) {
		return "The daily spend cap has been reached. Try again after the cap resets.";
	}
	const detail =
		typeof body === "object" && body !== null && "detail" in body
			? (body as { detail?: unknown }).detail
			: undefined;
	if (typeof detail === "string" && detail.trim().length > 0) {
		return detail;
	}
	return "Scribe could not submit that job. Try again in a moment.";
}

function isSection(item: PaletteItem): item is SectionItem {
	return "section" in item;
}

function isSelectable(item: PaletteItem): item is ResultItem {
	return !isSection(item) && item.type !== "empty";
}

export function isCommandPaletteShortcut(event: KeyboardEvent): boolean {
	return (
		(event.metaKey || event.ctrlKey) &&
		(event.key.toLowerCase() === "k" || event.code === "KeyK")
	);
}

export function useCommandPalette() {
	const [isOpen, setIsOpen] = React.useState(false);

	React.useEffect(() => {
		const open = () => setIsOpen(true);
		const keydown = (event: KeyboardEvent) => {
			if (isCommandPaletteShortcut(event)) {
				event.preventDefault();
				event.stopPropagation();
				setIsOpen(true);
			}
		};
		document.addEventListener(CMDK_OPEN_EVENT, open);
		window.addEventListener("keydown", keydown, { capture: true });
		return () => {
			document.removeEventListener(CMDK_OPEN_EVENT, open);
			window.removeEventListener("keydown", keydown, { capture: true });
		};
	}, []);

	return {
		isOpen,
		open: React.useCallback(() => setIsOpen(true), []),
		close: React.useCallback(() => setIsOpen(false), []),
	};
}

export function CommandPalette({ navigate }: CommandPaletteProps) {
	const auth = useAuth();
	const { isOpen, close } = useCommandPalette();
	const [query, setQuery] = React.useState("");
	const [selectedIndex, setSelectedIndex] = React.useState(0);
	const [library, setLibrary] = React.useState<LibraryRow[]>([]);
	const [jobs, setJobs] = React.useState<ActiveJob[]>([]);
	const [loadState, setLoadState] = React.useState<LoadState>({
		state: "idle",
	});
	const [submitState, setSubmitState] = React.useState<SubmitState>({
		state: "idle",
	});
	const inputRef = React.useRef<HTMLInputElement>(null);
	const modalRef = React.useRef<HTMLDialogElement>(null);
	const videoUrl = parseVideoUrl(query);

	React.useEffect(() => {
		if (!isOpen) {
			return;
		}
		const controller = new AbortController();
		setQuery("");
		setSelectedIndex(0);
		setSubmitState({ state: "idle" });
		setLoadState({ state: "loading" });
		window.setTimeout(() => inputRef.current?.focus(), 30);
		void Promise.all([
			auth.protectedFetch("/api/library?limit=100", {
				signal: controller.signal,
			}),
			auth.protectedFetch("/api/jobs/active", { signal: controller.signal }),
		])
			.then(async ([libraryResponse, jobsResponse]) => {
				if (
					isAuthStatus(libraryResponse.status) ||
					isAuthStatus(jobsResponse.status)
				) {
					setLibrary([]);
					setJobs([]);
					setLoadState({ state: "auth" });
					auth.maybeAutoSignIn();
					return;
				}
				if (!libraryResponse.ok || !jobsResponse.ok) {
					setLibrary([]);
					setJobs([]);
					setLoadState({
						state: "error",
						message: "Search is temporarily unavailable.",
					});
					return;
				}
				const [libraryBody, jobsBody] = (await Promise.all([
					libraryResponse.json(),
					jobsResponse.json(),
				])) as [LibraryResponse, ActiveJobsResponse];
				setLibrary(libraryBody.rows ?? []);
				setJobs(jobsBody.jobs ?? []);
				setLoadState({ state: "idle" });
			})
			.catch((error: unknown) => {
				if (error instanceof DOMException && error.name === "AbortError") {
					return;
				}
				setLibrary([]);
				setJobs([]);
				setLoadState({
					state: "error",
					message: "Search is temporarily unavailable.",
				});
			});
		return () => controller.abort();
	}, [auth, isOpen]);

	const items = React.useMemo<PaletteItem[]>(() => {
		if (videoUrl !== null) {
			return [];
		}
		const lower = normalize(query);
		const list: PaletteItem[] = [];
		const matchedTranscripts = library
			.filter((transcript) =>
				fuzzyMatch(
					`${transcript.title} ${transcript.video_id} ${(transcript.tags ?? []).join(" ")}`,
					lower,
				),
			)
			.slice(0, 6)
			.map<ResultItem>((transcript) => ({
				type: "transcript",
				key: `t${transcript.id}`,
				title: transcript.title,
				hint: `#${transcript.id} · ${fmtDuration(transcript.duration_seconds)} · ${fmtRelative(transcript.created_at)}`,
				keywords: `${transcript.title} ${transcript.video_id} ${(transcript.tags ?? []).join(" ")}`,
				onPick: () => {
					navigate(route("transcript", transcript.id));
					close();
				},
			}));
		list.push({ section: "Transcripts" });
		if (matchedTranscripts.length > 0) {
			list.push(...matchedTranscripts);
		} else {
			list.push({
				type: "empty",
				key: "empty-transcripts",
				title:
					loadState.state === "loading"
						? "Loading transcripts"
						: "No transcripts found",
				hint:
					loadState.state === "loading"
						? "Fetching the latest library rows"
						: "Try another title, tag, or video id",
				keywords: "",
			});
		}

		const matchedJobs = jobs
			.filter((job) =>
				fuzzyMatch(
					`${job.title ?? ""} ${job.video_id} ${job.status} ${job.source ?? ""}`,
					lower,
				),
			)
			.map<ResultItem>((job) => ({
				type: "job",
				key: `j${job.id}`,
				title: job.title || job.video_id,
				hint: `job ${job.id} · ${job.status}`,
				keywords: `${job.title ?? ""} ${job.video_id} ${job.status} ${job.source ?? ""}`,
				onPick: () => {
					navigate(route("job", job.id));
					close();
				},
			}));
		list.push({ section: "In flight" });
		if (matchedJobs.length > 0) {
			list.push(...matchedJobs);
		} else {
			list.push({
				type: "empty",
				key: "empty-jobs",
				title:
					loadState.state === "loading"
						? "Loading active jobs"
						: "No active jobs",
				hint:
					loadState.state === "loading"
						? "Checking the worker queue"
						: "Paste a YouTube URL to submit one",
				keywords: "",
			});
		}

		list.push({ section: "Navigate" });
		for (const item of NAV_ITEMS) {
			if (!fuzzyMatch(`${item.title} ${item.keywords}`, lower)) {
				continue;
			}
			list.push({
				type: "cmd",
				key: `go-${item.page}`,
				title: item.title,
				glyph: item.glyph,
				hint: item.hint,
				keywords: item.keywords,
				onPick: () => {
					navigate(route(item.page));
					close();
				},
			});
		}

		if (loadState.state === "auth") {
			list.push({ section: "Search status" });
			list.push({
				type: "empty",
				key: "search-auth",
				title: "Sign in required",
				hint: "Authenticate to search transcripts and active jobs",
				keywords: "",
			});
		} else if (loadState.state === "error") {
			list.push({ section: "Search status" });
			list.push({
				type: "empty",
				key: "search-error",
				title: "Search unavailable",
				hint: loadState.message,
				keywords: "",
			});
		}

		return list;
	}, [close, jobs, library, loadState, navigate, query, videoUrl]);

	const selectable = React.useMemo(
		() =>
			items
				.map((item, index) => ({ item, index }))
				.filter((entry): entry is { item: ResultItem; index: number } =>
					isSelectable(entry.item),
				),
		[items],
	);
	const safeSelectedIndex =
		selectable.length === 0
			? 0
			: Math.min(selectedIndex, selectable.length - 1);

	React.useEffect(() => {
		if (selectable.length === 0) {
			setSelectedIndex(0);
			return;
		}
		setSelectedIndex((value) => Math.min(value, selectable.length - 1));
	}, [selectable.length]);

	const submitUrl = async () => {
		if (
			videoUrl === null ||
			submitState.state === "submitting" ||
			submitState.state === "success"
		) {
			return;
		}
		setSubmitState({ state: "submitting" });
		try {
			const response = await auth.protectedFetch("/jobs", {
				method: "POST",
				headers: { "Content-Type": "application/json" },
				body: JSON.stringify({ url: videoUrl.url, source: "manual" }),
			});
			const body = await safeJson(response);
			if (isAuthStatus(response.status)) {
				auth.maybeAutoSignIn();
			}
			if (!response.ok) {
				setSubmitState({
					state: "error",
					message: submitErrorMessage(response.status, body),
				});
				return;
			}
			if (!isJobView(body)) {
				setSubmitState({
					state: "error",
					message:
						"Scribe accepted the request but returned an invalid job response.",
				});
				return;
			}
			setSubmitState({ state: "success", job: body });
		} catch {
			setSubmitState({
				state: "error",
				message: "Scribe could not submit that job. Try again in a moment.",
			});
		}
	};

	const watchPipeline = (jobId: number) => {
		navigate(route("job", jobId));
		close();
	};

	const onQueryChange = (event: React.ChangeEvent<HTMLInputElement>) => {
		setQuery(event.target.value);
		setSelectedIndex(0);
		if (submitState.state !== "submitting") {
			setSubmitState({ state: "idle" });
		}
	};

	const onKeyDown = (event: React.KeyboardEvent<HTMLDialogElement>) => {
		if (event.key === "Escape") {
			event.preventDefault();
			close();
			return;
		}
		if (event.key === "Tab") {
			const focusable = Array.from(
				modalRef.current?.querySelectorAll<HTMLElement>(
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
			if (selectable.length > 0) {
				setSelectedIndex((value) => (value + 1) % selectable.length);
			}
			return;
		}
		if (event.key === "ArrowUp") {
			event.preventDefault();
			if (selectable.length > 0) {
				setSelectedIndex(
					(value) => (value - 1 + selectable.length) % selectable.length,
				);
			}
			return;
		}
		if (event.key === "Enter") {
			event.preventDefault();
			if (videoUrl !== null && submitState.state !== "success") {
				void submitUrl();
				return;
			}
			selectable[safeSelectedIndex]?.item.onPick();
		}
	};

	if (!isOpen) {
		return null;
	}

	return (
		<div className="cmdk-overlay" onMouseDown={close}>
			<dialog
				open
				className="cmdk-modal"
				aria-modal="true"
				aria-label="Command palette"
				ref={modalRef}
				onKeyDown={onKeyDown}
				onMouseDown={(event) => event.stopPropagation()}
			>
				<div className="cmdk-input-row">
					<IconSearch size={16} />
					<input
						ref={inputRef}
						placeholder="Paste a YouTube URL · or search transcripts, jobs, commands..."
						value={query}
						onChange={onQueryChange}
						aria-label="Search commands and transcripts"
					/>
					<span className="kbd">esc</span>
				</div>

				{videoUrl !== null && submitState.state !== "success" ? (
					<div className="cmdk-submit">
						<IconPlus size={18} />
						<div className="label">
							<div className="cmdk-submit-title">Submit job</div>
							<div className="cmdk-submit-meta">
								video_id <span className="tnum">{videoUrl.videoId}</span> ·
								source=manual
							</div>
						</div>
						<button
							type="button"
							className="btn"
							onClick={() => void submitUrl()}
							disabled={submitState.state === "submitting"}
						>
							{submitState.state === "submitting" ? "Submitting" : "Submit"}
							<span className="kbd">↵</span>
						</button>
					</div>
				) : null}

				{submitState.state === "error" ? (
					<div className="cmdk-result cmdk-result-error" role="alert">
						<div className="cmdk-glyph">
							<IconClock size={13} />
						</div>
						<div className="cmdk-result-body">
							<div className="cmdk-title">Submit failed</div>
							<div className="cmdk-hint">{submitState.message}</div>
						</div>
					</div>
				) : null}

				{submitState.state === "success" ? (
					<div className="cmdk-result cmdk-result-ok">
						<div className="cmdk-glyph">
							<IconCheck size={18} />
						</div>
						<div className="cmdk-result-body">
							<div className="cmdk-title">
								Queued as job #{submitState.job.job_id}
							</div>
							<div className="cmdk-hint">
								video_id {submitState.job.video_id} · status{" "}
								{submitState.job.status}
							</div>
						</div>
						<button
							type="button"
							className="btn primary"
							onClick={() => watchPipeline(submitState.job.job_id)}
						>
							Watch pipeline <IconArrow size={12} />
						</button>
					</div>
				) : null}

				<div className="cmdk-list" aria-label="Command results">
					{items.map((item, index) => {
						if (isSection(item)) {
							return (
								<div
									key={`section:${item.section}`}
									className="cmdk-section-label"
								>
									{item.section}
								</div>
							);
						}
						const selectableIndex = selectable.findIndex(
							(entry) => entry.index === index,
						);
						const isSelected = selectableIndex === safeSelectedIndex;
						const content = (
							<>
								<div className="cmdk-glyph">
									{item.type === "transcript" ? (
										<IconWave size={13} />
									) : item.type === "job" ? (
										<span className="live-dot" />
									) : item.type === "cmd" ? (
										<span className="cmdk-cmd-glyph">{item.glyph}</span>
									) : (
										<IconClock size={13} />
									)}
								</div>
								<div className="cmdk-title">{item.title}</div>
								<div className="cmdk-hint">{item.hint}</div>
							</>
						);
						if (item.type === "empty") {
							return (
								<div key={item.key} className="cmdk-item cmdk-item-empty">
									{content}
								</div>
							);
						}
						return (
							<button
								type="button"
								key={item.key}
								className={`cmdk-item ${isSelected ? "sel" : ""}`}
								aria-selected={isSelected}
								onClick={item.onPick}
								onMouseEnter={() => setSelectedIndex(selectableIndex)}
							>
								{content}
							</button>
						);
					})}
				</div>

				<div className="cmdk-foot">
					<span>
						<span className="kbd">↑↓</span> navigate
					</span>
					<span>
						<span className="kbd">↵</span> open
					</span>
					<span>
						<span className="kbd">esc</span> close
					</span>
					<div className="grow" />
					<span className="muted">
						paste any youtube URL for instant submit
					</span>
				</div>
			</dialog>
		</div>
	);
}
