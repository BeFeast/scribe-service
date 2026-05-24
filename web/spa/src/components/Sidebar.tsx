import React from "react";

import { useAuth } from "../hooks/useAuth";
import {
	type Route,
	type RoutePage,
	handleRouteAnchorClick,
	routeToHref,
} from "../hooks/useRoute";
import { isAuthStatus } from "../lib/auth";
import { formatUsdCost } from "../lib/currency";
import { IconLibrary, IconOps, IconQueue, IconSettings } from "./ShellIcons";

type SidebarProps = {
	route: Route;
	navigate: (route: Route) => void;
};

type LibraryResponse = {
	total?: number;
	rows?: Array<{ tags?: string[] | null }>;
};

type ActiveJobsResponse = {
	jobs?: Array<{ id: number; status: string }>;
};

type OpsResponse = {
	vast_spend_24h?: number;
	daily_spend_cap_usd?: number;
	recent_failures?: Array<{ updated_at?: string }>;
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
	workers: string;
	vastSpend24h: number;
	dailyCap: number;
};

type SidebarData = {
	libraryTotal: number;
	tags: TagCount[];
	queueCount: number;
	failuresToday: number;
	pipeline: PipelineStats;
};

type SidebarStatus = "loading" | "ready" | "auth-required" | "unavailable";

