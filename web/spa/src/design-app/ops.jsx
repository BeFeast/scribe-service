// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { IconExternal, IconRefresh } from "./icons.jsx";
import { RECENT_FAILURES, SPEND_SERIES, STATS, fmtRelative, fmtUsd } from "./data.js";
import { FailureRow } from "./job-pages.jsx";
// Ops dashboard — spend, queue, backups, recent failures, sparkline.

export function OpsPage({ navigate, loading, error, onRefresh, onRetryJob, onDeleteJob }) {
  const spendPct = STATS.daily_spend_cap_usd > 0 ? Math.min(1, STATS.vast_spend_24h / STATS.daily_spend_cap_usd) : 0;
  const spendCls = spendPct > 0.85 ? "err" : spendPct > 0.6 ? "warn" : "";
  const system = Array.isArray(STATS.system) ? STATS.system : [];

  return (
    <div className="pane">
      <div className="pane-header">
        <div>
          <h1 className="pane-h1">Ops</h1>
          <div className="pane-sub">
            Window: rolling 24h · last refresh <span className="tnum">a few seconds ago</span>
          </div>
        </div>
        <div className="pane-actions">
          <button className="btn" onClick={onRefresh}><IconRefresh size={14}/> Refresh</button>
          <button className="btn"><IconExternal size={14}/> Grafana</button>
        </div>
      </div>

      {loading && <div className="empty"><div className="empty-title">Loading ops</div><div>Fetching /api/ops.</div></div>}
      {error && <div className="empty"><div className="empty-title">Ops snapshot unavailable</div><div>{error}</div></div>}

      <div className="metric-grid">
        <div className="metric">
          <div className="label">Queue depth</div>
          <div className="value tnum">{STATS.queue_depth}</div>
          <div className="delta">
            workers <span className="tnum">{STATS.worker_pool.active}/{STATS.worker_pool.total}</span> busy
          </div>
        </div>
        <div className="metric">
          <div className="label">Transcripts · 24h</div>
          <div className="value tnum">{STATS.transcripts_done}</div>
          <div className="delta">
            {STATS.transcripts_partial > 0
              ? <><span style={{color: "var(--warn)"}}>{STATS.transcripts_partial} partial</span> · awaiting resummarize</>
              : <>all summaries fresh</>}
          </div>
        </div>
        <div className="metric">
          <div className="label">Vast spend · 24h</div>
          <div className="value tnum">{fmtUsd(STATS.vast_spend_24h)}</div>
          <div className="delta">
            of <span className="tnum">{fmtUsd(STATS.daily_spend_cap_usd)}</span> cap · {(spendPct * 100).toFixed(0)}% used
          </div>
          <div className="bar-track">
            <div className={spendCls} style={{width: `${spendPct * 100}%`}}/>
          </div>
        </div>
        <div className="metric">
          <div className="label">Backup heartbeat</div>
          <div className="value" style={{fontSize: 22}}>
            <span className="status-glyph" style={{color: STATS.backup.stale ? "var(--err)" : "var(--ok)", marginRight: 6}}>
              {STATS.backup.stale ? "✗" : "✓"}
            </span>
            <span className="tnum" style={{fontFamily: "var(--font-mono)"}}>
              {Math.round(STATS.backup.age_seconds / 3600)}h
            </span>
          </div>
          <div className="delta">
            last success {fmtRelative(STATS.backup.last_success_iso)} · stale after {STATS.backup.stale_after / 3600}h
          </div>
        </div>
      </div>

      <div style={{display: "grid", gridTemplateColumns: "1.4fr 1fr", gap: 16, marginTop: 16}}>
        <div className="metric" style={{padding: "18px 20px"}}>
          <div className="row">
            <div className="label">Vast.ai spend · last 14 days</div>
            <div className="spacer"/>
            <div className="mono muted" style={{fontSize: 11}}>
              7d <span className="tnum" style={{color: "var(--fg-soft)"}}>{fmtUsd(STATS.vast_spend_7d)}</span>
              {"   "}
              30d <span className="tnum" style={{color: "var(--fg-soft)"}}>{fmtUsd(STATS.vast_spend_30d)}</span>
            </div>
          </div>
          <Sparkline series={SPEND_SERIES} cap={STATS.daily_spend_cap_usd}/>
        </div>

        <div className="metric" style={{padding: "18px 20px"}}>
          <div className="label" style={{marginBottom: 12}}>Jobs by status · 24h</div>
          <StatusBars stats={STATS.jobs_by_status}/>
        </div>
      </div>

      <div className="section-label" style={{marginTop: 36}}>
        <span>Recent failures · 7d</span>
        <span className="mono muted" style={{fontSize: 11}}>
          {RECENT_FAILURES.length} failed · last 24h: <span style={{color: "var(--err)"}} className="tnum">{RECENT_FAILURES.filter(f => f.failed_at.startsWith("2026-05-16")).length}</span>
        </span>
      </div>
      {RECENT_FAILURES.length > 0
        ? RECENT_FAILURES.map(f => <FailureRow key={f.id} f={f} navigate={navigate} onRetryJob={onRetryJob} onDeleteJob={onDeleteJob}/>)
        : <div className="empty"><div className="empty-title">No recent failures</div><div>Failed jobs from the last 7 days will appear here.</div></div>}

      <div className="section-label" style={{marginTop: 36}}>
        <span>System</span>
      </div>
      <div style={{display: "grid", gridTemplateColumns: "repeat(2, 1fr)", gap: 12,
                   fontFamily: "var(--font-mono)", fontSize: 12.5}}>
        {system.length > 0
          ? system.map((row) => <SystemRow key={row.label} label={row.label} value={row.value} status={row.status}/>)
          : <div className="empty" style={{gridColumn: "1 / -1"}}><div className="empty-title">System snapshot unavailable</div><div>Refresh to request a fresh ops snapshot.</div></div>}
      </div>
    </div>
  );
}

