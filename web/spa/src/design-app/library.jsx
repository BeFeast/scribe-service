// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { IconCards, IconFeed, IconPlus, IconSearch, IconTable } from "./icons.jsx";
import { ACTIVE_JOBS, LIBRARY_TOTAL, TRANSCRIPTS, fmtDuration, fmtElapsed, fmtRelative } from "./data.js";
// Library — list of all transcripts. Layout switchable: table / feed / cards.
// Also surfaces in-flight jobs as a thin strip at the top.

function InFlightStrip({ navigate }) {
  if (!ACTIVE_JOBS.length) return null;
  return (
    <div style={{
      display: "flex", flexDirection: "column", gap: 0,
      border: "var(--rule)",
      borderRadius: "var(--radius-lg)",
      background: "var(--bg-card)",
      marginBottom: 24,
      overflow: "hidden",
    }}>
      <div style={{
        display: "flex", alignItems: "center", gap: 10,
        padding: "10px 16px",
        background: "var(--bg-soft)",
        borderBottom: "1px solid var(--border-soft)",
        fontFamily: "var(--font-mono)", fontSize: 11,
        textTransform: "uppercase", letterSpacing: "0.06em",
        color: "var(--muted)", fontWeight: 600,
        whiteSpace: "nowrap",
      }}>
        <span className="live-dot"/>
        <span>In flight</span>
        <span className="muted" style={{fontWeight: 400, textTransform: "none", letterSpacing: 0}}>
          · {ACTIVE_JOBS.length} job{ACTIVE_JOBS.length > 1 ? "s" : ""}
        </span>
        <div className="spacer"/>
        <a onClick={() => navigate("queue")} style={{cursor: "pointer", color: "var(--link)"}}>open queue →</a>
      </div>
      {ACTIVE_JOBS.map(j => <InFlightRow key={j.id} job={j} navigate={navigate}/>)}
    </div>
  );
}

function InFlightRow({ job, navigate }) {
  const stages = ["queued","downloading","transcribing","summarizing"];
  return (
    <div onClick={() => navigate("job", { id: job.id })}
         style={{
           display: "grid", gridTemplateColumns: "1fr auto auto", gap: 16,
           alignItems: "center",
           padding: "12px 16px",
           borderBottom: "1px solid var(--border-soft)",
           cursor: "pointer",
         }}>
      <div>
        <div style={{fontWeight: 550, fontSize: 14, marginBottom: 4, display: "flex", alignItems: "center", gap: 8}}>
          {job.title}
        </div>
        <div style={{display: "flex", gap: 4, alignItems: "center"}}>
          {stages.map((s, i) => {
            const st = job.stages[s];
            const c = st.state === "done" ? "var(--ok)"
                    : st.state === "active" ? "var(--accent)"
                    : "var(--border)";
            return (
              <React.Fragment key={s}>
                <div title={s} style={{
                  width: 32, height: 4, borderRadius: 999,
                  background: c,
                  position: "relative",
                  opacity: st.state === "pending" ? 0.5 : 1,
                }}>
                  {st.state === "active" && st.progress != null && (
                    <div style={{
                      position: "absolute", inset: 0,
                      background: "var(--accent)",
                      width: `${st.progress * 100}%`,
                      borderRadius: 999,
                      boxShadow: `0 0 8px ${st.state === "active" ? "var(--accent)" : "transparent"}`,
                    }}/>
                  )}
                </div>
              </React.Fragment>
            );
          })}
        </div>
      </div>
      <div className="mono muted" style={{fontSize: 11.5}}>
        {job.status === "queued" ? "queued"
         : job.status === "downloading" ? "downloading…"
         : job.status === "transcribing" ? `transcribing · ${Math.round((job.stages.transcribing.progress || 0) * 100)}%`
         : job.status === "summarizing" ? `summarizing · ${Math.round((job.stages.summarizing.progress || 0) * 100)}%`
         : job.status}
      </div>
      <div className="mono muted" style={{fontSize: 11.5}}>
        {fmtElapsed(job.elapsed_s)}
      </div>
    </div>
  );
}

