import React from "react";

import { IconExternal, IconRefresh } from "../components/ShellIcons";
import { useAuth } from "../hooks/useAuth";
import type { Route } from "../hooks/useRoute";
import { handleRouteAnchorClick, routeToHref } from "../hooks/useRoute";
import type { DisplayCurrency } from "../lib/currency";
import { formatUsdCost } from "../lib/currency";

type Tone = "ok" | "warn" | "err" | "info" | "accent";

type BackupSnapshot = {
	last_success_iso: string | null;
	age_seconds: number | null;
	stale_after: number;
	stale: boolean;
	path: string;
};

type WorkerPoolSnapshot = {
	active: number;
	total: number;
};

type SystemSnapshot = {
	label: string;
	value: string;
	status: Tone;
};

type RecentFailure = {
	id: number;
	video_id: string;
	url: string;
	error: string | null;
	updated_at: string;
};

type OpsSnapshot = {
	window_days: number;
	queue_depth: number;
	worker_pool: WorkerPoolSnapshot;
	transcripts_done: number;
	transcripts_partial: number;
	vast_spend_24h: number;
	vast_spend_7d: number;
	vast_spend_30d: number;
	daily_spend_cap_usd: number;
	spend_series_14d?: number[];
	jobs_by_status?: Record<string, number>;
	backup: BackupSnapshot;
	recent_failures?: RecentFailure[];
	system?: SystemSnapshot[];
};

type OpsProps = {
	displayCurrency: DisplayCurrency;
	navigate: (route: Route) => void;
};

const STATUS_TONES: Record<string, Tone> = {
	done: "ok",
	failed: "err",
	queued: "info",
};

function compactNumber(value: number): string {
	return new Intl.NumberFormat("en-US").format(value);
}

function formatRelativeTime(value: string | null): string {
	if (value === null) {
		return "never";
	}
	const timestamp = new Date(value).getTime();
	if (!Number.isFinite(timestamp)) {
		return "unknown";
	}
	const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
	if (seconds < 90) {
		return "a few seconds ago";
	}
	if (seconds < 7200) {
		return `${Math.round(seconds / 60)}m ago`;
	}
	if (seconds < 172800) {
		return `${Math.round(seconds / 3600)}h ago`;
	}
	return `${Math.round(seconds / 86400)}d ago`;
}

function formatAgeHours(seconds: number | null): string {
	if (seconds === null) {
		return "never";
	}
	return `${Math.max(0, Math.round(seconds / 3600))}h`;
}

function formatFailureTime(value: string): string {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}

function MetricPanel({
	label,
	value,
	children,
	tone,
}: React.PropsWithChildren<{
	label: string;
	value: React.ReactNode;
	tone?: Tone;
}>) {
	return (
		<section className={`metric${tone ? ` metric-${tone}` : ""}`}>
			<div className="label">{label}</div>
			<div className="value tnum">{value}</div>
			<div className="delta">{children}</div>
		</section>
	);
}

