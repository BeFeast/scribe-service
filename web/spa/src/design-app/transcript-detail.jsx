// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { useAuth } from "../hooks/useAuth";
import { useIsMobile } from "../hooks/useIsMobile";
import { IconAlert, IconArrow, IconCheck, IconClock, IconCopy, IconDownload, IconExternal, IconLink, IconRSS, IconRefresh, IconSparkle, IconWave, IconX } from "./icons.jsx";
import { CURRENT_TRANSCRIPT, CURRENT_TRANSCRIPT_STATE, TRANSCRIPTS, fmtDuration, fmtRelative, fmtUsd, publicBaseUrl } from "./data.js";
import {
	IconDocIOS,
	IconPlayIOS,
	IconShareIOS,
	IconWarnIOS,
} from "./mobile/icons-ios.jsx";
import { ShareSheet as MobileShareSheet, shareUrlsFor } from "./mobile/ShareSheet.jsx";
// Transcript detail — title, meta, summary (rendered from MD), transcript body.

const LANG_LABELS = {
  ar: "Arabic",
  de: "German",
  en: "English",
  es: "Spanish",
  fr: "French",
  he: "Hebrew",
  it: "Italian",
  ja: "Japanese",
  ko: "Korean",
  pl: "Polish",
  pt: "Portuguese",
  ru: "Russian",
  tr: "Turkish",
  uk: "Ukrainian",
  zh: "Chinese",
};

