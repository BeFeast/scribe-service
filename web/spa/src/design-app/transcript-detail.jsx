// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { useAuth } from "../hooks/useAuth";
import { IconAlert, IconArrow, IconCheck, IconClock, IconCopy, IconDownload, IconExternal, IconLink, IconRSS, IconRefresh, IconSparkle, IconWave, IconX } from "./icons.jsx";
import { CURRENT_TRANSCRIPT, CURRENT_TRANSCRIPT_STATE, TRANSCRIPTS, fmtDuration, fmtRelative, fmtUsd } from "./data.js";
// Transcript detail — title, meta, summary (rendered from MD), transcript excerpt.

export function TranscriptDetail({ id, navigate, onRefresh }) {
  const auth = useAuth();
  const t = CURRENT_TRANSCRIPT || TRANSCRIPTS.find((r) => r.id === id);
  if (CURRENT_TRANSCRIPT_STATE.loading) return <DetailState title="Loading transcript" body="Fetching /transcripts/{id}."/>;
  if (!t || CURRENT_TRANSCRIPT_STATE.error) return <DetailState title="Transcript unavailable" body={CURRENT_TRANSCRIPT_STATE.error || "No transcript is loaded."} navigate={navigate}/>;
  const [regenerating, setRegenerating] = React.useState(false);
  const [copied, setCopied] = React.useState(null);
  const [shareOpen, setShareOpen] = React.useState(false);
  const [deleteConfirm, setDeleteConfirm] = React.useState(false);

  function copy(text, key) {
    try {
      navigator.clipboard && navigator.clipboard.writeText(text);
      setCopied(key); setTimeout(() => setCopied(null), 1600);
    } catch (e) {
      setCopied("err:" + key); setTimeout(() => setCopied(null), 2400);
    }
  }
  async function regen() {
    setRegenerating(true);
    try {
      const response = await auth.protectedFetch("/transcripts/" + t.id + "/resummarize", { method: "POST" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      onRefresh && onRefresh();
    } finally {
      setRegenerating(false);
    }
  }
  async function deleteTranscript() {
    const response = await auth.protectedFetch("/admin/transcripts/" + t.id, { method: "DELETE" });
    if (response.ok) navigate("library");
  }

  return (
    <div className="pane pane-narrow">
      <div className="row" style={{marginBottom: 18}}>
        <a onClick={() => void deleteTranscript()}
           style={{display: "inline-flex", alignItems: "center", gap: 6,
                   fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--muted)",
                   cursor: "pointer", textDecoration: "none"}}>
          ← Library
        </a>
        <div className="spacer"/>
        <div className="share-wrap">
          <button className="btn primary" onClick={() => setShareOpen(o => !o)}>
            <IconLink size={13}/> Share
          </button>
          {shareOpen && (
            <ShareSheet t={t} onClose={() => setShareOpen(false)}
                        copy={copy} copied={copied}/>
          )}
        </div>
      </div>

      <div className="mono muted" style={{fontSize: 12, marginBottom: 8}}>
        #{t.id} · transcript
      </div>
      <h1 className="detail-h1">{t.title}</h1>

      <div className="detail-meta">
        <span>{t.lang || "—"}</span>
        <span className="sep">·</span>
        <span><IconClock size={12} style={{verticalAlign: -1, marginRight: 3}}/>{fmtDuration(t.duration_seconds)}</span>
        <span className="sep">·</span>
        <span>{fmtRelative(t.created_at)}</span>
        <span className="sep">·</span>
        <a href={`https://youtu.be/${t.video_id}`} target="_blank" rel="noreferrer">
          <IconExternal size={11} style={{verticalAlign: -1, marginRight: 3}}/>
          youtu.be/{t.video_id}
        </a>
      </div>

      {t.tags && (
        <div className="detail-tags">
          {t.tags.map(tg => (
            <span key={tg} className="chip" onClick={() => navigate("library", { tag: tg })}
                  style={{cursor: "pointer"}}>
              #{tg}
            </span>
          ))}
        </div>
      )}

      {t.summary_md == null ? (
        <PartialNotice transcript={t} onRegen={regen} regenerating={regenerating}/>
      ) : (
        <>
          <div className="section-label">
            <span>Summary</span>
            <div className="row" style={{gap: 6}}>
              <button className="btn ghost" onClick={() => copy(t.summary_md, "all")}
                      style={{fontSize: 12, padding: "4px 8px"}}>
                <IconCopy size={12}/> {copied === "all" ? "Copied" : "Copy"}
              </button>
              <button className="btn ghost" onClick={regen} disabled={regenerating}
                      style={{fontSize: 12, padding: "4px 8px",
                              color: regenerating ? "var(--accent)" : undefined}}>
                {regenerating ? <span className="spinner"/> : <IconRefresh size={12}/>}
                {regenerating ? "Regenerating…" : "Regenerate"}
              </button>
            </div>
          </div>
          <div className="prose"><Markdown src={t.summary_md}/></div>
        </>
      )}

      <div className="section-label">
        <span>Transcript excerpt</span>
        <div className="row" style={{gap: 6}}>
          <span className="mono muted" style={{fontSize: 11}}>
            ~{Math.round((t.duration_seconds || 1) / 60)} min · {t.lang}
          </span>
          <button className="btn ghost" style={{fontSize: 12, padding: "4px 8px"}}>
            <IconDownload size={12}/> Download .md
          </button>
        </div>
      </div>
      <div className="transcript-body">
        {t.transcript_excerpt}{"\n\n…"}
      </div>

      <div className="hr"/>
      <div className="mono muted" style={{fontSize: 11, display: "flex", gap: 16, flexWrap: "wrap"}}>
        <span>job_id: <span className="tnum">{t.id + 76}</span></span>
        <span>video_id: {t.video_id}</span>
        <span>vast_cost: {fmtUsd(t.vast_cost)}</span>
        <span>created: {t.created_at.replace("T", " ").replace("Z", "Z")}</span>
      </div>

      <div className="danger-zone">
        <IconAlert size={20} style={{color: "var(--err)", flexShrink: 0}}/>
        <div className="dz-text">
          <div className="dz-title">Delete transcript</div>
          <div className="dz-sub">
            Removes the row plus its shortlinks. The owning job ({t.id + 76}) is kept;
            resubmitting the same video will re-run the full pipeline (~{fmtUsd(t.vast_cost || 0.02)}).
          </div>
        </div>
        {deleteConfirm ? (
          <div className="row" style={{gap: 6, flexShrink: 0}}>
            <button className="btn ghost" onClick={() => setDeleteConfirm(false)}
                    style={{fontSize: 12, padding: "5px 10px"}}>
              Cancel
            </button>
            <button className="btn danger" onClick={() => navigate("library")}
                    style={{fontSize: 12, padding: "5px 12px"}}>
              <IconX size={12}/> Yes, delete
            </button>
          </div>
        ) : (
          <button className="btn danger" onClick={() => setDeleteConfirm(true)}
                  style={{fontSize: 12, padding: "5px 12px", flexShrink: 0}}>
            Delete…
          </button>
        )}
      </div>
    </div>
  );
}

// ─── Share sheet ──────────────────────────────────────────────────────────
function ShareSheet({ t, onClose, copy, copied }) {
  const ref = React.useRef(null);
  const [visibility, setVisibility] = React.useState("public");
  const fullUrl = `scribe.oklabs.uk/transcripts/${t.id}`;
  const shortlink = t.summary_shortlink || `go.oklabs.uk/${t.id}s`;

  React.useEffect(() => {
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) onClose();
    }
    function onKey(e) { if (e.key === "Escape") onClose(); }
    setTimeout(() => document.addEventListener("click", onDoc), 0);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("click", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  function isCopied(key)  { return copied === key; }
  function isErr(key)     { return copied === "err:" + key; }
  function statusFor(key, label) {
    if (isErr(key))    return <span className="sh-status err"><IconX size={11}/> failed — clipboard blocked</span>;
    if (isCopied(key)) return <span className="sh-status ok"><IconCheck size={11}/> {label || "copied"}</span>;
    return null;
  }

  function download(filename, ext) {
    // Simulated for prototype — real impl hits /transcripts/{id}/{kind}.md
    copy(`# ${t.title}\n\n(mock download: ${filename})`, "dl:" + ext);
  }

  return (
    <div className="share-sheet" ref={ref}>
      <div className="sh-hd">
        <span className="lbl">Share</span>
        <div className="spacer"/>
        <span className={"vis " + visibility}
              onClick={() => setVisibility(visibility === "public" ? "unlisted" : "public")}
              title="Click to toggle visibility">
          <span className="vis-dot"/>
          {visibility === "public" ? "public" : "unlisted"}
        </span>
      </div>

      {/* Primary action: the shortlink */}
      <div className="sh-url">
        <span className="scheme">go.oklabs.uk/</span>
        <span className="path">{shortlink.replace(/^go\.oklabs\.uk\//, "")}</span>
        <button className="btn primary"
                onClick={() => copy("https://" + shortlink, "sl")}
                style={{fontSize: 12, padding: "5px 10px"}}>
          {isCopied("sl") ? <><IconCheck size={12}/> Copied</> : <><IconCopy size={12}/> Copy link</>}
        </button>
      </div>

      {/* Copy as Markdown */}
      <div className="sh-section">
        <div className="sh-section-label">Copy as Markdown</div>
        <div className="sh-item" onClick={() => copy(t.summary_md || "", "summary")}>
          <div className="sh-glyph"><IconSparkle size={14}/></div>
          <div className="sh-text">
            <div className="sh-title">Summary</div>
            <div className="sh-sub">~{t.summary_md ? Math.round(t.summary_md.length / 4) : 0} tokens · paste into Obsidian, notes…</div>
          </div>
          {statusFor("summary", "copied to clipboard")
            ?? <span className="sh-keys"><span className="kbd">⌘</span><span className="kbd">C</span></span>}
        </div>
        <div className="sh-item" onClick={() => copy(t.transcript_excerpt || "", "transcript")}>
          <div className="sh-glyph"><IconWave size={14}/></div>
          <div className="sh-text">
            <div className="sh-title">Transcript</div>
            <div className="sh-sub">{fmtDuration(t.duration_seconds)} · timestamps · {t.lang || "—"}</div>
          </div>
          {statusFor("transcript", "copied to clipboard")
            ?? <span className="sh-keys"><span className="kbd">⇧</span><span className="kbd">⌘</span><span className="kbd">C</span></span>}
        </div>
      </div>

      {/* Download */}
      <div className="sh-section">
        <div className="sh-section-label">Download</div>
        <div className="sh-item" onClick={() => download(`scribe-${t.id}-summary.md`, "summary")}>
          <div className="sh-glyph"><IconDownload size={13}/></div>
          <div className="sh-text">
            <div className="sh-title">summary.md</div>
            <div className="sh-sub">/transcripts/{t.id}/summary.md</div>
          </div>
          {statusFor("dl:summary", "downloaded")}
        </div>
        <div className="sh-item" onClick={() => download(`scribe-${t.id}-transcript.md`, "transcript")}>
          <div className="sh-glyph"><IconDownload size={13}/></div>
          <div className="sh-text">
            <div className="sh-title">transcript.md</div>
            <div className="sh-sub">/transcripts/{t.id}/transcript.md</div>
          </div>
          {statusFor("dl:transcript", "downloaded")}
        </div>
      </div>

      {/* Send to integration */}
      <div className="sh-section">
        <div className="sh-section-label">Send to</div>
        <div className="sh-item" onClick={() => copy("sent:telegram", "tg")}>
          <div className="sh-glyph" style={{color: "var(--info)"}}><IconExternal size={13}/></div>
          <div className="sh-text">
            <div className="sh-title">Telegram</div>
            <div className="sh-sub">@oleg · summary as message + .md attachment</div>
          </div>
          {statusFor("tg", "sent")
            ?? <span className="sh-keys"><IconArrow size={11}/></span>}
        </div>
        <div className="sh-item" onClick={() => copy("sent:obsidian", "ob")}>
          <div className="sh-glyph" style={{color: "#7c3aed"}}><IconExternal size={13}/></div>
          <div className="sh-text">
            <div className="sh-title">Obsidian vault</div>
            <div className="sh-sub">scribe/ · creates summary + transcript + frontmatter</div>
          </div>
          {statusFor("ob", "sent")
            ?? <span className="sh-keys"><IconArrow size={11}/></span>}
        </div>
        <div className="sh-item" onClick={() => copy(`${fullUrl}/feed.xml`, "rss")}>
          <div className="sh-glyph"><IconRSS size={13}/></div>
          <div className="sh-text">
            <div className="sh-title">RSS</div>
            <div className="sh-sub">Subscribe to all summaries · feed.xml</div>
          </div>
          {statusFor("rss", "feed URL copied")
            ?? <span className="sh-keys"><IconCopy size={11}/></span>}
        </div>
      </div>
    </div>
  );
}

function PartialNotice({ transcript, onRegen, regenerating }) {
  return (
    <div style={{
      padding: "16px 18px",
      border: "1px solid color-mix(in oklab, var(--warn) 32%, transparent)",
      borderRadius: "var(--radius-lg)",
      background: "color-mix(in oklab, var(--warn) 10%, var(--bg))",
      color: "var(--warn)",
      marginTop: 12,
      display: "flex", gap: 14, alignItems: "flex-start",
    }}>
      <IconAlert size={18}/>
      <div style={{flex: 1, color: "var(--fg)"}}>
        <div style={{fontWeight: 600, marginBottom: 4}}>Partial transcript — summary failed</div>
        <div style={{color: "var(--fg-soft)", fontSize: 13.5, lineHeight: 1.5}}>
          Whisper transcribed this video successfully but the codex summarizer
          timed out. The transcript is preserved; rerunning will only re-summarize
          (no Vast.ai cost).
        </div>
      </div>
      <button className="btn primary" onClick={onRegen} disabled={regenerating}>
        {regenerating ? <span className="spinner"/> : <IconRefresh size={14}/>}
        {regenerating ? "Summarizing…" : "Run summarizer"}
      </button>
    </div>
  );
}

// Tiny markdown renderer — handles headings, bold, italic, code, lists, blockquotes.
// Sufficient for our seed content; not a full CommonMark parser.
function Markdown({ src }) {
  const parts = React.useMemo(() => parseMd(src), [src]);
  return <>{parts}</>;
}
function parseMd(md) {
  const lines = md.split("\n");
  const out = [];
  let i = 0; let key = 0;
  while (i < lines.length) {
    const l = lines[i];
    if (/^##\s+/.test(l)) {
      out.push(<h2 key={key++}>{inline(l.replace(/^##\s+/, ""))}</h2>);
      i++; continue;
    }
    if (/^#\s+/.test(l)) {
      out.push(<h2 key={key++}>{inline(l.replace(/^#\s+/, ""))}</h2>);
      i++; continue;
    }
    if (/^>/.test(l)) {
      const buf = [];
      while (i < lines.length && /^>/.test(lines[i])) {
        buf.push(lines[i].replace(/^>\s?/, "")); i++;
      }
      out.push(<blockquote key={key++}>{inline(buf.join(" "))}</blockquote>);
      continue;
    }
    if (/^\s*[-*]\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*]\s+/, "")); i++;
      }
      out.push(<ul key={key++}>{items.map((it, j) => <li key={j}>{inline(it)}</li>)}</ul>);
      continue;
    }
    if (/^\s*\d+\.\s+/.test(l)) {
      const items = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+\.\s+/, "")); i++;
      }
      out.push(<ol key={key++}>{items.map((it, j) => <li key={j}>{inline(it)}</li>)}</ol>);
      continue;
    }
    if (l.trim() === "") { i++; continue; }
    // paragraph: collect until blank line
    const buf = [l];
    i++;
    while (i < lines.length && lines[i].trim() !== "" && !/^(#|>|\s*[-*]\s|\s*\d+\.\s)/.test(lines[i])) {
      buf.push(lines[i]); i++;
    }
    out.push(<p key={key++}>{inline(buf.join(" "))}</p>);
  }
  return out;
}
function inline(s) {
  // **bold** *italic* `code`
  const out = []; let i = 0; let buf = ""; let k = 0;
  const flush = () => { if (buf) { out.push(buf); buf = ""; } };
  while (i < s.length) {
    if (s.startsWith("**", i)) {
      const end = s.indexOf("**", i + 2);
      if (end !== -1) { flush(); out.push(<strong key={k++}>{s.slice(i+2, end)}</strong>); i = end + 2; continue; }
    }
    if (s[i] === "*" && s[i+1] !== " ") {
      const end = s.indexOf("*", i + 1);
      if (end !== -1) { flush(); out.push(<em key={k++}>{s.slice(i+1, end)}</em>); i = end + 1; continue; }
    }
    if (s[i] === "`") {
      const end = s.indexOf("`", i + 1);
      if (end !== -1) { flush(); out.push(<code key={k++}>{s.slice(i+1, end)}</code>); i = end + 1; continue; }
    }
    buf += s[i]; i++;
  }
  flush();
  return out;
}


function DetailState({ title, body, navigate }) {
  return (
    <div className="pane pane-narrow">
      {navigate && <a onClick={() => navigate("library")} style={{display: "inline-flex", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--muted)", cursor: "pointer", marginBottom: 18}}>← Library</a>}
      <div className="empty"><div className="empty-title">{title}</div><div>{body}</div></div>
    </div>
  );
}
