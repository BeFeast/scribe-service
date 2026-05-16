import React from "react";

import type { Route } from "../hooks/useRoute";

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
	jobs_by_status: Record<string, number>;
	transcripts_done: number;
	transcripts_partial: number;
	queue_depth: number;
	vast_spend_24h: number;
	vast_spend_7d: number;
	vast_spend_30d: number;
	daily_spend_cap_usd: number;
	spend_series_14d: number[];
	backup: BackupSnapshot;
	worker_pool: WorkerPoolSnapshot;
	recent_failures: RecentFailure[];
	system: SystemSnapshot[];
};

type OpsProps = {
	navigate: (route: Route) => void;
};

const FAILURE_PAGE_SIZE = 10;
const STATUS_TONES: Record<string, Tone> = {
	done: "ok",
	failed: "err",
	queued: "info",
};

function money(value: number): string {
	return new Intl.NumberFormat("en-US", {
		style: "currency",
		currency: "USD",
		maximumFractionDigits: 2,
	}).format(value);
}

function compactNumber(value: number): string {
	return new Intl.NumberFormat("en-US").format(value);
}

function relativeAge(seconds: number | null): string {
	if (seconds === null) {
		return "never";
	}
	if (seconds < 90) {
		return `${Math.max(0, Math.round(seconds))}s ago`;
	}
	if (seconds < 7200) {
		return `${Math.round(seconds / 60)}m ago`;
	}
	if (seconds < 172800) {
		return `${Math.round(seconds / 3600)}h ago`;
	}
	return `${Math.round(seconds / 86400)}d ago`;
}

