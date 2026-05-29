// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { IconClock, IconLibrary, IconMoon, IconOps, IconQueue, IconRSS, IconSearch, IconSettings, IconSun } from "./icons.jsx";
import { ACTIVE_JOBS, LIBRARY_TOTAL, RECENT_FAILURES, STATS, TRANSCRIPTS, countFailuresInLastDay, fmtUsd, tagCounts } from "./data.js";
// Shell: top bar + sidebar nav. Calls navigate(page, params?) from props.

export function TopBar({ onOpenCmdk, t, setTweak }) {
  const isDark = t.theme === "dark";
  return (
    <div className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="14" height="14" fill="none"
               stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <line x1="4"  y1="9"  x2="4"  y2="15"/>
            <line x1="7.5"y1="6"  x2="7.5"y2="18"/>
            <line x1="11" y1="8.5"x2="11" y2="15.5"/>
            <line x1="14" y1="8"  x2="20" y2="8"/>
            <line x1="14" y1="12" x2="20" y2="12"/>
            <line x1="14" y1="16" x2="18" y2="16"/>
          </svg>
        </span>
        <span>scribe</span>
      </div>
      <div className="grow" />
      <div className="cmdk" onClick={onOpenCmdk} role="button" tabIndex={0}
           onKeyDown={(e) => { if (e.key === "Enter") onOpenCmdk(); }}>
        <IconSearch size={14} />
        <span>Paste URL or search transcripts…</span>
        <span className="kbd">⌘K</span>
      </div>
      <button className="iconbtn" title="Toggle theme"
              onClick={() => setTweak("theme", isDark ? "light" : "dark")}>
        {isDark ? <IconSun size={16}/> : <IconMoon size={16}/>}
      </button>
      <a className="iconbtn" href="#" title="RSS feed">
        <IconRSS size={16}/>
      </a>
    </div>
  );
}

export function Sidebar({ page, navigate }) {
  const tags = tagCounts().slice(0, 8);
  const transcriptCount = Math.max(LIBRARY_TOTAL, TRANSCRIPTS.length);
  const queueCount = ACTIVE_JOBS.length;
  const failuresToday = countFailuresInLastDay(RECENT_FAILURES);
  return (
    <aside className="sidebar">
      <div className="nav-section">Browse</div>
      <a className={"nav-item " + (page === "library" ? "active" : "")}
         onClick={() => navigate("library")}>
        <IconLibrary size={15}/> <span>Library</span>
        <span className="count">{transcriptCount}</span>
      </a>
      <a className={"nav-item " + (page === "queue" ? "active" : "")}
         onClick={() => navigate("queue")}>
        <IconQueue size={15}/> <span>Queue</span>
        {queueCount > 0 && <span className="count" style={{color:"var(--accent)"}}>
          <span className="live-dot" style={{marginRight: 5}}/> {queueCount}
        </span>}
      </a>
      <a className={"nav-item " + (page === "history" ? "active" : "")}
         onClick={() => navigate("history")}>
        <IconClock size={15}/> <span>History</span>
      </a>
      <a className={"nav-item " + (page === "ops" ? "active" : "")}
         onClick={() => navigate("ops")}>
        <IconOps size={15}/> <span>Ops</span>
        {failuresToday > 0 && <span className="count" style={{color: "var(--err)"}}>{failuresToday}!</span>}
      </a>
      <a className={"nav-item " + (page === "settings" ? "active" : "")}
         onClick={() => navigate("settings")}>
        <IconSettings size={15}/> <span>Settings</span>
      </a>

      <div className="nav-section">Tags</div>
      <div className="tag-list">
        {tags.map(([tag, n]) => (
          <span key={tag} className="tag" title={`${n} transcripts`}
                onClick={() => navigate("library", { tag })}>
            {tag}<span className="n">{n}</span>
          </span>
        ))}
      </div>

      <div className="nav-section">Pipeline</div>
      <div style={{padding: "0 10px", fontFamily: "var(--font-mono)", fontSize: 11, color: "var(--muted)", lineHeight: 1.7}}>
        <div className="row" style={{justifyContent:"space-between"}}>
          <span>workers</span>
          <span className="tnum">{STATS.worker_pool.active}/{STATS.worker_pool.total}</span>
        </div>
        <div className="row" style={{justifyContent:"space-between"}}>
          <span>vast 24h</span>
          <span className="tnum">{fmtUsd(STATS.vast_spend_24h)}</span>
        </div>
        <div className="row" style={{justifyContent:"space-between"}}>
          <span>cap</span>
          <span className="tnum">{fmtUsd(STATS.daily_spend_cap_usd)}</span>
        </div>
      </div>
    </aside>
  );
}