export function LibraryPage({ navigate, t, setTweak, routeTag, loading, error, auth, onRefresh }) {
  const [q, setQ] = React.useState("");
  const [tag, setTag] = React.useState(routeTag || null);
  React.useEffect(() => setTag(routeTag || null), [routeTag]);
  const layout = t.libraryLayout || "table";

  const filtered = React.useMemo(() => {
    let rows = TRANSCRIPTS;
    if (q) rows = rows.filter(r => r.title.toLowerCase().includes(q.toLowerCase()));
    if (tag) rows = rows.filter(r => (r.tags||[]).includes(tag));
    return rows;
  }, [q, tag, TRANSCRIPTS]);

  const transcriptCount = q || tag ? filtered.length : Math.max(LIBRARY_TOTAL, TRANSCRIPTS.length);

  return (
    <div className="pane">
      <div className="pane-header">
        <div>
          <h1 className="pane-h1">Library</h1>
          <div className="pane-sub">
            {transcriptCount} transcript{transcriptCount !== 1 && "s"}
            {tag && <> · tag <code style={{padding: "1px 6px", background: "var(--bg-soft)", borderRadius: 3}}>{tag}</code>
              <a onClick={() => setTag(null)} style={{marginLeft: 8, cursor: "pointer", color: "var(--link)"}}>clear</a></>}
          </div>
        </div>
        <div className="pane-actions">
          <button className="btn primary" onClick={() => navigate(null, { openCmdk: true })}>
            <IconPlus size={14}/> Submit URL
          </button>
        </div>
      </div>

      <InFlightStrip navigate={navigate}/>

      <div className="lib-toolbar">
        <div className="search">
          <IconSearch size={14}/>
          <input placeholder="Search titles + transcripts…" value={q}
                 onChange={(e) => setQ(e.target.value)}/>
        </div>
        <div className="seg" role="tablist" aria-label="Layout">
          <button className={layout === "table" ? "active" : ""}
                  onClick={() => setTweak("libraryLayout", "table")}
                  title="Table layout">
            <IconTable size={14}/>
          </button>
          <button className={layout === "feed" ? "active" : ""}
                  onClick={() => setTweak("libraryLayout", "feed")}
                  title="Feed layout">
            <IconFeed size={14}/>
          </button>
          <button className={layout === "cards" ? "active" : ""}
                  onClick={() => setTweak("libraryLayout", "cards")}
                  title="Cards layout">
            <IconCards size={14}/>
          </button>
        </div>
      </div>

      {auth?.authRequired && <AuthRequiredState auth={auth} onRefresh={onRefresh}/>}
      {!auth?.authRequired && loading && <EmptyState title="Loading library" body="Fetching real transcripts from /api/library."/>}
      {!auth?.authRequired && !loading && error && <EmptyState title="Library unavailable" body={error}/>}
      {!auth?.authRequired && !loading && !error && filtered.length === 0 && <EmptyState title="No transcripts" body="Submit a YouTube URL to start the pipeline."/>}
      {!auth?.authRequired && !loading && !error && layout === "table" && <LibTable rows={filtered} navigate={navigate} onTag={setTag}/>}
      {!auth?.authRequired && !loading && !error && layout === "feed"  && <LibFeed  rows={filtered} navigate={navigate} onTag={setTag}/>}
      {!auth?.authRequired && !loading && !error && layout === "cards" && <LibCards rows={filtered} navigate={navigate} onTag={setTag}/>}
    </div>
  );
}

function AuthRequiredState({ auth, onRefresh }) {
  const waitingForClerk = auth.authRequired && !auth.clerkReady && !auth.authBlockedMessage;
  const body = auth.authBlockedMessage
    || (waitingForClerk
      ? "Authentication is required. Clerk is still loading; retry if the sign-in controls do not become available."
      : "Authentication is required to load the library from this public network.");
  return (
    <div className="empty auth-required" role="status" aria-live="polite">
      <div className="empty-title">Sign in required</div>
      <div>{body}</div>
      <div className="row" style={{justifyContent: "center", gap: 8, marginTop: 16}}>
        <button className="btn primary"
                onClick={() => auth.signIn()}
                disabled={auth.authRedirectInFlight || waitingForClerk}
                title={waitingForClerk ? "Waiting for Clerk browser runtime" : "Continue to Clerk sign-in"}>
          {auth.authRedirectInFlight || waitingForClerk ? <span className="spinner"/> : null}
          {auth.authRedirectInFlight ? "Opening Clerk..." : waitingForClerk ? "Preparing sign-in" : "Sign in"}
        </button>
        <button className="btn"
                onClick={() => {
                  auth.retryAuth();
                  onRefresh?.();
                }}>
          Retry
        </button>
      </div>
    </div>
  );
}