function formatTime(value: string): string {
	return new Intl.DateTimeFormat("en-US", {
		month: "short",
		day: "numeric",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}

function MetricCard({
	label,
	value,
	help,
	tone = "accent",
}: {
	label: string;
	value: string;
	help: string;
	tone?: Tone;
}) {
	return (
		<section className={`metric metric-${tone}`}>
			<p className="section-label">{label}</p>
			<strong>{value}</strong>
			<span className="metric-help">{help}</span>
		</section>
	);
}

function Sparkline({ values, cap }: { values: number[]; cap: number }) {
	const series = values.slice(-14);
	while (series.length < 14) {
		series.unshift(0);
	}
	const width = 260;
	const height = 80;
	const pad = 8;
	const max = Math.max(cap, ...series, 1);
	const x = (index: number) => pad + (index * (width - pad * 2)) / 13;
	const y = (value: number) =>
		height - pad - (value / max) * (height - pad * 2);
	const points = series.map((value, index) => [x(index), y(value)] as const);
	const line = points.map(([px, py]) => `${px},${py}`).join(" ");
	const area = `${pad},${height - pad} ${line} ${width - pad},${height - pad}`;
	const capY = y(cap);

	return (
		<svg
			className="spark ops-spark"
			viewBox={`0 0 ${width} ${height}`}
			role="img"
			aria-label="14 day Vast spend sparkline"
		>
			<line
				className="cap-line"
				x1={pad}
				y1={capY}
				x2={width - pad}
				y2={capY}
			/>
			<polygon className="area" points={area} />
			<polyline className="line" points={line} />
			{points.map(([cx, cy], index) => (
				<circle
					key={`${index}-${series[index]}`}
					className="dot"
					cx={cx}
					cy={cy}
					r="2.4"
				/>
			))}
		</svg>
	);
}

function StatusBars({ statuses }: { statuses: Record<string, number> }) {
	const rows = Object.entries(statuses).sort(
		(a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
	);
	const max = Math.max(...rows.map(([, count]) => count), 1);

	if (rows.length === 0) {
		return <p className="muted">No jobs in the last 24 h.</p>;
	}

	return (
		<div className="status-bars">
			{rows.map(([status, count]) => {
				const tone = STATUS_TONES[status] ?? "accent";
				return (
					<div className="status-bar" key={status}>
						<div className="status-bar-head">
							<span>{status}</span>
							<strong className="tnum">{compactNumber(count)}</strong>
						</div>
						<div className="bar-track">
							<span
								className={`status-fill ${tone}`}
								style={{ width: `${Math.max(5, (count / max) * 100)}%` }}
							/>
						</div>
					</div>
				);
			})}
		</div>
	);
}

function SystemRow({ item }: { item: SystemSnapshot }) {
	const tone =
		item.status === "err" ? "err" : item.status === "warn" ? "warn" : "ok";
	return (
		<div className="system-row">
			<div>
				<strong>{item.label}</strong>
				<span>{item.value}</span>
			</div>
			<span className={`chip ${tone}`}>{item.status}</span>
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
	return (
		<button
			type="button"
			className="failure-row failure-action"
			onClick={() => navigate({ page: "job", params: { id: failure.id } })}
		>
			<p className="err-title">Job #{failure.id}</p>
			<p className="err-msg">
				{failure.error ?? "Job failed without an error message."}
			</p>
			<p className="err-meta mono">
				{failure.video_id} - {formatTime(failure.updated_at)}
			</p>
		</button>
	);
}

export function Ops({ navigate }: OpsProps) {
	const [snapshot, setSnapshot] = React.useState<OpsSnapshot | null>(null);
	const [error, setError] = React.useState<string | null>(null);
	const [loading, setLoading] = React.useState(true);
	const [visibleFailures, setVisibleFailures] =
		React.useState(FAILURE_PAGE_SIZE);

	const load = React.useCallback(async (signal?: AbortSignal) => {
		setLoading(true);
		setError(null);
		try {
			const response = await fetch("/api/ops", { signal });
			if (!response.ok) {
				throw new Error(`ops endpoint returned ${response.status}`);
			}
			const body = (await response.json()) as OpsSnapshot;
			setSnapshot(body);
			setVisibleFailures(FAILURE_PAGE_SIZE);
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
	}, []);

	React.useEffect(() => {
		const abort = new AbortController();
		void load(abort.signal);
		return () => abort.abort();
	}, [load]);

	const failures = snapshot?.recent_failures ?? [];
	const visible = failures.slice(0, visibleFailures);
	const backupTone: Tone = snapshot?.backup.stale ? "err" : "ok";

	return (
		<section className="pane ops-page">
			<header className="pane-header">
				<div>
					<p className="eyebrow">Ops</p>
					<h1 className="pane-h1">Runtime dashboard</h1>
					<p className="pane-sub">One-shot snapshot from /api/ops.</p>
				</div>
				<button
					type="button"
					className="btn"
					onClick={() => void load()}
					disabled={loading}
				>
					{loading ? "Refreshing" : "Refresh"}
				</button>
			</header>

			{error !== null ? (
				<div className="failure-row">
					<p className="err-title">Ops snapshot unavailable</p>
					<p className="err-msg">{error}</p>
				</div>
			) : null}

			{snapshot === null && loading ? (
				<div className="card">
					<span className="spinner" />
					<p className="muted">Loading ops snapshot.</p>
				</div>
			) : null}

			{snapshot !== null ? (
				<>
					<div className="metric-grid">
						<MetricCard
							label="Queue depth"
							value={compactNumber(snapshot.queue_depth)}
							help={`${snapshot.worker_pool.active}/${snapshot.worker_pool.total} workers active`}
							tone="info"
						/>
						<MetricCard
							label="Transcripts 24 h"
							value={compactNumber(snapshot.transcripts_done)}
							help={`${compactNumber(snapshot.transcripts_partial)} partial`}
							tone="ok"
						/>
						<MetricCard
							label="Vast spend 24 h"
							value={money(snapshot.vast_spend_24h)}
							help={`${money(snapshot.vast_spend_7d)} over 7 d`}
						/>
						<MetricCard
							label="Backup heartbeat"
							value={relativeAge(snapshot.backup.age_seconds)}
							help={snapshot.backup.stale ? "stale" : "fresh"}
							tone={backupTone}
						/>
					</div>

					<div className="ops-grid">
						<section className="card">
							<div className="card-head">
								<div>
									<p className="section-label">14-day spend</p>
									<strong>{money(snapshot.vast_spend_30d)} / 30 d</strong>
								</div>
								<span className="chip info">
									cap {money(snapshot.daily_spend_cap_usd)}
								</span>
							</div>
							<Sparkline
								values={snapshot.spend_series_14d}
								cap={snapshot.daily_spend_cap_usd}
							/>
						</section>
						<section className="card">
							<p className="section-label">Jobs by status</p>
							<StatusBars statuses={snapshot.jobs_by_status} />
						</section>
					</div>

					<section className="ops-section">
						<div className="section-line">
							<p className="section-label">Recent failures</p>
							<span className="muted">
								{failures.length >= 50
									? "50+ total"
									: `${compactNumber(failures.length)} total`}
							</span>
						</div>
						{visible.length === 0 ? (
							<div className="card">
								<p className="muted">No recent failures.</p>
							</div>
						) : (
							<div className="failure-list">
								{visible.map((failure) => (
									<FailureRow
										key={failure.id}
										failure={failure}
										navigate={navigate}
									/>
								))}
							</div>
						)}
						{visibleFailures < failures.length ? (
							<button
								type="button"
								className="btn ghost"
								onClick={() =>
									setVisibleFailures((count) => count + FAILURE_PAGE_SIZE)
								}
							>
								Show more
							</button>
						) : null}
					</section>

					<section className="ops-section">
						<p className="section-label">System rollcall</p>
						<div className="system-list">
							{snapshot.system.map((item) => (
								<SystemRow item={item} key={item.label} />
							))}
						</div>
					</section>
				</>
			) : null}
		</section>
	);
}