function Sparkline({ series, cap }: { series: number[]; cap: number }) {
	const values = series.slice(-14);
	while (values.length < 14) {
		values.unshift(0);
	}
	const width = 100;
	const height = 100;
	const capEnabled = cap > 0;
	const max = Math.max(...values, capEnabled ? cap : 0, 1);
	const points = values.map((value, index) => {
		const x = (index / Math.max(values.length - 1, 1)) * width;
		const y = height - (value / max) * height * 0.85 - 6;
		return [x, y] as const;
	});
	const linePath = `M ${points
		.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`)
		.join(" L ")}`;
	const areaPath = `${linePath} L ${width},${height} L 0,${height} Z`;
	const capY = height - (cap / max) * height * 0.85 - 6;

	return (
		<svg
			className="spark ops-spark"
			viewBox={`0 0 ${width} ${height}`}
			preserveAspectRatio="none"
			role="img"
			aria-label="14 day Vast.ai spend"
		>
			{capEnabled ? (
				<line className="cap-line" x1="0" x2={width} y1={capY} y2={capY} />
			) : null}
			<path d={areaPath} className="area" opacity="0.4" />
			<path d={linePath} className="line" />
			{points.map(([cx, cy], index) => (
				<circle
					key={`${index}-${values[index]}`}
					cx={cx}
					cy={cy}
					r={index === points.length - 1 ? 2 : 0.8}
					className="dot"
				/>
			))}
		</svg>
	);
}

function StatusBars({ stats }: { stats: Record<string, number> }) {
	const entries = Object.entries(stats).sort(
		(a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
	);
	const max = Math.max(...entries.map(([, count]) => count), 1);

	if (entries.length === 0) {
		return <p className="muted">No jobs in the last 24h.</p>;
	}

	return (
		<div className="status-bars ops-status-bars">
			{entries.map(([status, count]) => {
				const tone = STATUS_TONES[status] ?? "accent";
				return (
					<div className="ops-status-row" key={status}>
						<div className="mono muted">{status}</div>
						<div className="bar-track">
							<span
								className={`status-fill ${tone}`}
								style={{ width: `${Math.max(4, (count / max) * 100)}%` }}
							/>
						</div>
						<div className="mono tnum ops-status-count">
							{compactNumber(count)}
						</div>
					</div>
				);
			})}
		</div>
	);
}

function countFailuresSince(
	failures: RecentFailure[],
	seconds: number,
): number {
	const cutoff = Date.now() - seconds * 1000;
	return failures.filter((failure) => {
		const timestamp = new Date(failure.updated_at).getTime();
		return Number.isFinite(timestamp) && timestamp >= cutoff;
	}).length;
}

function SystemRow({ item }: { item: SystemSnapshot }) {
	const tone =
		item.status === "ok" ? "ok" : item.status === "warn" ? "warn" : "err";
	const glyph = item.status === "ok" ? "●" : item.status === "warn" ? "◐" : "○";
	return (
		<div className="ops-system-row">
			<span className={`status-glyph ${tone}`} aria-hidden="true">
				{glyph}
			</span>
			<span className={`ops-system-status ${tone}`}>{item.status}</span>
			<span className="muted">{item.label}</span>
			<div className="spacer" />
			<span className="ops-system-value">{item.value}</span>
		</div>
	);
}

function FailureRow({
	failure,
	navigate,
}: {
	failure: RecentFailure;
	navigate: (route: Route) => void;
}) {
	const route: Route = { page: "job", params: { id: failure.id } };
	return (
		<div className="failure-row">
			<a
				className="failure-action"
				href={routeToHref(route)}
				onClick={(event) => handleRouteAnchorClick(event, route, navigate)}
			>
				<p className="err-title">Job #{failure.id}</p>
				<p className="err-msg">
					{failure.error ?? "Job failed without an error message."}
				</p>
				<p className="err-meta mono">
					{failure.video_id} - {formatFailureTime(failure.updated_at)}
				</p>
			</a>
		</div>
	);
}

export function Ops({ displayCurrency, navigate }: OpsProps) {
	const auth = useAuth();
	const [snapshot, setSnapshot] = React.useState<OpsSnapshot | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [loading, setLoading] = React.useState(true);
	const [lastRefreshAt, setLastRefreshAt] = React.useState<string | null>(null);

	const load = React.useCallback(
		async (signal?: AbortSignal) => {
			setLoading(true);
			setError(null);
			try {
				const response = await auth.protectedFetch("/api/ops", { signal });
				if (!response.ok) {
					throw new Error(`ops endpoint returned ${response.status}`);
				}
				setSnapshot((await response.json()) as OpsSnapshot);
				setLastRefreshAt(new Date().toISOString());
			} catch (caught) {
				if (!signal?.aborted) {
					setError(
						caught instanceof Error ? caught.message : "failed to load ops",
					);
				}
			} finally {
				if (!signal?.aborted) {
					setLoading(false);
				}
			}
		},
		[auth],
	);

	React.useEffect(() => {
		const abort = new AbortController();
		void load(abort.signal);
		return () => abort.abort();
	}, [load]);

	const cap = snapshot?.daily_spend_cap_usd ?? 0;
	const capEnabled = cap > 0;
	const spendPct =
		snapshot !== null && capEnabled
			? Math.min(1, snapshot.vast_spend_24h / cap)
			: 0;
	const spendCls = spendPct > 0.85 ? "err" : spendPct > 0.6 ? "warn" : "";
	const failures = snapshot?.recent_failures ?? [];
	const failures24h = countFailuresSince(failures, 24 * 3600);
	const failureSummary =
		failures.length >= 50
			? `showing ${compactNumber(failures.length)} most recent (capped) · last 24h: ${compactNumber(failures24h)}`
			: `${compactNumber(failures.length)} failed · last 24h: ${compactNumber(failures24h)}`;
	const lastRefreshLabel = loading
		? "refreshing"
		: formatRelativeTime(lastRefreshAt);
	const spendSeries = snapshot?.spend_series_14d ?? [];
	const jobsByStatus = snapshot?.jobs_by_status ?? {};
	const systemRows = snapshot?.system ?? [];

	return (
		<section className="pane ops-page">
			<header className="pane-header">
				<div>
					<h1 className="pane-h1">Ops</h1>
					<div className="pane-sub">
						Window: rolling {snapshot?.window_days ?? 1}d · last refresh{" "}
						<span className="tnum">{lastRefreshLabel}</span>
					</div>
				</div>
				<div className="pane-actions">
					<button
						type="button"
						className="btn"
						onClick={() => void load()}
						disabled={loading}
					>
						{loading ? <span className="spinner" aria-hidden="true" /> : null}
						{loading ? null : <IconRefresh size={14} />}
						Refresh
					</button>
					<button
						type="button"
						className="btn"
						disabled
						title="No Grafana dashboard URL is configured for this runtime."
					>
						<IconExternal size={14} />
						Grafana unavailable
					</button>
				</div>
			</header>

			{error !== null ? (
				<div className="error-banner">
					<p className="err-title">Ops snapshot unavailable</p>
					<p className="err-msg">{error}</p>
				</div>
			) : null}

			{snapshot === null && loading ? (
				<div className="empty-queue">
					<div>
						<span className="spinner" aria-hidden="true" />
						<strong>Loading ops snapshot</strong>
						<span>Fetching the current one-shot dashboard state.</span>
					</div>
				</div>
			) : null}

			{snapshot !== null ? (
				<>
					<div className="metric-grid">
						<MetricPanel label="Queue depth" value={snapshot.queue_depth}>
							workers{" "}
							<span className="tnum">
								{snapshot.worker_pool.active}/{snapshot.worker_pool.total}
							</span>{" "}
							busy
						</MetricPanel>
						<MetricPanel
							label="Transcripts · 24h"
							value={compactNumber(snapshot.transcripts_done)}
						>
							{snapshot.transcripts_partial > 0 ? (
								<>
									<span className="warn-text">
										{compactNumber(snapshot.transcripts_partial)} partial
									</span>{" "}
									· awaiting resummarize
								</>
							) : (
								"all summaries fresh"
							)}
						</MetricPanel>
						<MetricPanel
							label="Vast spend · 24h"
							value={formatUsdCost(snapshot.vast_spend_24h, displayCurrency)}
						>
							{capEnabled ? (
								<>
									of{" "}
									<span className="tnum">
										{formatUsdCost(cap, displayCurrency)}
									</span>{" "}
									cap · {(spendPct * 100).toFixed(0)}% used
									<div className="bar-track">
										<span
											className={spendCls}
											style={{ width: `${spendPct * 100}%` }}
										/>
									</div>
								</>
							) : (
								"daily cap disabled"
							)}
						</MetricPanel>
						<MetricPanel
							label="Backup heartbeat"
							value={
								<>
									<span
										className={`status-glyph ${
											snapshot.backup.stale ? "err" : "ok"
										}`}
									>
										{snapshot.backup.stale ? "✗" : "✓"}
									</span>{" "}
									{formatAgeHours(snapshot.backup.age_seconds)}
								</>
							}
						>
							last success{" "}
							{formatRelativeTime(snapshot.backup.last_success_iso)} · stale
							after {Math.round(snapshot.backup.stale_after / 3600)}h
						</MetricPanel>
					</div>

					<div className="ops-grid">
						<section className="metric ops-panel">
							<div className="row ops-panel-head">
								<div className="label">Vast.ai spend · last 14 days</div>
								<div className="spacer" />
								<div className="mono muted ops-spend-summary">
									7d{" "}
									<span className="tnum">
										{formatUsdCost(snapshot.vast_spend_7d, displayCurrency)}
									</span>{" "}
									30d{" "}
									<span className="tnum">
										{formatUsdCost(snapshot.vast_spend_30d, displayCurrency)}
									</span>
								</div>
							</div>
							{spendSeries.length > 0 ? (
								<Sparkline series={spendSeries} cap={cap} />
							) : (
								<div className="ops-unavailable muted">
									14-day spend series unavailable.
								</div>
							)}
							{capEnabled ? null : (
								<p className="muted ops-cap-note">Daily spend cap disabled.</p>
							)}
						</section>

						<section className="metric ops-panel">
							<div className="label ops-panel-label">Jobs by status · 24h</div>
							<StatusBars stats={jobsByStatus} />
						</section>
					</div>

					<div className="section-label split ops-section-label">
						<span>Recent failures · 7d</span>
						<span className="mono muted">{failureSummary}</span>
					</div>
					{failures.length === 0 ? (
						<div className="empty-queue">
							<div>
								<strong>No recent failures</strong>
								<span>
									Failed jobs will appear here after the next snapshot.
								</span>
							</div>
						</div>
					) : (
						<div className="failure-list">
							{failures.map((failure) => (
								<FailureRow
									key={failure.id}
									failure={failure}
									navigate={navigate}
								/>
							))}
						</div>
					)}

					<div className="section-label ops-section-label">
						<span>System</span>
					</div>
					<div className="ops-system-grid">
						{systemRows.length === 0 ? (
							<div className="ops-system-row">
								<span className="ops-system-status warn">unknown</span>
								<span className="muted">System rollcall unavailable.</span>
							</div>
						) : (
							systemRows.map((item) => (
								<SystemRow item={item} key={item.label} />
							))
						)}
					</div>
				</>
			) : null}
		</section>
	);
}