const navItems: Array<{
	page: RoutePage;
	label: string;
	icon: (props: { size?: number }) => React.ReactNode;
}> = [
	{ page: "library", label: "Library", icon: IconLibrary },
	{ page: "queue", label: "Queue", icon: IconQueue },
	{ page: "ops", label: "Ops", icon: IconOps },
	{ page: "settings", label: "Settings", icon: IconSettings },
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

function isToday(value: string | undefined): boolean {
	if (value === undefined) {
		return false;
	}
	const date = new Date(value);
	if (Number.isNaN(date.getTime())) {
		return false;
	}
	const now = new Date();
	return date.toDateString() === now.toDateString();
}

function sidebarDataFromResponses(
	library: LibraryResponse,
	activeJobs: ActiveJobsResponse,
	ops: OpsResponse,
): SidebarData {
	const active = ops.worker_pool?.active ?? 0;
	const total = ops.worker_pool?.total ?? 0;
	return {
		libraryTotal: library.total ?? library.rows?.length ?? 0,
		tags: tagCountsFromLibrary(library),
		queueCount: activeJobs.jobs?.length ?? 0,
		failuresToday:
			ops.recent_failures?.filter((row) => isToday(row.updated_at)).length ?? 0,
		pipeline: {
			workers: `${active}/${total}`,
			vastSpend24h: ops.vast_spend_24h ?? 0,
			dailyCap: ops.daily_spend_cap_usd ?? 0,
		},
	};
}

export function Sidebar({ route, navigate }: SidebarProps) {
	const auth = useAuth();
	const [data, setData] = React.useState<SidebarData | null>(null);
	const [status, setStatus] = React.useState<SidebarStatus>("loading");

	React.useEffect(() => {
		const abort = new AbortController();

		async function loadSidebarData() {
			setStatus("loading");
			try {
				const [libraryResponse, activeJobsResponse, opsResponse] =
					await Promise.all([
						auth.protectedFetch("/api/library?limit=100", {
							signal: abort.signal,
						}),
						auth.protectedFetch("/api/jobs/active", { signal: abort.signal }),
						auth.protectedFetch("/api/ops", { signal: abort.signal }),
					]);
				if (abort.signal.aborted) {
					return;
				}
				if (
					isAuthStatus(libraryResponse.status) ||
					isAuthStatus(activeJobsResponse.status) ||
					isAuthStatus(opsResponse.status)
				) {
					setData(null);
					setStatus("auth-required");
					return;
				}
				if (!libraryResponse.ok || !activeJobsResponse.ok || !opsResponse.ok) {
					throw new Error("sidebar endpoints unavailable");
				}
				const [libraryBody, activeJobsBody, opsBody] = (await Promise.all([
					libraryResponse.json(),
					activeJobsResponse.json(),
					opsResponse.json(),
				])) as [LibraryResponse, ActiveJobsResponse, OpsResponse];
				setData(sidebarDataFromResponses(libraryBody, activeJobsBody, opsBody));
				setStatus("ready");
			} catch (_error) {
				if (!abort.signal.aborted) {
					setData(null);
					setStatus("unavailable");
				}
			}
		}

		void loadSidebarData();
		return () => abort.abort();
	}, [auth]);

	return (
		<aside className="sidebar" aria-label="Primary">
			<div className="nav-section">Browse</div>
			{navItems.map((item) => {
				const nextRoute: Route = { page: item.page, params: {} };
				const Icon = item.icon;
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
						<Icon size={15} />
						<span>{item.label}</span>
						<NavCount page={item.page} data={data} />
					</a>
				);
			})}

			<div className="nav-section">Tags</div>
			<SidebarPanel
				status={status}
				onSignIn={auth.signIn}
				signInDisabled={
					auth.clerkConfigured &&
					(!auth.clerkReady || auth.authRedirectInFlight)
				}
				authBlockedMessage={auth.authBlockedMessage}
				unavailableLabel="Tags unavailable - retry shortly."
				authPrompt="Sign in to see your tags."
			>
				{data?.tags.length ? (
					<div className="tag-list">
						{data.tags.map((item) => {
							const nextRoute: Route = {
								page: "library",
								params: { tag: item.tag },
							};
							return (
								<a
									key={item.tag}
									href={routeToHref(nextRoute)}
									className={
										route.params.tag === item.tag ? "tag active" : "tag"
									}
									title={`${item.count} transcripts`}
									onClick={(event) =>
										handleRouteAnchorClick(event, nextRoute, navigate)
									}
								>
									{item.tag}
									<span className="n">{item.count}</span>
								</a>
							);
						})}
					</div>
				) : (
					<p className="empty-state sidebar-state">No tags yet</p>
				)}
			</SidebarPanel>

			<div className="nav-section">Pipeline</div>
			<SidebarPanel
				status={status}
				onSignIn={auth.signIn}
				signInDisabled={
					auth.clerkConfigured &&
					(!auth.clerkReady || auth.authRedirectInFlight)
				}
				authBlockedMessage={auth.authBlockedMessage}
				unavailableLabel="Pipeline unavailable - retry shortly."
				authPrompt="Sign in to see pipeline status."
			>
				<div className="pipeline-summary">
					<div className="row">
						<span>workers</span>
						<span className="tnum">{data?.pipeline.workers ?? "0/0"}</span>
					</div>
					<div className="row">
						<span>vast 24h</span>
						<span className="tnum">
							{formatUsdCost(data?.pipeline.vastSpend24h ?? 0, "USD")}
						</span>
					</div>
					<div className="row">
						<span>cap</span>
						<span className="tnum">
							{formatUsdCost(data?.pipeline.dailyCap ?? 0, "USD")}
						</span>
					</div>
				</div>
			</SidebarPanel>
		</aside>
	);
}

function NavCount({
	page,
	data,
}: {
	page: RoutePage;
	data: SidebarData | null;
}) {
	if (page === "library") {
		return <span className="count">{data?.libraryTotal ?? 0}</span>;
	}
	if (page === "queue" && (data?.queueCount ?? 0) > 0) {
		return (
			<span className="count count-live">
				<span className="live-dot" />
				{data?.queueCount}
			</span>
		);
	}
	if (page === "ops" && (data?.failuresToday ?? 0) > 0) {
		return <span className="count count-err">{data?.failuresToday}!</span>;
	}
	return <span className="count" aria-hidden="true" />;
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
			<div className="empty-state sidebar-state" data-state="auth-required">
				<p>{authBlockedMessage ?? authPrompt}</p>
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
			<p className="error-state sidebar-state" data-state="unavailable">
				{unavailableLabel}
			</p>
		);
	}
	if (status === "loading") {
		return (
			<p className="loading-state sidebar-state" data-state="loading">
				<span className="spinner" />
				Loading...
			</p>
		);
	}
	return <>{children}</>;
}
