// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { isInFlight } from "./api.jsx";
import { IconArrow, IconClock, IconCopy, IconExternal, IconPlus, IconRefresh, IconX } from "./icons.jsx";
import { ACTIVE_JOBS, CURRENT_JOB, CURRENT_JOB_LOG, CURRENT_JOB_STATE, RECENT_FAILURES, STATS, fmtElapsed, fmtRelative, fmtUsd } from "./data.js";
// Job-in-flight detail — real-time pipeline diagram.
// Shows all 5 stages, current stage animated, logs panel, kill / retry actions.

const STAGE_ORDER = ["queued", "downloading", "transcribing", "summarizing", "done"];
const STAGE_LABEL = {
  queued: "Queue",
  downloading: "Download",
  transcribing: "Transcribe",
  summarizing: "Summarize",
  done: "Publish",
};

export function QueuePage({ navigate, loading, error, onRefresh, onRetryJob, onDeleteJob }) {
  return (
    <div className="pane">
      <div className="pane-header">
        <div>
          <h1 className="pane-h1">Queue</h1>
          <div className="pane-sub">
            {ACTIVE_JOBS.length} in flight · workers <span className="tnum">{STATS.worker_pool.active}/{STATS.worker_pool.total}</span> busy
            · <span className="live-dot" style={{verticalAlign: "middle"}}/> live
          </div>
        </div>
        <div className="pane-actions">
          <button className="btn" onClick={onRefresh}><IconRefresh size={14}/> Poll now</button>
          <button className="btn primary" onClick={() => navigate(null, { openCmdk: true })}>
            <IconPlus size={14}/> Submit URL
          </button>
        </div>
      </div>

      {loading && <div className="empty"><div className="empty-title">Loading queue</div><div>Fetching active jobs.</div></div>}
      {!loading && error && <div className="empty"><div className="empty-title">Queue unavailable</div><div>{error}</div></div>}
      {!loading && !error && ACTIVE_JOBS.length === 0 && <div className="empty"><div className="empty-title">Queue is idle</div><div>No active jobs right now.</div></div>}
      {!loading && !error && ACTIVE_JOBS.map(j => <JobCard key={j.id} job={j} navigate={navigate}/>)}

      {RECENT_FAILURES.length > 0 && (
        <>
          <div className="section-label" style={{marginTop: 40}}>
            <span>Recent terminal jobs · failed</span>
            <a onClick={() => navigate("ops")} style={{cursor: "pointer", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--link)"}}>
              all failures →
            </a>
          </div>
          {RECENT_FAILURES.slice(0, 2).map(f => <FailureRow key={f.id} f={f} navigate={navigate} onRetryJob={onRetryJob} onDeleteJob={onDeleteJob}/>)}
        </>
      )}
    </div>
  );
}

export function JobCard({ job, navigate }) {
  return (
    <div style={{
      border: "var(--rule)",
      borderRadius: "var(--radius-lg)",
      background: "var(--bg-card)",
      marginBottom: 16,
      overflow: "hidden",
    }}>
      <div style={{
        padding: "14px 18px",
        borderBottom: "1px solid var(--border-soft)",
        display: "flex", alignItems: "center", gap: 12,
      }}>
        <div className="mono muted" style={{fontSize: 12}}>job_id <span style={{color: "var(--fg-soft)"}}>{job.id}</span></div>
        <div className="mono muted" style={{fontSize: 12, opacity: 0.5}}>·</div>
        <div className="mono muted" style={{fontSize: 12}}>via {job.source}</div>
        <div className="spacer"/>
        <StatusChip status={job.status}/>
        <span className="mono muted" style={{fontSize: 12}}>
          <IconClock size={11} style={{verticalAlign: -1, marginRight: 3}}/>
          {fmtElapsed(job.elapsed_s)}
        </span>
        <button className="btn ghost" onClick={() => navigate("job", { id: job.id })}
                style={{fontSize: 12, padding: "4px 10px"}}>
          Open <IconArrow size={11}/>
        </button>
      </div>
      <div style={{padding: "16px 18px"}}>
        <div style={{
          fontFamily: "var(--font-display)", fontSize: 17, fontWeight: 600,
          letterSpacing: "-0.01em", marginBottom: 14,
        }}>{job.title}</div>
        <PipelineDiagram job={job} compact/>
      </div>
    </div>
  );
}

export function StatusChip({ status }) {
  const map = {
    queued: { cls: "info", text: "queued", glyph: "○" },
    downloading: { cls: "run", text: "downloading", glyph: "↓" },
    transcribing: { cls: "run", text: "transcribing", glyph: "≋" },
    summarizing: { cls: "run", text: "summarizing", glyph: "✦" },
    done: { cls: "ok", text: "done", glyph: "✓" },
    failed: { cls: "err", text: "failed", glyph: "✗" },
  };
  const s = map[status] || map.queued;
  const isRun = ["downloading","transcribing","summarizing"].includes(status);
  return (
    <span className={"chip " + s.cls}>
      {isRun ? <span className="spinner"/> : <span className="status-glyph">{s.glyph}</span>}
      {s.text}
    </span>
  );
}

export function PipelineDiagram({ job, compact }) {
  return (
    <div className="pipeline">
      {STAGE_ORDER.map((s, i) => {
        const st = job.stages[s] || { state: "pending" };
        return (
          <div key={s} className={"stage " + st.state}>
            <div className="stage-num">stage {i+1}</div>
            <div className="stage-name">
              {st.state === "active" && <span className="spinner"/>}
              {st.state === "done" && <span className="status-glyph" style={{color: "var(--ok)"}}>✓</span>}
              {st.state === "pending" && <span className="status-glyph" style={{color: "var(--muted)"}}>○</span>}
              {st.state === "failed" && <span className="status-glyph" style={{color: "var(--err)"}}>✗</span>}
              <span>{STAGE_LABEL[s]}</span>
            </div>
            {st.note && <div className="stage-note">{st.note}</div>}
            {st.duration_s != null && st.state === "done" && (
              <div className="stage-note" style={{color: "var(--ok)"}}>
                completed in {fmtElapsed(st.duration_s)}
              </div>
            )}
            {st.state === "active" && st.progress != null && (
              <div className="progressbar"><div style={{width: `${st.progress * 100}%`}}/></div>
            )}
            {st.state === "active" && st.progress == null && (
              <div className="progressbar"><div style={{width: "100%", background: "color-mix(in oklab, var(--accent) 40%, transparent)"}}/></div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export function JobDetail({ id, navigate, log = CURRENT_JOB_LOG, onRefresh, onCancelJob, onRetryJob, onDeleteJob }) {
  const job = CURRENT_JOB || ACTIVE_JOBS.find((j) => j.id === id);
  const [actionState, setActionState] = React.useState({ pending: null, message: null, error: null });
  const actionAbortRef = React.useRef(null);
  React.useEffect(() => () => actionAbortRef.current?.abort(), []);
  const runAction = async (name, task) => {
    actionAbortRef.current?.abort();
    const controller = new AbortController();
    actionAbortRef.current = controller;
    setActionState({ pending: name, message: null, error: null });
    try {
      await task(controller.signal);
      if (!controller.signal.aborted) setActionState({ pending: null, message: `${name} complete`, error: null });
    } catch (error) {
      if (!controller.signal.aborted) setActionState({ pending: null, message: null, error: error instanceof Error ? error.message : String(error) });
    } finally {
      if (actionAbortRef.current === controller) actionAbortRef.current = null;
    }
  };
  const copyJob = () => runAction("copy", async () => {
    await navigator.clipboard.writeText(JSON.stringify(job, null, 2));
  });
  const cancelJob = () => runAction("cancel", async (signal) => {
    await onCancelJob?.(job.id, signal);
  });
  const retryJob = () => runAction("retry", async (signal) => {
    await onRetryJob?.(job.id, signal);
  });
  const refreshJob = () => runAction("refresh", async (signal) => {
    await onRefresh?.(job.id, signal);
  });
  const deleteJob = () => runAction("delete", async (signal) => {
    await onDeleteJob?.(job.id, signal);
    if (!signal.aborted) navigate("queue");
  });
  if (CURRENT_JOB_STATE.loading) return <div className="pane"><div className="empty"><div className="empty-title">Loading job</div><div>Fetching /jobs/{id}.</div></div></div>;
  if (!job || CURRENT_JOB_STATE.error) return <div className="pane"><a onClick={() => navigate("queue")} style={{display: "inline-flex", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--muted)", cursor: "pointer", marginBottom: 18}}>← Queue</a><div className="empty"><div className="empty-title">Job unavailable</div><div>{CURRENT_JOB_STATE.error || "No job is loaded."}</div></div></div>;

  return (
    <div className="pane">
      <a onClick={() => navigate("queue")}
         style={{display: "inline-flex", alignItems: "center", gap: 6,
                 fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--muted)",
                 cursor: "pointer", marginBottom: 18, textDecoration: "none"}}>
        ← Queue
      </a>

      <div className="row" style={{marginBottom: 8, gap: 12}}>
        <div className="mono muted" style={{fontSize: 12}}>
          job_id <span style={{color: "var(--fg-soft)"}}>{job.id}</span> · via {job.source}
        </div>
        <div className="spacer"/>
        <StatusChip status={job.status}/>
        <span className="mono muted" style={{fontSize: 12}}>
          <IconClock size={11} style={{verticalAlign: -1, marginRight: 3}}/>
          {fmtElapsed(job.elapsed_s)} elapsed
        </span>
      </div>
      <h1 className="detail-h1" style={{marginBottom: 12}}>{job.title}</h1>
      <div className="detail-meta">
        <a href={`https://youtu.be/${job.video_id}`} target="_blank" rel="noreferrer">
          <IconExternal size={11} style={{verticalAlign: -1, marginRight: 3}}/>
          {job.url}
        </a>
      </div>

      <PipelineDiagram job={job}/>

      {job.status === "failed" && job.error && (
        <>
          <div className="section-label"><span>Failure</span></div>
          <pre className="job-error">{job.error}</pre>
        </>
      )}

      <div className="section-label">
        <span>Pipeline log</span>
        <span className="mono muted" style={{fontSize: 11}}>
          <span className="live-dot"/> tailing
        </span>
      </div>
      <LogTail log={log}/>

      <div className="section-label">
        <span>Job actions</span>
      </div>
      <div className="row" style={{gap: 8, flexWrap: "wrap"}}>
        <button className="btn" onClick={copyJob} disabled={actionState.pending === "copy"}><IconCopy size={14}/> Copy job JSON</button>
        <a className="btn" href="/metrics" target="_blank" rel="noreferrer"><IconExternal size={14}/> Open in Prometheus</a>
        <button className="btn" onClick={refreshJob} disabled={actionState.pending === "refresh"}><IconRefresh size={14}/> Poll now</button>
        {job.status === "failed" && <button className="btn" onClick={retryJob} disabled={actionState.pending === "retry"}><IconRefresh size={14}/> Retry job</button>}
        {(job.status === "failed" || job.status === "done") && onDeleteJob && (
          <button
            className="btn"
            onClick={deleteJob}
            disabled={actionState.pending === "delete"}
            aria-label={`Clear job ${job.id}`}
            style={{color: "var(--err)", borderColor: "color-mix(in oklab, var(--err) 32%, var(--border))"}}
          >
            <IconX size={14}/> Clear
          </button>
        )}
        {isInFlight(job.status) && <button className="btn" onClick={cancelJob} disabled={actionState.pending === "cancel"} style={{color: "var(--err)", borderColor: "color-mix(in oklab, var(--err) 32%, var(--border))"}}>
          <IconX size={14}/> Cancel job
        </button>}
      </div>
      {(actionState.message || actionState.error) && (
        <div className="mono muted" style={{fontSize: 11.5, marginTop: 10, color: actionState.error ? "var(--err)" : "var(--ok)"}}>
          {actionState.error || actionState.message}
        </div>
      )}

      <div className="hr"/>
      <div className="mono muted" style={{fontSize: 11.5, lineHeight: 1.7}}>
        <div>This page polls <code>GET /jobs/{job.id}</code> every 2s while the job is in flight.</div>
        <div>Webhooks fire on terminal status (<code>done</code> | <code>failed</code>).</div>
        <div>Daily Vast spend cap: <span className="tnum">{fmtUsd(STATS.daily_spend_cap_usd)}</span> · used <span className="tnum">{fmtUsd(STATS.vast_spend_24h)}</span> in the last 24h.</div>
      </div>
    </div>
  );
}

export function selectLogLines(log) {
  const raw = Array.isArray(log?.lines) ? log.lines : [];
  return raw.map(adaptLogLine);
}

function LogTail({ log }) {
  const lines = selectLogLines(log);
  return (
    <div style={{
      fontFamily: "var(--font-mono)",
      fontSize: 12,
      background: "var(--bg-soft)",
      border: "1px solid var(--border-soft)",
      borderRadius: "var(--radius)",
      padding: "12px 16px",
      lineHeight: 1.65,
      maxHeight: 280,
      overflowY: "auto",
      fontFeatureSettings: '"tnum"',
    }}>
      {lines.map((ln, i) => (
        <div key={i} style={{color: ln.color || "var(--fg-soft)"}}>
          <span style={{color: "var(--muted)"}}>{ln.t}</span>{"  "}
          <span style={{color: "var(--muted)"}}>{ln.tag}</span>{" "}
          {ln.msg}
        </div>
      ))}
      <div style={{color: "var(--accent)", marginTop: 4}}>
        <span style={{color: "var(--muted)"}}>{currentT()}</span>{"  "}
        <span className="live-dot"/> {log?.connected ? "tailing live stream…" : log?.error || "waiting for next update…"}
      </div>
    </div>
  );
}
function adaptLogLine(line) {
  const ts = line.ts ? new Date(line.ts).toISOString().slice(11,19) : currentT();
  const level = line.lvl ? `[${String(line.lvl).toLowerCase()}]` : "[log]";
  const tag = line.stage ? `[${line.stage}]` : level;
  const color = line.lvl === "ERROR" ? "var(--err)" : line.lvl === "WARNING" ? "var(--warn)" : "var(--fg-soft)";
  return { t: ts, tag, msg: line.msg || JSON.stringify(line), color };
}
function currentT() {
  return new Date().toISOString().slice(11,19);
}

export function FailureRow({ f, navigate, onRetryJob, onDeleteJob }) {
  const [state, setState] = React.useState({ pending: null, error: null });
  const run = async (kind, action) => {
    if (!action) return;
    setState({ pending: kind, error: null });
    try {
      await action(f.id);
      setState({ pending: null, error: null });
    } catch (error) {
      setState({ pending: null, error: error instanceof Error ? error.message : String(error) });
    }
  };
  const retry = () => run("retry", onRetryJob);
  const dismiss = () => run("delete", onDeleteJob);
  return (
    <div className="failure-row">
      <div>
        <div className="err-title">{f.title}</div>
        <div className="err-msg">{f.error}</div>
        <div className="err-meta">job_id {f.id} · {fmtRelative(f.failed_at)} · via {f.source}</div>
        {state.error && <div className="err-meta" style={{color: "var(--err)"}}>{state.error}</div>}
      </div>
      <div className="row" style={{gap: 6}}>
        <button className="btn" onClick={retry} disabled={state.pending !== null || !onRetryJob}><IconRefresh size={12}/> Retry</button>
        {onDeleteJob && (
          <button
            className="btn"
            onClick={dismiss}
            disabled={state.pending !== null}
            aria-label={`Dismiss failed job ${f.id}`}
            style={{color: "var(--err)", borderColor: "color-mix(in oklab, var(--err) 32%, var(--border))"}}
          >
            <IconX size={12}/> Clear
          </button>
        )}
        <button className="btn ghost" onClick={() => navigate?.("job", { id: f.id })}><IconExternal size={12}/></button>
      </div>
    </div>
  );
}