function LibTable({ rows, navigate, onTag }) {
  return (
    <table className="lib-table">
      <thead>
        <tr>
          <th className="col-num">#</th>
          <th>Title</th>
          <th className="col-tags">Tags</th>
          <th className="col-len">Length</th>
          <th className="col-time">Created</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(r => (
          <tr key={r.id} onClick={() => navigate("transcript", { id: r.id })}>
            <td className="col-num">{r.id}</td>
            <td className="col-title">
              {r.summary_md == null && <span className="chip warn" style={{marginRight: 8}}>partial</span>}
              {r.title}
            </td>
            <td className="col-tags">
              <div className="row-tags">
                {(r.tags||[]).map(tg => (
                  <span key={tg} className="tag" onClick={(e) => { e.stopPropagation(); onTag(tg); }}>
                    {tg}
                  </span>
                ))}
              </div>
            </td>
            <td className="col-meta col-len">{fmtDuration(r.duration_seconds)}</td>
            <td className="col-meta col-time">{fmtRelative(r.created_at)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function LibFeed({ rows, navigate, onTag }) {
  return (
    <div className="lib-feed">
      {rows.map(r => (
        <div key={r.id} className="feed-item" onClick={() => navigate("transcript", { id: r.id })}>
          <div className="feed-num">#{r.id}</div>
          <div>
            <div className="feed-meta-top">
              <span>{fmtRelative(r.created_at)}</span>
              <span className="sep">·</span>
              <span>{fmtDuration(r.duration_seconds)}</span>
              <span className="sep">·</span>
              <span>{r.lang || "—"}</span>
              {r.summary_md == null && <>
                <span className="sep">·</span>
                <span className="chip warn" style={{padding: "1px 6px"}}>partial</span>
              </>}
            </div>
            <h2 className="feed-title">{r.title}</h2>
            <p className="feed-excerpt">{previewSummary(r)}</p>
            <div className="feed-tags">
              {(r.tags||[]).map(tg => (
                <span key={tg} className="tag" onClick={(e) => { e.stopPropagation(); onTag(tg); }}
                      style={{fontFamily: "var(--font-mono)", fontSize: 11, padding: "1px 7px",
                              background: "var(--bg-soft)", border: "1px solid var(--border-soft)",
                              borderRadius: 999, color: "var(--fg-soft)", cursor: "pointer"}}>
                  {tg}
                </span>
              ))}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function LibCards({ rows, navigate, onTag }) {
  return (
    <div className="lib-cards">
      {rows.map(r => (
        <div key={r.id} className="card" onClick={() => navigate("transcript", { id: r.id })}>
          <div className="card-meta-top">
            <span>#{r.id}</span>
            <span style={{opacity: 0.4}}>·</span>
            <span>{fmtRelative(r.created_at)}</span>
            <span style={{opacity: 0.4}}>·</span>
            <span>{fmtDuration(r.duration_seconds)}</span>
            {r.summary_md == null && <>
              <div className="spacer"/>
              <span className="chip warn">partial</span>
            </>}
          </div>
          <h3 className="card-title">{r.title}</h3>
          <p className="card-excerpt">{previewSummary(r)}</p>
          <div className="card-foot">
            {(r.tags||[]).slice(0,3).map(tg => (
              <span key={tg} className="tag" onClick={(e) => { e.stopPropagation(); onTag(tg); }}
                    style={{fontFamily: "var(--font-mono)", fontSize: 10.5, padding: "1px 7px",
                            background: "var(--bg-soft)", border: "1px solid var(--border-soft)",
                            borderRadius: 999, color: "var(--fg-soft)", cursor: "pointer"}}>
                {tg}
              </span>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function previewSummary(r) {
  if (!r.summary_md) return "Whisper finished; summary regeneration pending. POST /transcripts/{id}/resummarize to retry.";
  // Pull first paragraph after "## TL;DR", strip markdown syntax for clean excerpt.
  const m = r.summary_md.match(/##\s*TL;DR\s*\n([^\n]+(?:\n[^\n#][^\n]*)*)/);
  const raw = m ? m[1] : r.summary_md;
  const clean = raw
    .replace(/\*\*(.+?)\*\*/g, "$1")
    .replace(/\*(.+?)\*/g, "$1")
    .replace(/`(.+?)`/g, "$1")
    .replace(/^#+\s*/gm, "")
    .replace(/\n+/g, " ")
    .trim();
  return clean.slice(0, 240);
}


function EmptyState({ title, body }) {
  return <div className="empty"><div className="empty-title">{title}</div><div>{body}</div></div>;
}
