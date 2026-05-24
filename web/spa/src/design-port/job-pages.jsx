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
const STAGE_SUBLABEL = {
  queued: "Waiting for a worker slot",
  downloading: "yt-dlp · residential IP",
  transcribing: "faster-whisper · Vast.ai GPU",
  summarizing: "codex CLI · prompt template v3",
  done: "Shortlinks · webhook · DB write",
};

function QueuePage({ navigate }) {
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
          <button className="btn"><IconRefresh size={14}/> Poll now</button>
          <button className="btn primary" onClick={() => navigate(null, { openCmdk: true })}>
            <IconPlus size={14}/> Submit URL
          </button>
        </div>
      </div>

      {ACTIVE_JOBS.map(j => <JobCard key={j.id} job={j} navigate={navigate}/>)}

      {RECENT_FAILURES.length > 0 && (
        <>
          <div className="section-label" style={{marginTop: 40}}>
            <span>Recent terminal jobs · failed</span>
            <a onClick={() => navigate("ops")} style={{cursor: "pointer", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--link)"}}>
              all failures →
            </a>
          </div>
          {RECENT_FAILURES.slice(0, 2).map(f => <FailureRow key={f.id} f={f}/>)}
        </>
      )}
    </div>
  );
}

function JobCard({ job, navigate }) {
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

function StatusChip({ status }) {
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

function PipelineDiagram({ job, compact }) {
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
            {!compact && (
              <div className="mono muted" style={{fontSize: 11.5, marginTop: 2}}>
                {STAGE_SUBLABEL[s]}
              </div>
            )}
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

function JobDetail({ id, navigate }) {
  const job = ACTIVE_JOBS.find(j => j.id === id) || ACTIVE_JOBS[0];

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

      <div className="section-label">
        <span>Pipeline log</span>
        <span className="mono muted" style={{fontSize: 11}}>
          <span className="live-dot"/> tailing
        </span>
      </div>
      <LogTail job={job}/>

      <div className="section-label">
        <span>Job actions</span>
      </div>
      <div className="row" style={{gap: 8, flexWrap: "wrap"}}>
        <button className="btn"><IconCopy size={14}/> Copy job JSON</button>
        <button className="btn"><IconExternal size={14}/> Open in Prometheus</button>
        <button className="btn" style={{color: "var(--err)", borderColor: "color-mix(in oklab, var(--err) 32%, var(--border))"}}>
          <IconX size={14}/> Cancel job
        </button>
      </div>

      <div className="hr"/>
      <div className="mono muted" style={{fontSize: 11.5, lineHeight: 1.7}}>
        <div>This page polls <code>GET /jobs/{job.id}</code> every 2s while the job is in flight.</div>
        <div>Webhooks fire on terminal status (<code>done</code> | <code>failed</code>).</div>
        <div>Daily Vast spend cap: <span className="tnum">{fmtUsd(STATS.daily_spend_cap_usd)}</span> · used <span className="tnum">{fmtUsd(STATS.vast_spend_24h)}</span> in the last 24h.</div>
      </div>
    </div>
  );
}

function LogTail({ job }) {
  const lines = buildLog(job);
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
        <span className="live-dot"/> waiting for next update…
      </div>
    </div>
  );
}
function currentT() {
  return new Date("2026-05-16T09:45:32Z").toISOString().slice(11,19);
}
function buildLog(job) {
  const lines = [];
  let t = new Date(job.started_at).getTime();
  const push = (tag, msg, color) => {
    lines.push({ t: new Date(t).toISOString().slice(11,19), tag, msg, color });
    t += 800 + Math.random() * 2000;
  };
  push("[queue]", `enqueued · position 1 of 1 · source=${job.source}`);
  if (job.stages.downloading.state !== "pending") {
    push("[worker]", `claim job ${job.id} · worker=scribe-worker-1`);
    push("[dl]",     `yt-dlp · client=android-vr · ${job.video_id} → /tmp/scribe-${job.id}.m4a`, "var(--info)");
    if (job.stages.downloading.state === "done") {
      push("[dl]",     `downloaded 78.2 MB in ${fmtElapsed(job.stages.downloading.duration_s || 72)} · 1.1 MB/s`, "var(--ok)");
      push("[ffmpeg]", `→ 16kHz mono WAV · 24 min audio · 28 MB`);
    }
  }
  if (job.stages.transcribing.state !== "pending") {
    push("[vast]",   `provisioning instance · template=whisper-l3-turbo · RTX 4090 · est. \$0.34/h`);
    push("[vast]",   `instance i-8e9b2 ready in 18s · ssh tunnel up`, "var(--info)");
    push("[whisper]",`faster-whisper large-v3-turbo · float16 · vad_filter=on`);
    if (job.stages.transcribing.state === "active") {
      push("[whisper]", `progress 4:21 / 16:42 · 1.6× realtime · spend so far \$0.0084`, "var(--accent)");
    }
    if (job.stages.transcribing.state === "done") {
      push("[whisper]", `done · 16:42 audio in ${fmtElapsed(job.stages.transcribing.duration_s || 318)} · \$0.0141`, "var(--ok)");
    }
  }
  if (job.stages.summarizing.state !== "pending") {
    push("[codex]", `acquired codex lock · prompt v3 · model=gpt-5 · temp=0.2`);
    if (job.stages.summarizing.state === "active") {
      push("[codex]", `streaming · 240 tok/s · 62% done`, "var(--accent)");
    }
  }
  return lines;
}

function FailureRow({ f }) {
  return (
    <div className="failure-row">
      <div>
        <div className="err-title">{f.title}</div>
        <div className="err-msg">{f.error}</div>
        <div className="err-meta">job_id {f.id} · {fmtRelative(f.failed_at)} · via {f.source}</div>
      </div>
      <div className="row" style={{gap: 6}}>
        <button className="btn"><IconRefresh size={12}/> Retry</button>
        <button className="btn ghost"><IconExternal size={12}/></button>
      </div>
    </div>
  );
}

Object.assign(window, { QueuePage, JobDetail, FailureRow, StatusChip, PipelineDiagram });
