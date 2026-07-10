// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { useAuth } from "../hooks/useAuth";
import { IconArrow, IconCheck, IconClock, IconPlus, IconSearch, IconWave } from "./icons.jsx";
import { ACTIVE_JOBS, STATS, TRANSCRIPTS, fmtDuration, fmtRelative, fmtUsd } from "./data.js";
import { isJobView, parseVideoUrl, pushRecentSubmission, readRecentSubmissions } from "./command-utils.js";
import { submitUploadJob } from "./api-jobs.js";
// Command palette — ⌘K. Detects YouTube URLs and offers a one-key submit.
// Falls through to a fuzzy-ish title search + a fixed list of commands.

export function CommandPalette({ open, onClose, navigate }) {
  const auth = useAuth();
  const [q, setQ] = React.useState("");
  const [sel, setSel] = React.useState(0);
  const [submitted, setSubmitted] = React.useState(null);
  const [recents, setRecents] = React.useState([]);
  const inputRef = React.useRef(null);
  const fileInputRef = React.useRef(null);

  React.useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current && inputRef.current.focus(), 30);
      setQ(""); setSel(0); setSubmitted(null);
      setRecents(readRecentSubmissions());
    }
  }, [open]);

  const videoUrl = parseVideoUrl(q);
  const ytId = videoUrl ? videoUrl.videoId : null;

  const items = React.useMemo(() => {
    const list = [];
    if (!q || !ytId) {
      const lower = q.toLowerCase();
      // Desktop upload entry point (#411): parity with the mobile CaptureSheet.
      // A native file picker submits via submitUploadJob → POST /jobs/upload.
      list.push({ section: "Add" });
      list.push({
        type: "upload",
        key: "upload-file",
        title: "Upload video / audio file…",
        hint: "attach a local file",
        onPick: () => { if (fileInputRef.current) fileInputRef.current.click(); },
      });

      const matched = TRANSCRIPTS
        .filter(t => !lower || t.title.toLowerCase().includes(lower) || (t.tags||[]).some(tg => tg.includes(lower)))
        .slice(0, 6)
        .map(t => ({
          type: "transcript",
          key: "t" + t.id,
          title: t.title,
          hint: `#${t.id} · ${fmtDuration(t.duration_seconds)} · ${fmtRelative(t.created_at)}`,
          onPick: () => { navigate("transcript", { id: t.id }); onClose(); },
        }));
      list.push({ section: "Transcripts" });
      list.push(...matched);

      const matchedJobs = ACTIVE_JOBS
        .filter(j => !lower || j.title.toLowerCase().includes(lower))
        .map(j => ({
          type: "job",
          key: "j" + j.id,
          title: j.title,
          hint: `job ${j.id} · ${j.status}`,
          live: true,
          onPick: () => { navigate("job", { id: j.id }); onClose(); },
        }));
      if (matchedJobs.length) {
        list.push({ section: "In flight" });
        list.push(...matchedJobs);
      }

      list.push({ section: "Navigate" });
      [
        { key: "go-lib", title: "Go to library", glyph: "L", onPick: () => { navigate("library"); onClose(); }, hint: "G L" },
        { key: "go-queue", title: "Go to queue", glyph: "Q", onPick: () => { navigate("queue"); onClose(); }, hint: "G Q" },
        { key: "go-history", title: "Go to history", glyph: "H", onPick: () => { navigate("history"); onClose(); }, hint: "G H" },
        { key: "go-ops", title: "Go to ops dashboard", glyph: "O", onPick: () => { navigate("ops"); onClose(); }, hint: "G O" },
        { key: "go-settings", title: "Go to settings", glyph: "S", onPick: () => { navigate("settings"); onClose(); }, hint: "G S" },
      ].forEach(c => list.push({ type: "cmd", ...c }));

      if (recents.length) {
        list.push({ section: "Recent submissions" });
        recents.forEach(r => list.push({
          type: "recent",
          key: "r" + r.id,
          title: r.title || r.video_id,
          hint: `job ${r.id}` + (r.status ? ` · ${r.status}` : ""),
          onPick: () => { navigate("job", { id: r.id }); onClose(); },
        }));
      }
    }
    return list;
  }, [q, ytId, recents, navigate, onClose, TRANSCRIPTS, ACTIVE_JOBS, STATS]);

  async function submitUrl() {
    if (!videoUrl || submitted) return;
    setSubmitted({ state: "submitting", video_id: ytId });
    try {
      const response = await auth.protectedFetch("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: videoUrl.url, source: "manual" }),
      });
      const body = await response.json().catch(() => null);
      if (!response.ok || !isJobView(body)) throw new Error("HTTP " + response.status);
      setSubmitted({ id: body.job_id, video_id: body.video_id, status: body.status });
      setRecents(pushRecentSubmission({ id: body.job_id, video_id: body.video_id, status: body.status }));
    } catch (error) {
      setSubmitted({ state: "error", video_id: ytId, message: error instanceof Error ? error.message : String(error) });
    }
  }

  // Upload path (#411): a picked local file submits via POST /jobs/upload and
  // surfaces the same submitting/queued/error states as submitUrl(). The 413
  // (too large) and 422 (invalid media) details bubble up from submitUploadJob.
  async function onPickFile(e) {
    const picked = e.target.files && e.target.files[0];
    // Reset so re-picking the same file fires change again.
    e.target.value = "";
    if (!picked || submitted) return;
    setSubmitted({ state: "submitting", video_id: picked.name });
    try {
      const result = await submitUploadJob(auth, picked, { source: "upload" });
      setSubmitted({ id: result.job_id, video_id: result.video_id, status: result.status });
      setRecents(pushRecentSubmission({ id: result.job_id, video_id: result.video_id, status: result.status }));
    } catch (error) {
      setSubmitted({ state: "error", video_id: picked.name, message: error instanceof Error ? error.message : String(error) });
    }
  }

  // Selectable item indices (skip section headers)
  const selectable = items.map((it, i) => ({ it, i })).filter(x => !x.it.section);
  const safeSel = Math.min(sel, Math.max(0, selectable.length - 1));

  function onKey(e) {
    if (e.key === "Escape") { onClose(); return; }
    if (ytId && !submitted && (e.key === "Enter" || (e.metaKey && e.key === "Enter"))) {
      e.preventDefault();
      void submitUrl();
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSel(s => (s + 1) % selectable.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSel(s => (s - 1 + selectable.length) % selectable.length);
    } else if (e.key === "Enter" && selectable[safeSel]) {
      e.preventDefault();
      selectable[safeSel].it.onPick && selectable[safeSel].it.onPick();
    }
  }

  if (!open) return null;
  return (
    <div className="cmdk-overlay" onClick={onClose}>
      <div className="cmdk-modal" onClick={(e) => e.stopPropagation()}>
        <div className="cmdk-input-row">
          <IconSearch size={16}/>
          <input ref={inputRef} placeholder="Paste a YouTube URL · or search transcripts, jobs, commands…"
                 value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey}/>
          <span className="kbd">esc</span>
        </div>

        {ytId && !submitted && (
          <div className="cmdk-submit">
            <IconPlus size={18}/>
            <div className="label">
              <div style={{fontWeight: 600, color: "var(--accent)"}}>Submit job</div>
              <div style={{fontSize: 12, color: "var(--fg-soft)", fontFamily: "var(--font-mono)", marginTop: 2}}>
                video_id <span className="tnum">{ytId}</span> · source=manual · cap remaining {fmtUsd(STATS.daily_spend_cap_usd - STATS.vast_spend_24h)}
              </div>
            </div>
            <button className="btn" onClick={() => submitUrl()}>
              Submit <span className="kbd" style={{marginLeft: 6}}>↵</span>
            </button>
          </div>
        )}

        {submitted && (
          <div style={{
            margin: 6, padding: "14px 16px",
            border: "1px solid color-mix(in oklab, var(--ok) 32%, transparent)",
            borderRadius: "var(--radius)",
            background: "color-mix(in oklab, var(--ok) 10%, var(--bg))",
            display: "flex", alignItems: "center", gap: 12, fontSize: 14,
          }}>
            <IconCheck size={18} style={{color: "var(--ok)"}}/>
            <div style={{flex: 1}}>
              <div style={{fontWeight: 600}}>Queued as job #{submitted.id || "…"}</div>
              <div className="mono muted" style={{fontSize: 12, marginTop: 2}}>
                video_id {submitted.video_id} · {submitted.state === "submitting" ? "submitting…" : submitted.state === "error" ? submitted.message : "webhook will fire on done|failed"}
              </div>
            </div>
            <button className="btn primary" disabled={!submitted.id}
                    onClick={() => { if (submitted.id) { navigate("job", { id: submitted.id }); onClose(); } }}>
              Watch pipeline <IconArrow size={12}/>
            </button>
          </div>
        )}

        <input
          ref={fileInputRef}
          type="file"
          accept="video/*,audio/*"
          onChange={onPickFile}
          style={{ display: "none" }}
        />

        <div className="cmdk-list">
          {items.map((it, idx) => {
            if (it.section) return <div key={"s" + idx} className="cmdk-section-label">{it.section}</div>;
            const myIdx = selectable.findIndex(x => x.i === idx);
            const isSel = myIdx === safeSel;
            return (
              <div key={it.key} className={"cmdk-item " + (isSel ? "sel" : "")}
                   onClick={it.onPick} onMouseEnter={() => setSel(myIdx)}>
                <div className="cmdk-glyph">
                  {it.type === "transcript" ? <IconWave size={13}/>
                   : it.type === "job"      ? <span className="live-dot" style={{margin: 0}}/>
                   : it.type === "cmd"      ? <span style={{fontFamily: "var(--font-mono)", fontSize: 11, fontWeight: 600}}>{it.glyph}</span>
                   : it.type === "recent"   ? <IconClock size={13}/>
                   : it.type === "upload"   ? <IconPlus size={13}/>
                   : <IconArrow size={13}/>}
                </div>
                <div className="cmdk-title">{it.title}</div>
                <div className="cmdk-hint">{it.hint}</div>
              </div>
            );
          })}
        </div>

        <div className="cmdk-foot">
          <span><span className="kbd">↑↓</span> navigate</span>
          <span><span className="kbd">↵</span> open</span>
          <span><span className="kbd">esc</span> close</span>
          <div className="grow"/>
          <span className="muted">tip: paste any youtube URL for instant submit</span>
        </div>
      </div>
    </div>
  );
}

