// Command palette — ⌘K. Detects YouTube URLs and offers a one-key submit.
// Falls through to a fuzzy-ish title search + a fixed list of commands.

const YT_RE = /(?:youtube\.com\/(?:watch\?v=|shorts\/|live\/)|youtu\.be\/)([A-Za-z0-9_-]{11})/;

function CommandPalette({ open, onClose, navigate }) {
  const [q, setQ] = React.useState("");
  const [sel, setSel] = React.useState(0);
  const [submitted, setSubmitted] = React.useState(null);
  const inputRef = React.useRef(null);

  React.useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current && inputRef.current.focus(), 30);
      setQ(""); setSel(0); setSubmitted(null);
    }
  }, [open]);

  const ytMatch = q.match(YT_RE);
  const ytId = ytMatch ? ytMatch[1] : null;

  const items = React.useMemo(() => {
    const list = [];
    if (!q || !ytId) {
      const lower = q.toLowerCase();
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
        { key: "go-ops", title: "Go to ops dashboard", glyph: "O", onPick: () => { navigate("ops"); onClose(); }, hint: "G O" },
        { key: "go-settings", title: "Go to settings", glyph: "S", onPick: () => { navigate("settings"); onClose(); }, hint: "G S" },
      ].forEach(c => list.push({ type: "cmd", ...c }));

      list.push({ section: "Recent submissions" });
      [
        { key: "r1", title: "Linus Torvalds on Git — Google Tech Talk", hint: "submitted 4m ago · telegram · transcribing 26%", onPick: () => { navigate("job", { id: 218 }); onClose(); }, type: "recent" },
        { key: "r2", title: "Rich Hickey — Simple Made Easy", hint: "submitted ~3h ago · #142 · done", onPick: () => { navigate("transcript", { id: 142 }); onClose(); }, type: "recent" },
        { key: "r3", title: "Bryan Cantrill — I Have Come to Bury the Andon Cord", hint: "submitted ~5h ago · #141 · done", onPick: () => { navigate("transcript", { id: 141 }); onClose(); }, type: "recent" },
      ].forEach(c => list.push(c));
    }
    return list;
  }, [q, ytId]);

  // Selectable item indices (skip section headers)
  const selectable = items.map((it, i) => ({ it, i })).filter(x => !x.it.section);
  const safeSel = Math.min(sel, Math.max(0, selectable.length - 1));

  function onKey(e) {
    if (e.key === "Escape") { onClose(); return; }
    if (ytId && !submitted && (e.key === "Enter" || (e.metaKey && e.key === "Enter"))) {
      e.preventDefault();
      setSubmitted({ id: 219, video_id: ytId, status: "queued" });
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
            <button className="btn" onClick={() => setSubmitted({ id: 219, video_id: ytId, status: "queued" })}>
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
              <div style={{fontWeight: 600}}>Queued as job #{submitted.id}</div>
              <div className="mono muted" style={{fontSize: 12, marginTop: 2}}>
                video_id {submitted.video_id} · est. ~6 min · webhook will fire on done|failed
              </div>
            </div>
            <button className="btn primary" onClick={() => { navigate("job", { id: 218 }); onClose(); }}>
              Watch pipeline <IconArrow size={12}/>
            </button>
          </div>
        )}

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

Object.assign(window, { CommandPalette });