export function TranscriptDetail({ id, navigate, onRefresh }) {
  const auth = useAuth();
  const isMobile = useIsMobile();
  const t = CURRENT_TRANSCRIPT || TRANSCRIPTS.find((r) => r.id === id);
  const [regenerating, setRegenerating] = React.useState(false);
  const [copied, setCopied] = React.useState(null);
  const [shareOpen, setShareOpen] = React.useState(false);
  const [deleteConfirm, setDeleteConfirm] = React.useState(false);
  const [actionError, setActionError] = React.useState(null);

  if (CURRENT_TRANSCRIPT_STATE.loading) return <DetailState title="Loading transcript" body="Fetching /transcripts/{id}."/>;
  if (!t || CURRENT_TRANSCRIPT_STATE.error) return <DetailState title="Transcript unavailable" body={CURRENT_TRANSCRIPT_STATE.error || "No transcript is loaded."} navigate={navigate}/>;

  if (isMobile) {
    return (
      <MobileTranscriptDetail
        t={t}
        shareOpen={shareOpen}
        setShareOpen={setShareOpen}
        onRegen={async () => {
          setRegenerating(true);
          setActionError(null);
          try {
            const response = await auth.protectedFetch("/transcripts/" + t.id + "/resummarize", { method: "POST" });
            if (!response.ok) throw new Error("HTTP " + response.status);
            onRefresh && onRefresh();
          } catch (error) {
            setActionError(error instanceof Error ? error.message : String(error));
          } finally {
            setRegenerating(false);
          }
        }}
        regenerating={regenerating}
      />
    );
  }

  function copy(text, key) {
    try {
      navigator.clipboard && navigator.clipboard.writeText(text);
      setCopied(key); setTimeout(() => setCopied(null), 1600);
    } catch (e) {
      setCopied("err:" + key); setTimeout(() => setCopied(null), 2400);
    }
  }
  async function copyFromEndpoint(path, key, fallback) {
    setActionError(null);
    try {
      const response = await auth.protectedFetch(path, { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      copy(await response.text(), key);
    } catch (error) {
      if (fallback != null) copy(fallback, key);
      else {
        setCopied("err:" + key);
        setActionError(error instanceof Error ? error.message : String(error));
        setTimeout(() => setCopied(null), 2400);
      }
    }
  }
  async function download(kind) {
    setActionError(null);
    const filename = `scribe-${t.id}-${kind}.md`;
    const path = "/transcripts/" + t.id + "/" + kind + ".md";
    try {
      const response = await auth.protectedFetch(path, { cache: "no-store" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      setCopied("dl:" + kind); setTimeout(() => setCopied(null), 1600);
    } catch (error) {
      setCopied("err:dl:" + kind);
      setActionError(error instanceof Error ? error.message : String(error));
      setTimeout(() => setCopied(null), 2400);
    }
  }
  async function regen() {
    setRegenerating(true);
    setActionError(null);
    try {
      const response = await auth.protectedFetch("/transcripts/" + t.id + "/resummarize", { method: "POST" });
      if (!response.ok) throw new Error("HTTP " + response.status);
      onRefresh && onRefresh();
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    } finally {
      setRegenerating(false);
    }
  }
  async function deleteTranscript() {
    setActionError(null);
    try {
      const response = await auth.protectedFetch("/admin/transcripts/" + t.id, { method: "DELETE" });
      if (response.ok) {
        onRefresh && onRefresh();
        navigate("library");
      } else {
        setActionError("HTTP " + response.status);
      }
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
    }
  }

  return (
    <div className="pane pane-narrow">
      <div className="row" style={{marginBottom: 18}}>
        <a onClick={() => navigate("library")}
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
                        copy={copy} copyFromEndpoint={copyFromEndpoint}
                        download={download} auth={auth} copied={copied}
                        setCopied={setCopied} setActionError={setActionError}/>
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
              <button className="btn ghost" onClick={() => copyFromEndpoint("/transcripts/" + t.id + "/summary.md", "all", t.summary_md)}
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
          <SummaryBody src={t.summary_md} dimmed={regenerating} navigate={navigate}/>
        </>
      )}

      <div className="section-label">
        <span>Transcript</span>
        <div className="row" style={{gap: 6}}>
          <span className="mono muted" style={{fontSize: 11}}>
            ~{Math.round((t.duration_seconds || 1) / 60)} min · {t.lang}
          </span>
          <button className="btn ghost" onClick={() => download("transcript")} style={{fontSize: 12, padding: "4px 8px"}}>
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
      {actionError && <div className="empty" style={{marginTop: 16}}><div className="empty-title">Action failed</div><div>{actionError}</div></div>}

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
            <button className="btn danger" onClick={() => void deleteTranscript()}
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
function ShareSheet({ t, onClose, copy, copyFromEndpoint, download, auth, copied, setCopied, setActionError }) {
  const ref = React.useRef(null);
  const [visibility, setVisibility] = React.useState("public");
  const [shareUrl, setShareUrl] = React.useState(null);
  const baseUrl = normalizedPublicBaseUrl();
  const fullUrl = `${baseUrl}/transcripts/${t.id}`;
  const fallbackShareUrl = `${baseUrl}/share/...`;
  const displayShareUrl = splitShareUrl(shareUrl || fallbackShareUrl);

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

  async function copyShareLink() {
    try {
      const response = await auth.protectedFetch("/api/transcripts/" + t.id + "/share-links", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_kind: "page", label: visibility }),
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      const body = await response.json();
      const url = body.share_url || (body.token ? `${baseUrl}/share/${body.token}` : fallbackShareUrl);
      setShareUrl(url);
      copy(url, "sl");
    } catch (error) {
      setActionError(error instanceof Error ? error.message : String(error));
      setCopied("err:sl"); setTimeout(() => setCopied(null), 2400);
    }
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
        <span className="scheme">{displayShareUrl.scheme}</span>
        <span className="path">{displayShareUrl.path}</span>
        <button className="btn primary"
                onClick={() => void copyShareLink()}
                style={{fontSize: 12, padding: "5px 10px"}}>
          {isCopied("sl") ? <><IconCheck size={12}/> Copied</> : <><IconCopy size={12}/> Copy link</>}
        </button>
      </div>

      {/* Copy as Markdown */}
      <div className="sh-section">
        <div className="sh-section-label">Copy as Markdown</div>
        <div className="sh-item" onClick={() => copyFromEndpoint("/transcripts/" + t.id + "/summary.md", "summary", t.summary_md || "")}>
          <div className="sh-glyph"><IconSparkle size={14}/></div>
          <div className="sh-text">
            <div className="sh-title">Summary</div>
            <div className="sh-sub">~{t.summary_md ? Math.round(t.summary_md.length / 4) : 0} tokens · paste into Obsidian, notes…</div>
          </div>
          {statusFor("summary", "copied to clipboard")
            ?? <span className="sh-keys"><span className="kbd">⌘</span><span className="kbd">C</span></span>}
        </div>
        <div className="sh-item" onClick={() => copyFromEndpoint("/transcripts/" + t.id + "/transcript.md", "transcript", t.transcript_excerpt || "")}>
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
        <div className="sh-item" onClick={() => download("summary")}>
          <div className="sh-glyph"><IconDownload size={13}/></div>
          <div className="sh-text">
            <div className="sh-title">summary.md</div>
            <div className="sh-sub">/transcripts/{t.id}/summary.md</div>
          </div>
          {statusFor("dl:summary", "downloaded")}
        </div>
        <div className="sh-item" onClick={() => download("transcript")}>
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

function normalizedPublicBaseUrl() {
  return publicBaseUrl().replace(/\/+$/, "");
}

function splitShareUrl(shareUrl) {
  try {
    const url = new URL(shareUrl);
    const pathname = url.pathname === "/" ? "" : url.pathname.replace(/^\//, "");
    return {
      scheme: url.origin + "/",
      path: pathname || url.hostname,
    };
  } catch {
    const parts = shareUrl.split("/");
    return {
      scheme: parts.length > 1 ? parts.slice(0, -1).join("/") + "/" : "",
      path: parts.at(-1) || shareUrl,
    };
  }
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
          Whisper transcribed this video successfully, but the summarizer
          failed to produce a summary. The transcript is preserved; rerunning
          re-summarizes only — no Vast.ai cost.
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
function SummaryBody({ src, dimmed, navigate }) {
  const summary = React.useMemo(() => splitFrontmatter(src), [src]);

  return (
    <div className={dimmed ? "body-dimmed" : undefined}>
      {summary.props && <PropertiesPanel props={summary.props} navigate={navigate}/>}
      <div className="prose"><Markdown src={summary.body}/></div>
    </div>
  );
}

function splitFrontmatter(src) {
  if (!src || !src.startsWith("---")) return { props: null, body: src || "" };
  const end = src.indexOf("\n---", 3);
  if (end === -1) return { props: null, body: src };
  const yaml = src.slice(3, end).replace(/^\n+/, "");
  const body = src.slice(end + 4).replace(/^\n+/, "");
  const props = parseFrontmatter(yaml);
  return { props: props.length ? props : null, body };
}

function parseFrontmatter(yaml) {
  const props = [];
  const lines = yaml.replace(/\r\n/g, "\n").split("\n");
  let index = 0;
  while (index < lines.length) {
    const line = lines[index] || "";
    const match = /^([A-Za-z0-9_-]+)\s*:\s*(.*)$/.exec(line);
    if (!match) {
      index += 1;
      continue;
    }
    const key = match[1];
    let raw = match[2].trim();
    if (raw.startsWith('"') && !closedDoubleQuote(raw)) {
      index += 1;
      while (index < lines.length && !closedDoubleQuote(raw)) {
        raw = `${raw} ${(lines[index] || "").trim()}`;
        index += 1;
      }
    } else {
      index += 1;
    }
    props.push({ key, raw, value: parseFrontmatterValue(raw) });
  }
  return props;
}

function closedDoubleQuote(value) {
  return !value.startsWith('"') || (value.length > 1 && value.endsWith('"'));
}

function parseFrontmatterValue(raw) {
  if (!raw) return "";
  const unquoted = unwrapYamlString(raw);
  const wiki = /^\[\[([^\]]+)\]\]$/.exec(unquoted);
  if (wiki) return { kind: "wikilink", target: wiki[1] };
  if (raw.startsWith("[") && raw.endsWith("]")) {
    return {
      kind: "list",
      items: raw.slice(1, -1).split(",").map((item) => unwrapYamlString(item.trim())).filter(Boolean),
    };
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(unquoted)) return { kind: "date", value: unquoted };
  return unquoted;
}

function unwrapYamlString(value) {
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  return value;
}

function PropertiesPanel({ props, navigate }) {
  // Closed by default — operators usually skim summary first; expand for metadata.
  const [open, setOpen] = React.useState(false);
  const [copied, setCopied] = React.useState(null);

  function copyValue(prop) {
    try {
      navigator.clipboard && navigator.clipboard.writeText(scalarToCopy(prop.value));
      setCopied(prop.key);
      setTimeout(() => setCopied(null), 1400);
    } catch {
      setCopied("err:" + prop.key);
      setTimeout(() => setCopied(null), 1800);
    }
  }

  return (
    <section className={open ? "fm-panel" : "fm-panel collapsed"} aria-label="Summary properties">
      <button className="fm-header" type="button" onClick={() => setOpen((value) => !value)} aria-expanded={open}>
        <span className="fm-caret">{open ? "▾" : "▸"}</span>
        <span>Properties</span>
        <span className="fm-count">{props.length}</span>
        <span className="spacer"/>
        <span className="fm-source">frontmatter</span>
      </button>
      {open && (
        <div className="fm-rows">
          {props.map((prop) => (
            <div className="fm-row" key={prop.key}>
              <div className="fm-name">
                <PropertyIcon prop={prop}/>
                <span>{formatPropName(prop.key)}</span>
              </div>
              <div className="fm-value">
                <PropertyValue prop={prop} navigate={navigate}/>
              </div>
              <button className="fm-copy" type="button" onClick={() => copyValue(prop)} title={`Copy ${formatPropName(prop.key)}`}>
                {copied === prop.key ? "copied" : <IconCopy size={12}/>}
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

function PropertyValue({ prop, navigate }) {
  const key = prop.key.toLowerCase();
  const value = prop.value;
  if ((key === "type" || key === "category") && typeof value === "string") {
    return <span className="fm-pill accent">{value.toUpperCase()}</span>;
  }
  if ((key === "language" || key === "lang") && typeof value === "string") {
    const code = value.toLowerCase();
    return <><span className="fm-pill">{code.toUpperCase()}</span><span className="fm-lang-label">{LANG_LABELS[code] || value}</span></>;
  }
  if ((key === "short_description" || key === "description" || key === "summary") && typeof value === "string") {
    return <span className="fm-description">{value}</span>;
  }
  if (value && typeof value === "object" && value.kind === "date") {
    return <><span className="tnum">{value.value}</span><span className="fm-date-human">· {formatHumanDate(value.value)}</span></>;
  }
  if (value && typeof value === "object" && value.kind === "wikilink") {
    return (
      <button className="fm-link" type="button" onClick={() => navigate("library", { tag: value.target })}>
        [[{value.target}]]
      </button>
    );
  }
  if (value && typeof value === "object" && value.kind === "list") {
    return (
      <span className="fm-tags">
        {value.items.map((item) => (
          <button className="fm-tag" type="button" key={item} onClick={() => navigate("library", { tag: stripHash(item) })}>
            #{stripHash(item)}
          </button>
        ))}
      </span>
    );
  }
  if ((key === "source" || key === "url" || key === "link") && typeof value === "string") {
    return <button className="fm-link" type="button" onClick={() => navigate("library", { tag: stripWiki(value) })}>{value}</button>;
  }
  return <span>{String(value)}</span>;
}

function PropertyIcon({ prop }) {
  const key = prop.key.toLowerCase();
  if (key === "type" || key === "category") return <IconSparkle size={13}/>;
  if (key === "date" || key === "created" || key === "updated") return <IconClock size={13}/>;
  if (key === "source" || key === "url" || key === "link") return <IconLink size={13}/>;
  if (key === "tags" || key === "labels") return <span aria-hidden="true">#</span>;
  if (key === "language" || key === "lang") return <span aria-hidden="true">A</span>;
  return <span aria-hidden="true">≡</span>;
}

function formatPropName(key) {
  return key.replaceAll("_", " ");
}

function formatHumanDate(value) {
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

function scalarToCopy(value) {
  if (value && typeof value === "object" && value.kind === "list") return value.items.join(", ");
  if (value && typeof value === "object" && value.kind === "wikilink") return `[[${value.target}]]`;
  if (value && typeof value === "object" && value.kind === "date") return value.value;
  return String(value);
}

function stripHash(value) {
  return String(value).replace(/^#/, "");
}

function stripWiki(value) {
  const match = /^\[\[([^\]]+)\]\]$/.exec(String(value));
  return match ? match[1] : String(value);
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


// ─── Mobile transcript detail ────────────────────────────────────────────
//
// Literal port of `viewTranscript(id)` from `Scribe iOS.html` (mobile design
// source, SHA-256
// 421c930d9f2d5c1549dc632760f796992630a27eaa6fa38f28ff25584bc3ebb9) at
// ~966-1006. Source mapping:
//   ~971 const head            → <header className="detail-head"> below
//   ~983 const segCtl          → <div className="seg" id="t-seg">
//   ~987 const partialBanner   → <div className="banner">
//   ~988 summaryHtml           → <div className="prose"> (driven by SummaryBody)
//   ~989 transcriptHtml        → <div className="transcript-body">
//   ~990 bodyFor(s)            → React conditional on seg state
//   ~994 navRight share btn    → inline `.nb-btn.icon` above the detail-head
//                                (the mobile shell's navbar right-slot is
//                                shared chrome; keeping the share affordance
//                                in-page avoids lifting share-open state into
//                                the shell)
//   ~999 segEl.onclick         → React onClick on each seg button
//
// Real-data wiring: `t` is `adaptTranscript(runtime.currentTranscript)` from
// `data.js` — same adapter the desktop branch uses. The seg control reads
// `t.summary_md` and `t.transcript_excerpt` directly. No prototype seed text.
function MobileTranscriptDetail({ t, shareOpen, setShareOpen, onRegen, regenerating }) {
	const initialSeg = t.summary_md ? "summary" : "transcript";
	const [seg, setSeg] = React.useState(initialSeg);
	// Reset seg when navigating to a different transcript.
	React.useEffect(() => {
		setSeg(t.summary_md ? "summary" : "transcript");
	}, [t.id, t.summary_md]);

	const langLabel = (t.lang || "").toUpperCase() || "—";

	return (
		<>
			<div className="m-transcript-actions">
				<button
					type="button"
					className="nb-btn icon"
					data-act="share"
					aria-label="Share"
					onClick={() => setShareOpen(true)}
				>
					<IconShareIOS size={20} />
				</button>
			</div>

			<div className="detail-head">
				<h1 className="detail-title detail-h1">{t.title}</h1>
				<div className="detail-meta">
					<span>#{t.id}</span>
					<span className="sep">·</span>
					<span>{fmtDuration(t.duration_seconds)}</span>
					<span className="sep">·</span>
					<span>{langLabel}</span>
					<span className="sep">·</span>
					<span>{fmtUsd(t.vast_cost)}</span>
					<span className="sep">·</span>
					<span>{fmtRelative(t.created_at)}</span>
				</div>
				{t.tags && t.tags.length > 0 ? (
					<div className="detail-tags">
						{t.tags.map((tg) => (
							<span key={tg} className="tag">
								{tg}
							</span>
						))}
					</div>
				) : null}
			</div>

			{t.summary_md ? (
				<div className="seg" id="t-seg" style={{ marginTop: 6 }}>
					<button
						type="button"
						data-v="summary"
						className={seg === "summary" ? "active" : undefined}
						onClick={() => setSeg("summary")}
					>
						<IconDocIOS size={16} /> Summary
					</button>
					<button
						type="button"
						data-v="transcript"
						className={seg === "transcript" ? "active" : undefined}
						onClick={() => setSeg("transcript")}
					>
						<IconPlayIOS size={15} /> Transcript
					</button>
				</div>
			) : null}

			{t.is_partial ? (
				<div className="banner">
					<span className="b-ic">
						<IconWarnIOS size={18} />
					</span>
					<div>
						<b>Summary unavailable.</b>{" "}
						The summarizer did not complete. The transcript is saved — you can re-run summarization from Ops.
						{onRegen ? (
							<>
								{" "}
								<button
									type="button"
									className="banner-action"
									onClick={onRegen}
									disabled={regenerating}
								>
									{regenerating ? "Summarizing…" : "Run summarizer"}
								</button>
							</>
						) : null}
					</div>
				</div>
			) : null}

			<div id="t-content">
				{seg === "summary" && t.summary_md ? (
					<div className="prose">
						<Markdown src={stripSummaryFrontmatter(t.summary_md)} />
					</div>
				) : (
					<div className="transcript-body">
						{t.transcript_excerpt || ""}
					</div>
				)}
			</div>

			<MobileShareSheet
				t={t}
				open={shareOpen}
				onClose={() => setShareOpen(false)}
				onAction={(action) => handleMobileShareAction(action, t)}
			/>
		</>
	);
}

// stripSummaryFrontmatter is a thin wrapper around the existing
// `splitFrontmatter()` helper so the mobile branch renders only the
// markdown body (the YAML frontmatter shows up as a closed-by-default
// Properties panel on desktop; the mobile design has no such panel).
function stripSummaryFrontmatter(src) {
	return splitFrontmatter(src).body;
}

// Wave 2a commit N — placeholder. Replaced in commit N+1 with real
// `navigator.share` / `navigator.clipboard.writeText(summary_md)` /
// RSS copy. No-op here so the literal-port commit stays free of fake
// share toasts (banned by issue #277).
function handleMobileShareAction(_action, _t) {
	// intentionally empty in commit N
}

function DetailState({ title, body, navigate }) {
  return (
    <div className="pane pane-narrow">
      {navigate && <a onClick={() => navigate("library")} style={{display: "inline-flex", fontFamily: "var(--font-mono)", fontSize: 12, color: "var(--muted)", cursor: "pointer", marginBottom: 18}}>← Library</a>}
      <div className="empty"><div className="empty-title">{title}</div><div>{body}</div></div>
    </div>
  );
}