function Sparkline({ series, cap }) {
  const w = 100, h = 100;
  const max = Math.max(...series, cap);
  const pts = series.map((v, i) => {
    const x = (i / (series.length - 1)) * w;
    const y = h - (v / max) * h * 0.85 - 6;
    return [x, y];
  });
  const linePath = "M " + pts.map(p => `${p[0].toFixed(1)},${p[1].toFixed(1)}`).join(" L ");
  const areaPath = linePath + ` L ${w},${h} L 0,${h} Z`;
  const capY = h - (cap / max) * h * 0.85 - 6;
  return (
    <svg className="spark" viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none"
         style={{height: 80, marginTop: 12}}>
      <line x1="0" x2={w} y1={capY} y2={capY}
            stroke="var(--err)" strokeWidth="0.5" strokeDasharray="2 2"/>
      <path d={areaPath} className="area" opacity="0.4"/>
      <path d={linePath} className="line"/>
      {pts.map((p, i) => (
        <circle key={i} cx={p[0]} cy={p[1]} r={i === pts.length - 1 ? 2 : 0.8} className="dot"/>
      ))}
    </svg>
  );
}

function StatusBars({ stats }) {
  const entries = Object.entries(stats).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...entries.map(e => e[1]));
  const colorOf = (s) => ({
    done: "var(--ok)", failed: "var(--err)",
    queued: "var(--info)", downloading: "var(--accent)",
    transcribing: "var(--accent)", summarizing: "var(--accent)",
  })[s] || "var(--muted)";
  return (
    <div style={{display: "flex", flexDirection: "column", gap: 8}}>
      {entries.map(([s, n]) => (
        <div key={s} style={{display: "grid", gridTemplateColumns: "100px 1fr 36px", gap: 10, alignItems: "center"}}>
          <div className="mono" style={{fontSize: 12, color: "var(--fg-soft)"}}>{s}</div>
          <div className="bar-track" style={{marginTop: 0, height: 10}}>
            <div style={{width: `${(n / max) * 100}%`, background: colorOf(s)}}/>
          </div>
          <div className="mono tnum" style={{fontSize: 12, textAlign: "right"}}>{n}</div>
        </div>
      ))}
    </div>
  );
}

function SystemRow({ label, value, status }) {
  return (
    <div style={{
      display: "flex", gap: 12, alignItems: "center",
      padding: "10px 14px",
      border: "1px solid var(--border-soft)",
      borderRadius: "var(--radius)",
      background: "var(--bg-card)",
    }}>
      <span className="status-glyph" style={{
        color: status === "ok" ? "var(--ok)" : status === "warn" ? "var(--warn)" : "var(--err)",
      }}>{status === "ok" ? "●" : "○"}</span>
      <span className="muted" style={{fontSize: 11.5}}>{label}</span>
      <div className="spacer"/>
      <span style={{color: "var(--fg-soft)", fontSize: 12}}>{value}</span>
    </div>
  );
}
