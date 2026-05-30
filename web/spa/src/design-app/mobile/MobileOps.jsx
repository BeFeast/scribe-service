// Mobile Ops dashboard — Wave 2d / Issue #279
//
// Literal port of `Scribe iOS.html` viewOps() (~lines 1093-1140) and the
// associated sparkPath() helper (~lines 1083-1092). The desktop OpsPage in
// design-app/ops.jsx is the canonical Claude Design source for the
// non-mobile route and stays untouched (biome-ignored, byte-locked
// against the staged design source). This file is the mobile adapter:
// same real data (STATS, SPEND_SERIES, RECENT_FAILURES, fmtUsd,
// countFailuresInLastDay from ./data.js), iOS-shaped DOM, mobile-only
// recipe classes that live inside @media (max-width: 768px) in
// styles.css.
//
// Source mapping (Scribe iOS.html → this file):
//   ~1083 sparkPath(series)    → sparkPath(series) (pure math; no DOM)
//   ~1093 viewOps()            → <MobileOps />
//   ~1097 .metric-grid         → .metric-grid (mobile)
//   ~1103 .panel "Vast.ai"     → first .panel (sparkline + KV rows)
//   ~1112 .panel "System"      → second .panel (health chip + KV rows)
//   ~1120 .panel "Recent fail" → third .panel (.fail-row list)
//
// Real-data rules (HARD, AGENTS.md):
//   - No fabricated host names / hardcoded telemetry.
//   - Failure list maps real RECENT_FAILURES (not the prototype's fake
//     single failure microcopy "bot wall · #214").
//   - Spark draws real SPEND_SERIES; if empty, the spark is omitted.
//   - The "+3 vs prev day" prototype microcopy is replaced with real
//     numbers where computable, or omitted (Wave 2d does not yet
//     compute prev-day delta from runtime state; that lands with the
//     #279 follow-up data wiring).

import React from "react";
import {
	RECENT_FAILURES,
	SPEND_SERIES,
	STATS,
	countFailuresInLastDay,
	fmtUsd,
} from "../data.js";

/* ── sparkPath (verbatim port of viewOps' helper) ───────────────────── */

export function sparkPath(series) {
	const w = 300;
	const h = 56;
	const pad = 3;
	if (!Array.isArray(series) || series.length < 2) {
		return null;
	}
	const max = Math.max(...series);
	const min = Math.min(...series);
	const xs = (i) => pad + (i * (w - pad * 2)) / (series.length - 1);
	const ys = (v) => h - pad - ((v - min) / (max - min || 1)) * (h - pad * 2);
	let d = `M${xs(0)} ${ys(series[0])}`;
	series.forEach((v, i) => {
		if (i) d += ` L${xs(i).toFixed(1)} ${ys(v).toFixed(1)}`;
	});
	const last = series.length - 1;
	const area = `${d} L${xs(last).toFixed(1)} ${h} L${xs(0)} ${h} Z`;
	const lx = xs(last).toFixed(1);
	const ly = ys(series[last]).toFixed(1);
	return { line: d, area, lx, ly };
}

/* ── helpers ────────────────────────────────────────────────────────── */

function systemHealthClass(stats) {
	// STATS.system is an array of system entries (see FALLBACK_STATS).
	// If any entry is unhealthy, surface .err; otherwise .ok.
	const list = Array.isArray(stats?.system) ? stats.system : [];
	if (list.length === 0) return "ok"; // neutral healthy default
	for (const entry of list) {
		const status = entry?.status ?? entry?.state ?? null;
		if (
			status === "err" ||
			status === "error" ||
			status === "down" ||
			status === "stale"
		) {
			return "err";
		}
		if (status === "warn" || status === "warning") return "warn";
	}
	return "ok";
}

function systemHealthLabel(cls) {
	if (cls === "err") return "degraded";
	if (cls === "warn") return "warning";
	return "healthy";
}

/* ── MobileOps page ─────────────────────────────────────────────────── */

