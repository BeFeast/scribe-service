import React from "react";

import { useAuth } from "../hooks/useAuth";
import {
	type Route,
	type RoutePage,
	handleRouteAnchorClick,
	routeToHref,
} from "../hooks/useRoute";
import { isAuthStatus } from "../lib/auth";

type SidebarProps = {
	route: Route;
	navigate: (route: Route) => void;
};

type LibraryResponse = {
	rows?: Array<{ tags?: string[] | null }>;
};

type OpsResponse = {
	queue_depth?: number;
	transcripts_done?: number;
	transcripts_partial?: number;
	worker_pool?: {
		active?: number;
		total?: number;
	};
};

type TagCount = {
	tag: string;
	count: number;
};

type PipelineStats = {
	queueDepth: number;
	done: number;
	partial: number;
	workers: string;
};

type SidebarStatus = "loading" | "ready" | "auth-required" | "unavailable";

const navItems: Array<{ page: RoutePage; label: string }> = [
	{ page: "library", label: "Library" },
	{ page: "queue", label: "Queue" },
	{ page: "ops", label: "Ops" },
	{ page: "settings", label: "Settings" },
];

function tagCountsFromLibrary(body: LibraryResponse): TagCount[] {
	const counts = new Map<string, number>();
	for (const row of body.rows ?? []) {
		for (const tag of row.tags ?? []) {
			counts.set(tag, (counts.get(tag) ?? 0) + 1);
		}
	}
	return [...counts.entries()]
		.map(([tag, count]) => ({ tag, count }))
		.sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag))
		.slice(0, 8);
}

function pipelineFromOps(body: OpsResponse): PipelineStats {
	const active = body.worker_pool?.active ?? 0;
	const capacity = body.worker_pool?.total ?? 0;
	return {
		queueDepth: body.queue_depth ?? 0,
		done: body.transcripts_done ?? 0,
		partial: body.transcripts_partial ?? 0,
		workers: `${active}/${capacity}`,
	};
}

export function Sidebar({ route, navigate }: SidebarProps) {
	const auth = useAuth();
	const [tags, setTags] = React.useState<TagCount[]>([]);
	const [pipeline, setPipeline] = React.useState<PipelineStats | null>(null);
	const [status, setStatus] = React.useState<SidebarStatus>("loading");

	React.useEffect(() => {
		const abort = new AbortController();

		async function loadSidebarData() {
			try {
				const [libraryResponse, opsResponse] = await Promise.all([
					auth.protectedFetch("/api/library?limit=100", {
						signal: abort.signal,
					}),
					auth.protectedFetch("/api/ops", { signal: abort.signal }),
				]);
				if (abort.signal.aborted) {
					return;
				}
				if (
					isAuthStatus(libraryResponse.status) ||
					isAuthStatus(opsResponse.status)
				) {
					setTags([]);
					setPipeline(null);
					setStatus("auth-required");
					return;
				}
				if (!libraryResponse.ok || !opsResponse.ok) {
					throw new Error("sidebar endpoints unavailable");
				}
				const libraryBody = (await libraryResponse.json()) as LibraryResponse;
				const opsBody = (await opsResponse.json()) as OpsResponse;
				setTags(tagCountsFromLibrary(libraryBody));
				setPipeline(pipelineFromOps(opsBody));
				setStatus("ready");
			} catch (_error) {
				if (!abort.signal.aborted) {
					setTags([]);
					setPipeline(null);
					setStatus("unavailable");
				}
			}
		}

		void loadSidebarData();
		return () => abort.abort();
	}, [auth]);

	return (
		<aside className="sidebar" aria-label="Primary">
			<section className="sidebar-section">
				<h2>Browse</h2>
				<div className="nav-list">
					{navItems.map((item) => {
						const nextRoute: Route = { page: item.page, params: {} };
						return (
							<a
								key={item.page}
								href={routeToHref(nextRoute)}
								className={
									route.page === item.page ? "nav-item active" : "nav-item"
								}
								onClick={(event) =>
									handleRouteAnchorClick(event, nextRoute, navigate)
								}
							>
								{item.label}
							</a>
						);
					})}
				</div>
			</section>
			<section className="sidebar-section">
				<div className="section-heading">
					<h2>Tags</h2>
				</div>
				<SidebarPanel
					status={status}
					onSignIn={auth.signIn}
					signInDisabled={
						auth.clerkConfigured &&
						(!auth.clerkReady || auth.authRedirectInFlight)
					}
					authBlockedMessage={auth.authBlockedMessage}
					unavailableLabel="Tags unavailable — retry shortly."
					authPrompt="Sign in to see your tags."
				>
					{tags.length > 0 ? (
						<div className="tag-list">
							{tags.map((item) => {
								const nextRoute: Route = {
									page: "library",
									params: { tag: item.tag },
								};
								return (
									<a
										key={item.tag}
										href={routeToHref(nextRoute)}
										className={
											route.params.tag === item.tag
												? "tag-pill active"
												: "tag-pill"
										}
										onClick={(event) =>
											handleRouteAnchorClick(event, nextRoute, navigate)
										}
									>
										<span>{item.tag}</span>
										<span className="tnum">{item.count}</span>
									</a>
								);
							})}
						</div>
					) : (
						<p className="empty-note">No tags yet</p>
					)}
				</SidebarPanel>
			</section>
			<section className="sidebar-section pipeline-mini">
				<div className="section-heading">
					<h2>Pipeline</h2>
				</div>
				<SidebarPanel
					status={status}
					onSignIn={auth.signIn}
					signInDisabled={
						auth.clerkConfigured &&
						(!auth.clerkReady || auth.authRedirectInFlight)
					}
					authBlockedMessage={auth.authBlockedMessage}
					unavailableLabel="Pipeline unavailable — retry shortly."
					authPrompt="Sign in to see pipeline status."
				>
					{pipeline ? (
						<dl className="mini-stats">
							<div>
								<dt>Queue</dt>
								<dd>{pipeline.queueDepth}</dd>
							</div>
							<div>
								<dt>Workers</dt>
								<dd>{pipeline.workers}</dd>
							</div>
							<div>
								<dt>Done</dt>
								<dd>{pipeline.done}</dd>
							</div>
							<div>
								<dt>Partial</dt>
								<dd>{pipeline.partial}</dd>
							</div>
						</dl>
					) : (
						<p className="empty-note">Pipeline idle</p>
					)}
				</SidebarPanel>
			</section>
		</aside>
	);
}

function SidebarPanel({
	status,
	onSignIn,
	signInDisabled,
	authBlockedMessage,
	unavailableLabel,
	authPrompt,
	children,
}: {
	status: SidebarStatus;
	onSignIn: () => Promise<void>;
	signInDisabled: boolean;
	authBlockedMessage: string | null;
	unavailableLabel: string;
	authPrompt: string;
	children: React.ReactNode;
}) {
	if (status === "auth-required") {
		return (
			<div className="sidebar-locked" data-state="auth-required">
				<p className="empty-note">{authBlockedMessage ?? authPrompt}</p>
				<button
					type="button"
					className="btn ghost sidebar-signin"
					onClick={() => void onSignIn()}
					disabled={signInDisabled}
				>
					Sign in
				</button>
			</div>
		);
	}
	if (status === "unavailable") {
		return (
			<p className="empty-note" data-state="unavailable">
				{unavailableLabel}
			</p>
		);
	}
	if (status === "loading") {
		return (
			<p className="empty-note" data-state="loading" aria-busy="true">
				Loading…
			</p>
		);
	}
	return <>{children}</>;
}
