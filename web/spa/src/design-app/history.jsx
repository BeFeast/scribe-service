// History — chronological log of every job (done, failed, running, queued).
// Polls /api/jobs with limit/offset pagination + optional status filter.
import React from "react";
import { fetchJson } from "./api.jsx";
import { fmtRelative } from "./data.js";
import { IconArrow, IconExternal, IconRefresh, IconX } from "./icons.jsx";
import { StatusChip } from "./job-pages.jsx";

const PAGE_SIZE = 50;
const STATUS_OPTIONS = [
  { value: "", label: "All" },
  { value: "queued", label: "Queued" },
  { value: "downloading", label: "Downloading" },
  { value: "transcribing", label: "Transcribing" },
  { value: "summarizing", label: "Summarizing" },
  { value: "done", label: "Done" },
  { value: "failed", label: "Failed" },
];

export function HistoryPage({ navigate, auth, onDeleteJob }) {
  const [status, setStatus] = React.useState("");
  const [offset, setOffset] = React.useState(0);
  const [body, setBody] = React.useState({ jobs: [], total: 0, limit: PAGE_SIZE, offset: 0 });
  const [state, setState] = React.useState({ loading: true, error: null });

  const load = React.useCallback(async (signal) => {
    setState({ loading: true, error: null });
    try {
      const params = new URLSearchParams();
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(offset));
      if (status) params.set("status", status);
      const url = "/api/jobs?" + params.toString();
      const data = await fetchJson(auth, url, signal);
      if (!signal?.aborted) {
        setBody(data);
        setState({ loading: false, error: null });
      }
    } catch (error) {
      if (!signal?.aborted) {
        setState({ loading: false, error: error instanceof Error ? error.message : String(error) });
      }
    }
  }, [auth, status, offset]);

  React.useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const totalLabel = body.total === 1 ? "1 job" : `${body.total} jobs`;
  const pageStart = body.jobs.length === 0 ? 0 : offset + 1;
  const pageEnd = offset + body.jobs.length;
  const hasPrev = offset > 0;
  const hasNext = offset + body.jobs.length < body.total;

  return (
    <div className="pane">
      <div className="pane-header">
        <div>
          <h1 className="pane-h1">History</h1>
          <div className="pane-sub">
            {totalLabel}
            {body.total > 0 && (
              <> · showing <span className="tnum">{pageStart}</span>–<span className="tnum">{pageEnd}</span></>
            )}
            {status && <> · status <span className="mono">{status}</span></>}
          </div>
        </div>
        <div className="pane-actions">
          <label className="mono muted" style={{fontSize: 12, display: "inline-flex", alignItems: "center", gap: 6}}>
            <span>status</span>
            <select
              value={status}
              onChange={(event) => {
                setStatus(event.target.value);
                setOffset(0);
              }}
              style={{
                fontFamily: "var(--font-mono)",
                fontSize: 12,
                padding: "4px 8px",
                border: "1px solid var(--border)",
                borderRadius: "var(--radius)",
                background: "var(--bg-card)",
                color: "var(--fg)",
              }}
            >
              {STATUS_OPTIONS.map((opt) => (
                <option key={opt.value || "all"} value={opt.value}>{opt.label}</option>
              ))}
            </select>
          </label>
          <button className="btn" onClick={() => load()}>
            <IconRefresh size={14}/> Refresh
          </button>
        </div>
      </div>

      {state.loading && body.jobs.length === 0 && (
        <div className="empty">
          <div className="empty-title">Loading history</div>
          <div>Fetching /api/jobs.</div>
        </div>
      )}
      {state.error && (
        <div className="empty">
          <div className="empty-title">History unavailable</div>
          <div>{state.error}</div>
        </div>
      )}
      {!state.loading && !state.error && body.jobs.length === 0 && (
        <div className="empty">
          <div className="empty-title">No jobs yet</div>
          <div>Submitted jobs will appear here once any have run.</div>
        </div>
      )}

      {body.jobs.length > 0 && (
        <div style={{
          border: "var(--rule)",
          borderRadius: "var(--radius-lg)",
          background: "var(--bg-card)",
          overflow: "hidden",
          marginBottom: 16,
        }}>
          {body.jobs.map((row) => (
            <HistoryRow key={row.id} row={row} navigate={navigate} onDeleteJob={onDeleteJob} onDeleted={() => load()}/>
          ))}
        </div>
      )}

      {body.total > 0 && (
        <div className="row" style={{gap: 12, marginTop: 12}}>
          <div className="mono muted" style={{fontSize: 12}}>
            page {Math.floor(offset / PAGE_SIZE) + 1} of {Math.max(1, Math.ceil(body.total / PAGE_SIZE))}
          </div>
          <div className="spacer"/>
          <button
            className="btn"
            disabled={!hasPrev}
            onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
          >
            ← Previous
          </button>
          <button
            className="btn"
            disabled={!hasNext}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Next →
          </button>
        </div>
      )}
    </div>
  );
}

function HistoryRow({ row, navigate, onDeleteJob, onDeleted }) {
  const transcriptId = row.transcript_id;
  const isTerminal = row.status === "failed" || row.status === "done";
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState(null);
  const dismiss = async () => {
    if (!onDeleteJob || busy) return;
    setBusy(true);
    setError(null);
    try {
      await onDeleteJob(row.id);
      onDeleted?.();
    } catch (err) {
      setBusy(false);
      setError(err instanceof Error ? err.message : String(err));
    }
  };
  return (
    <div style={{
      display: "grid",
      gridTemplateColumns: "1fr auto auto auto auto",
      gap: 16,
      alignItems: "center",
      padding: "12px 16px",
      borderBottom: "1px solid var(--border-soft)",
    }}>
      <div style={{minWidth: 0}}>
        <div style={{
          fontWeight: 550, fontSize: 14, marginBottom: 4,
          overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
        }}>
          {row.title || row.video_id || `Job #${row.id}`}
        </div>
        <div className="mono muted" style={{fontSize: 11.5}}>
          job_id <span style={{color: "var(--fg-soft)"}}>{row.id}</span>
          {row.source && <> · via {row.source}</>}
          {row.source_label && <> · {row.source_label}</>}
          <> · created {fmtRelative(row.created_at)}</>
          {row.error && <> · <span style={{color: "var(--err)"}}>{row.error}</span></>}
        </div>
      </div>
      <StatusChip status={row.status}/>
      <a
        className="btn ghost"
        onClick={() => navigate("job", { id: row.id })}
        style={{fontSize: 12, padding: "4px 10px", cursor: "pointer"}}
      >
        Open <IconArrow size={11}/>
      </a>
      {transcriptId ? (
        <a
          className="btn ghost"
          onClick={() => navigate("transcript", { id: transcriptId })}
          style={{fontSize: 12, padding: "4px 10px", cursor: "pointer"}}
        >
          Transcript <IconExternal size={11}/>
        </a>
      ) : (
        <span style={{width: 1}}/>
      )}
      {isTerminal && onDeleteJob ? (
        <button
          className="btn"
          onClick={dismiss}
          disabled={busy}
          aria-label={`Clear job ${row.id}`}
          title={error || "Clear"}
          style={{
            fontSize: 12,
            padding: "4px 10px",
            color: "var(--err)",
            borderColor: "color-mix(in oklab, var(--err) 32%, var(--border))",
          }}
        >
          <IconX size={11}/> Clear
        </button>
      ) : (
        <span style={{width: 1}}/>
      )}
    </div>
  );
}