export function MobileOps(_props) {
	const stats = STATS;
	const cap = stats.daily_spend_cap_usd ?? 0;
	const spent = stats.vast_spend_24h ?? 0;
	const capPct = cap > 0 ? Math.min(100, Math.round((spent / cap) * 100)) : 0;
	const sp = sparkPath(SPEND_SERIES);

	const pool = stats.worker_pool ?? { active: 0, total: 0 };
	const backup = stats.backup ?? {};
	const backupAgeH =
		typeof backup.age_seconds === "number"
			? Math.round(backup.age_seconds / 3600)
			: null;
	const backupStale = Boolean(backup.stale);

	const failures24h = countFailuresInLastDay(RECENT_FAILURES);
	const totalFailures = RECENT_FAILURES.length;

	const healthCls = systemHealthClass(stats);
	const healthLabel = systemHealthLabel(healthCls);

	return (
		<div className="m-ops">
			<div className="metric-grid">
				<div className="metric">
					<div className="m-label">Done · 24h</div>
					<div className="m-value">{stats.transcripts_done ?? 0}</div>
					<div className="m-delta">
						{stats.transcripts_partial > 0
							? `${stats.transcripts_partial} partial`
							: "all summaries fresh"}
					</div>
				</div>
				<div className="metric">
					<div className="m-label">Queue depth</div>
					<div className="m-value">{stats.queue_depth ?? 0}</div>
					<div className="m-delta">
						{pool.active}/{pool.total} workers busy
					</div>
				</div>
				<div className="metric">
					<div className="m-label">Spend · 24h</div>
					<div className="m-value">{fmtUsd(spent)}</div>
					<div className="m-delta">
						cap {fmtUsd(cap)} · {capPct}%
					</div>
				</div>
				<div className="metric">
					<div className="m-label">Failures · 24h</div>
					<div
						className="m-value"
						style={{
							color: failures24h > 0 ? "var(--err)" : undefined,
						}}
					>
						{failures24h}
					</div>
					<div className={failures24h > 0 ? "m-delta bad" : "m-delta"}>
						{totalFailures > 0
							? `${totalFailures} in last 7d`
							: "no recent failures"}
					</div>
				</div>
			</div>

			<div className="panel">
				<div className="p-h">
					<span className="p-title">Vast.ai spend</span>
					<span className="p-meta">
						{SPEND_SERIES.length > 0
							? `${SPEND_SERIES.length} day${
									SPEND_SERIES.length === 1 ? "" : "s"
								}`
							: "no data"}
					</span>
				</div>
				{sp ? (
					<svg
						className="spark"
						viewBox="0 0 300 56"
						preserveAspectRatio="none"
						aria-hidden="true"
						focusable="false"
					>
						<path className="area" d={sp.area} />
						<path className="line" d={sp.line} />
						<circle className="dot" cx={sp.lx} cy={sp.ly} r="3" />
					</svg>
				) : null}
				<div className="kv">
					<span className="k">Today</span>
					<span className="v">{fmtUsd(spent)}</span>
				</div>
				<div className="kv">
					<span className="k">7-day</span>
					<span className="v">{fmtUsd(stats.vast_spend_7d ?? 0)}</span>
				</div>
				<div className="kv">
					<span className="k">30-day</span>
					<span className="v">{fmtUsd(stats.vast_spend_30d ?? 0)}</span>
				</div>
				<div className="kv">
					<span className="k">Daily cap</span>
					<div className="bar-track">
						<div style={{ width: `${capPct}%` }} />
					</div>
				</div>
			</div>

			<div className="panel">
				<div className="p-h">
					<span className="p-title">System</span>
					<span className={`m-health ${healthCls}`}>
						<span className="m-health-dot" />
						{healthLabel}
					</span>
				</div>
				<div className="kv">
					<span className="k">Worker pool</span>
					<span className="v">
						{pool.active}/{pool.total} active
					</span>
				</div>
				<div className="kv">
					<span className="k">Last backup</span>
					<span className="v">
						{backupAgeH !== null ? `${backupAgeH}h ago` : "—"}
					</span>
				</div>
				<div className="kv">
					<span className="k">Backup state</span>
					<span
						className="v"
						style={{ color: backupStale ? "var(--err)" : "var(--ok)" }}
					>
						{backupStale ? "stale" : "fresh"}
					</span>
				</div>
			</div>

			<div className="panel">
				<div className="p-h">
					<span className="p-title">Recent failures</span>
					<span
						className="p-meta"
						style={{
							color: totalFailures > 0 ? "var(--err)" : "var(--muted)",
						}}
					>
						{totalFailures}
					</span>
				</div>
				{totalFailures === 0 ? (
					<div className="kv">
						<span className="k">No failures in window</span>
					</div>
				) : (
					RECENT_FAILURES.map((f) => (
						<div key={f.id} className="fail-row">
							<div className="f-title">{f.title}</div>
							<div className="f-msg">{f.error}</div>
							<div className="f-meta">
								#{f.id} · {f.source ?? "unknown"} · {f.failed_at}
							</div>
						</div>
					))
				)}
			</div>

			<div style={{ height: 8 }} />
		</div>
	);
}
