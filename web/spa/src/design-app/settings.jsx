// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { IconCards, IconCopy, IconDot, IconExternal, IconFeed, IconMoon, IconPlus, IconRefresh, IconSparkle, IconSun, IconTable, IconX } from "./icons.jsx";
import { SCRIBE_USERS, STATS, fmtRelative, fmtUsd } from "./data.js";
// Settings page — config values that map to scribe/config.py settings.

const DEFAULT_PROMPT = `You are a careful, ruthless reader. Given a transcript of a YouTube video, produce a Markdown summary with the following sections:

## TL;DR
2-4 sentences — the core argument or finding. No throat-clearing.

## Key moves / The N points
A numbered list of the substantive claims. Use the speaker's framing where they actually used numbered points; otherwise infer them.

## Notable callouts
Memorable lines, references, surprising specifics. One bullet each.

## What I took away
A short personal-knowledge takeaway — what changes about how I work tomorrow.

Style:
- No filler ("the speaker discusses…", "this video covers…"). Just say what they said.
- Preserve concrete numbers, names, and exact phrasings.
- If you don't know something, omit it. Do not hedge.
- Markdown only. No HTML.`;

export function SettingsPage({ t, setTweak }) {
  const [prompt, setPrompt] = React.useState(DEFAULT_PROMPT);
  const [cap, setCap] = React.useState(STATS.daily_spend_cap_usd);
  const capUsagePct = cap > 0 ? Math.min(100, (STATS.vast_spend_24h / cap) * 100) : 0;
  const [webhook, setWebhook] = React.useState("https://telegram.oklabs.uk/webhook/scribe");
  const [publicBase, setPublicBase] = React.useState("https://scribe.oklabs.uk");
  const [keepBotwallRetries, setKeepBotwallRetries] = React.useState(true);
  const [embedTranscript, setEmbedTranscript] = React.useState(true);

  return (
    <div className="pane" style={{maxWidth: 960}}>
      <div className="pane-header">
        <div>
          <h1 className="pane-h1">Settings</h1>
          <div className="pane-sub">Reads <code>.env</code> · changes write back via <code>POST /admin/config</code></div>
        </div>
        <div className="pane-actions">
          <button className="btn ghost">Discard</button>
          <button className="btn primary">Save changes</button>
        </div>
      </div>

      <div className="settings-group">
        <h2>Pipeline</h2>
        <p className="group-sub">How scribe acquires audio, transcribes, and writes back.</p>

        <div className="settings-row">
          <div className="row-label">
            Daily Vast.ai spend cap
            <span className="hint">Rolling 24h. New <code>POST /jobs</code> returns 429 above the cap; retries and resummarize bypass it.</span>
          </div>
          <div className="row-control">
            <div className="row" style={{gap: 8}}>
              <input type="number" step="0.25" min="0" value={cap}
                     onChange={(e) => setCap(parseFloat(e.target.value) || 0)}
                     style={{width: 100}}/>
              <span className="muted mono" style={{fontSize: 12}}>USD</span>
              <span className="muted" style={{fontSize: 12, marginLeft: 16}}>
                Current 24h spend: <span className="tnum" style={{color: "var(--fg-soft)"}}>{fmtUsd(STATS.vast_spend_24h)}</span>
                {" "}({capUsagePct.toFixed(0)}%)
              </span>
            </div>
            <div className="bar-track" style={{maxWidth: 260}}>
              <div style={{width: `${capUsagePct}%`}}/>
            </div>
          </div>
        </div>

        <div className="settings-row">
          <div className="row-label">
            Worker concurrency
            <span className="hint">Postgres queue uses <code>FOR UPDATE SKIP LOCKED</code> so workers don't fight for jobs.</span>
          </div>
          <div className="row-control">
            <div className="seg" style={{width: "fit-content"}}>
              {[1, 2, 4, 8].map(n => (
                <button key={n} className={n === 2 ? "active" : ""}>{n}</button>
              ))}
            </div>
            <span className="muted mono" style={{fontSize: 12}}>
              currently <span className="tnum" style={{color: "var(--fg-soft)"}}>{STATS.worker_pool.total}</span> workers
            </span>
          </div>
        </div>

        <div className="settings-row">
          <div className="row-label">
            yt-dlp bot-wall retry
            <span className="hint">When YouTube returns "sign in to confirm you're not a bot", scribe cycles client= android-vr → web → mweb → tv with backoff.</span>
          </div>
          <div className="row-control">
            <div className="row" style={{gap: 12, alignItems: "center"}}>
              <span className={"toggle " + (keepBotwallRetries ? "on" : "")}
                    onClick={() => setKeepBotwallRetries(v => !v)}/>
              <span className="muted" style={{fontSize: 12.5}}>
                {keepBotwallRetries ? "On — try 4 fallback clients before failing" : "Off — fail on first bot-wall"}
              </span>
            </div>
          </div>
        </div>

        <div className="settings-row">
          <div className="row-label">
            Public base URL
            <span className="hint">Used to mint Chhoto shortlinks. Path is <code>/transcripts/&#123;id&#125;</code>.</span>
          </div>
          <div className="row-control">
            <input type="url" value={publicBase}
                   onChange={(e) => setPublicBase(e.target.value)}/>
            <span className="muted mono" style={{fontSize: 11.5}}>
              shortlinks resolve to <code>go.oklabs.uk/&lt;slug&gt;</code> → <code>{publicBase}/transcripts/142</code>
            </span>
          </div>
        </div>

        <div className="settings-row">
          <div className="row-label">
            Webhook callback
            <span className="hint">scribe POSTs <code>JobView</code> JSON here on terminal status. Per-job <code>callback_url</code> overrides this.</span>
          </div>
          <div className="row-control">
            <input type="url" value={webhook}
                   onChange={(e) => setWebhook(e.target.value)}/>
            <div className="row" style={{gap: 12, alignItems: "center"}}>
              <span className={"toggle " + (embedTranscript ? "on" : "")}
                    onClick={() => setEmbedTranscript(v => !v)}/>
              <span className="muted" style={{fontSize: 12.5}}>
                Embed <code>transcript</code> object in webhook payload
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="settings-group">
        <h2>Summary prompt</h2>
        <p className="group-sub">
          Edited live · saved as <code>src/scribe/prompts/transcript-summary.md</code>.
          Changes apply to new jobs and to <code>POST /resummarize</code>.
        </p>

        <div className="settings-row" style={{gridTemplateColumns: "1fr"}}>
          <div className="row-control">
            <div className="row" style={{justifyContent: "space-between"}}>
              <div className="row" style={{gap: 12}}>
                <span className="chip">v3 · active</span>
                <span className="chip" style={{cursor: "pointer"}}>v2</span>
                <span className="chip" style={{cursor: "pointer"}}>v1</span>
              </div>
              <div className="row" style={{gap: 8}}>
                <button className="btn ghost" style={{fontSize: 12, padding: "4px 8px"}}>
                  <IconSparkle size={12}/> Dry-run
                </button>
                <span className="muted mono" style={{fontSize: 11.5}}>
                  {prompt.length} chars · ~{Math.round(prompt.length / 4)} tokens
                </span>
              </div>
            </div>
            <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)}
                      spellCheck={false}/>
          </div>
        </div>
      </div>

      <div className="settings-group">
        <h2>Appearance</h2>
        <p className="group-sub">
          Choose the production appearance. Field / light / compact / feed is the default; every exported variant remains available here.
        </p>

        <div className="settings-row">
          <div className="row-label">Theme</div>
          <div className="row-control">
            <div className="seg" style={{width: "fit-content"}}>
              <button className={t.theme === "light" ? "active" : ""}
                      aria-pressed={t.theme === "light"}
                      onClick={() => setTweak("theme", "light")}>
                <IconSun size={13}/> Light
              </button>
              <button className={t.theme === "dark" ? "active" : ""}
                      aria-pressed={t.theme === "dark"}
                      onClick={() => setTweak("theme", "dark")}>
                <IconMoon size={13}/> Dark
              </button>
            </div>
          </div>
        </div>

        <div className="settings-row">
          <div className="row-label">
            Visual variant
            <span className="hint">Three takes on the same product. They share data and layout — only the type system, palette, and chrome change.</span>
          </div>
          <div className="row-control">
            <div className="seg" style={{width: "fit-content"}}>
              <button className={t.variant === "paper" ? "active" : ""}
                      aria-pressed={t.variant === "paper"}
                      onClick={() => setTweak("variant", "paper")}>Paper</button>
              <button className={t.variant === "terminal" ? "active" : ""}
                      aria-pressed={t.variant === "terminal"}
                      onClick={() => setTweak("variant", "terminal")}>Terminal</button>
              <button className={t.variant === "console" ? "active" : ""}
                      aria-pressed={t.variant === "console"}
                      onClick={() => setTweak("variant", "console")}>Console</button>
              <button className={t.variant === "field" ? "active" : ""}
                      aria-pressed={t.variant === "field"}
                      onClick={() => setTweak("variant", "field")}>Field</button>
            </div>
          </div>
        </div>

        <div className="settings-row">
          <div className="row-label">Library default layout</div>
          <div className="row-control">
            <div className="seg" style={{width: "fit-content"}}>
              <button className={t.libraryLayout === "table" ? "active" : ""}
                      aria-pressed={t.libraryLayout === "table"}
                      onClick={() => setTweak("libraryLayout", "table")}>
                <IconTable size={13}/> Table
              </button>
              <button className={t.libraryLayout === "feed" ? "active" : ""}
                      aria-pressed={t.libraryLayout === "feed"}
                      onClick={() => setTweak("libraryLayout", "feed")}>
                <IconFeed size={13}/> Feed
              </button>
              <button className={t.libraryLayout === "cards" ? "active" : ""}
                      aria-pressed={t.libraryLayout === "cards"}
                      onClick={() => setTweak("libraryLayout", "cards")}>
                <IconCards size={13}/> Cards
              </button>
            </div>
          </div>
        </div>

        <div className="settings-row">
          <div className="row-label">Density</div>
          <div className="row-control">
            <div className="seg" style={{width: "fit-content"}}>
              {["compact","cozy","comfy"].map(d => (
                <button key={d} className={t.density === d ? "active" : ""}
                        aria-pressed={t.density === d}
                        onClick={() => setTweak("density", d)}>{d}</button>
              ))}
            </div>
          </div>
        </div>
      </div>

      <AccessGroup/>

      <div className="settings-group">
        <h2>API access</h2>
        <p className="group-sub">Used by the Telegram bot, Obsidian plugin, and curl-from-anywhere.</p>

        <div className="settings-row">
          <div className="row-label">
            Bearer token
            <span className="hint">Required for <code>POST /jobs</code>, <code>POST /resummarize</code>, and admin routes.</span>
          </div>
          <div className="row-control">
            <div className="row" style={{gap: 8}}>
              <input type="text" value="scribe_••••••••••••••••••••" readOnly style={{maxWidth: 320}}/>
              <button className="btn"><IconCopy size={14}/> Copy</button>
              <button className="btn">Rotate</button>
            </div>
            <span className="muted mono" style={{fontSize: 11.5}}>
              last used 4m ago · 142 calls today
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}


// ─── Access group (users / authorization) ───────────────────────────────────
function AccessGroup() {
  const [users, setUsers] = React.useState(SCRIBE_USERS);
  React.useEffect(() => setUsers(SCRIBE_USERS), [SCRIBE_USERS]);
  const [showAdd, setShowAdd] = React.useState(false);
  const [draft, setDraft] = React.useState({ email: "", name: "", role: "user" });
  const [refreshing, setRefreshing] = React.useState(false);
  const [openMenu, setOpenMenu] = React.useState(null);

  const me = users.find(u => u.is_me);
  const total = users.length;
  const active = users.filter(u => u.state === "active").length;
  const admins = users.filter(u => u.role === "admin" && u.state === "active").length;
  const linked = users.filter(u => u.source === "clerk").length;

  React.useEffect(() => {
    if (!openMenu) return;
    const close = () => setOpenMenu(null);
    window.addEventListener("click", close);
    return () => window.removeEventListener("click", close);
  }, [openMenu]);

  function refresh() {
    setRefreshing(true);
    setTimeout(() => setRefreshing(false), 600);
  }
  function addUser() {
    if (!draft.email) return;
    setUsers(us => [...us, {
      email: draft.email,
      name: draft.name || draft.email.split("@")[0],
      role: draft.role,
      state: "active",
      source: "manual",
      clerk_subject: null,
      last_seen: null,
      calls_24h: 0,
    }]);
    setDraft({ email: "", name: "", role: "user" });
    setShowAdd(false);
  }
  function toggleState(email) {
    setUsers(us => us.map(u => u.email === email
      ? { ...u, state: u.state === "active" ? "disabled" : "active" }
      : u));
    setOpenMenu(null);
  }
  function setRole(email, role) {
    setUsers(us => us.map(u => u.email === email ? { ...u, role } : u));
    setOpenMenu(null);
  }
  function unlink(email) {
    setUsers(us => us.map(u => u.email === email
      ? { ...u, clerk_subject: null, source: "manual" } : u));
    setOpenMenu(null);
  }
  function remove(email) {
    setUsers(us => us.filter(u => u.email !== email));
    setOpenMenu(null);
  }

  return (
    <div className="settings-group">
      <div className="row" style={{alignItems: "baseline", justifyContent: "space-between", marginBottom: 4}}>
        <h2>Access</h2>
        <div className="row" style={{gap: 8}}>
          <button className="btn ghost" onClick={refresh} disabled={refreshing}
                  style={{fontSize: 12, padding: "4px 8px"}}>
            {refreshing ? <span className="spinner"/> : <IconRefresh size={12}/>}
            {refreshing ? "Refreshing…" : "Refresh"}
          </button>
          <button className="btn primary" onClick={() => setShowAdd(s => !s)}
                  style={{fontSize: 12, padding: "5px 10px"}}>
            <IconPlus size={12}/> Add user
          </button>
        </div>
      </div>
      <p className="group-sub">
        Email allowlist enforced on every <code>POST /jobs</code>, web UI, and webhook.
        Users with role <code>admin</code> can edit settings, retry failed jobs, and manage access.
      </p>

      {me && <CurrentSession me={me}/>}

      <div className="access-toolbar">
        <span className="stat"><strong>{active}</strong> active</span>
        <span className="stat"><span className="dot"/><strong>{admins}</strong> admins</span>
        <span className="stat"><span className="dot"/><strong>{linked}</strong> Clerk-linked</span>
        <span className="stat"><span className="dot"/><strong>{total - active}</strong> disabled</span>
      </div>

      {showAdd && (
        <div className="add-user-form">
          <input placeholder="email@domain.tld" type="email"
                 value={draft.email}
                 onChange={(e) => setDraft(d => ({...d, email: e.target.value}))}
                 onKeyDown={(e) => { if (e.key === "Enter") addUser(); }}/>
          <input placeholder="Display name (optional)"
                 value={draft.name}
                 onChange={(e) => setDraft(d => ({...d, name: e.target.value}))}/>
          <select value={draft.role} onChange={(e) => setDraft(d => ({...d, role: e.target.value}))}>
            <option value="user">user</option>
            <option value="admin">admin</option>
          </select>
          <div className="row" style={{gap: 4}}>
            <button className="btn primary" onClick={addUser} disabled={!draft.email}
                    style={{fontSize: 12, padding: "6px 10px"}}>
              Add
            </button>
            <button className="btn ghost" onClick={() => setShowAdd(false)}
                    style={{fontSize: 12, padding: "6px 10px"}}>
              <IconX size={12}/>
            </button>
          </div>
        </div>
      )}

      <div style={{
        border: "var(--rule)",
        borderRadius: "var(--radius)",
        overflow: "hidden",
        background: "var(--bg-card)",
      }}>
        <table className="users-table">
          <colgroup>
            <col className="c-user"/>
            <col className="c-role"/>
            <col className="c-state"/>
            <col className="c-activity"/>
            <col className="c-clerk"/>
            <col className="c-act"/>
          </colgroup>
          <thead>
            <tr>
              <th style={{paddingLeft: 16}}>User</th>
              <th>Role</th>
              <th>Status</th>
              <th>Activity · 24h</th>
              <th>Clerk identity</th>
              <th style={{paddingRight: 16}}></th>
            </tr>
          </thead>
          <tbody>
            {users.map(u => (
              <UserRow key={u.email} u={u}
                       openMenu={openMenu === u.email}
                       onOpenMenu={() => setOpenMenu(openMenu === u.email ? null : u.email)}
                       onToggle={() => toggleState(u.email)}
                       onSetRole={(r) => setRole(u.email, r)}
                       onUnlink={() => unlink(u.email)}
                       onRemove={() => remove(u.email)}/>
            ))}
          </tbody>
        </table>
      </div>

      <div className="row" style={{marginTop: 10, fontSize: 11.5}}>
        <span className="mono muted">
          <code>GET /api/auth/users</code> · admin only
        </span>
        <div className="spacer"/>
        <a className="mono" style={{fontSize: 11.5, color: "var(--link)"}}>
          View audit log →
        </a>
      </div>
    </div>
  );
}

function CurrentSession({ me }) {
  return (
    <div className="access-me">
      <div className="avatar">{initialsOf(me.name)}</div>
      <div className="info">
        <div className="name">
          {me.name}
          <span className="role-chip admin" style={{marginLeft: 8, fontSize: 10}}>you · {me.role}</span>
        </div>
        <div className="email">{me.email} · signed in via Clerk · {fmtRelative(me.last_seen)}</div>
      </div>
      <div className="spacer"/>
      <button className="btn ghost" style={{fontSize: 12, padding: "4px 8px"}}>
        <IconExternal size={12}/> Manage in Clerk
      </button>
      <button className="btn" style={{fontSize: 12, padding: "4px 10px"}}>
        Sign out
      </button>
    </div>
  );
}

function UserRow({ u, openMenu, onOpenMenu, onToggle, onSetRole, onUnlink, onRemove }) {
  const isMe = u.is_me;
  return (
    <tr className={u.state === "disabled" ? "disabled" : ""}>
      <td style={{paddingLeft: 16}}>
        <div className={"u-id" + (isMe ? " me" : "")}>
          <div className="u-avatar">{initialsOf(u.name)}</div>
          <div style={{minWidth: 0, flex: 1, overflow: "hidden"}}>
            <div className="u-name">
              <span>{u.name}</span>
              {isMe && <span style={{fontSize: 10, color: "var(--accent)", fontWeight: 600, letterSpacing: "0.04em", textTransform: "uppercase", flexShrink: 0}}>· you</span>}
            </div>
            <div className="u-email">{u.email}</div>
          </div>
        </div>
      </td>
      <td>
        <span className={"role-chip " + u.role}>{u.role}</span>
      </td>
      <td>
        <span className={"state-cell" + (u.state === "disabled" ? " disabled" : "")}>
          <span className="dot"/>
          {u.state}
        </span>
      </td>
      <td className="mono tnum" style={{fontSize: 12, color: "var(--fg-soft)", whiteSpace: "nowrap"}}>
        {u.last_seen
          ? <>
              <span>{u.calls_24h > 0 ? `${u.calls_24h} calls` : "idle"}</span>
              <span className="muted"> · {fmtRelative(u.last_seen)}</span>
            </>
          : <span className="muted">never signed in</span>}
      </td>
      <td>
        {u.clerk_subject
          ? <span className="u-subject linked" title={u.clerk_subject}>
              {u.clerk_subject.slice(0, 14)}…
            </span>
          : <span className="u-subject-pending">manual · link on sign-in</span>}
      </td>
      <td style={{paddingRight: 16, textAlign: "right", position: "relative", width: 80}}>
        <div className="row-actions">
          <button className="iconbtn" title="More actions"
                  onClick={(e) => { e.stopPropagation(); onOpenMenu(); }}>
            <IconDot size={14}/>
          </button>
        </div>
        {openMenu && (
          <div onClick={(e) => e.stopPropagation()} style={{
            position: "absolute", right: 16, top: "calc(100% - 6px)",
            zIndex: 5,
            minWidth: 200,
            background: "var(--bg-card)",
            border: "var(--rule)",
            borderRadius: "var(--radius)",
            boxShadow: "var(--shadow)",
            padding: 4,
            display: "flex", flexDirection: "column",
            fontSize: 13,
          }}>
            {u.role === "user"
              ? <MenuItem onClick={() => onSetRole("admin")}>Promote to admin</MenuItem>
              : <MenuItem onClick={() => onSetRole("user")} disabled={u.is_me}>
                  Demote to user{u.is_me && <span className="muted"> · can't demote self</span>}
                </MenuItem>}
            <MenuItem onClick={onToggle} disabled={u.is_me}>
              {u.state === "active" ? "Disable user" : "Re-enable user"}
              {u.is_me && <span className="muted"> · can't disable self</span>}
            </MenuItem>
            {u.clerk_subject && (
              <MenuItem onClick={onUnlink}>Unlink Clerk identity</MenuItem>
            )}
            <div style={{height: 1, background: "var(--border-soft)", margin: "4px 0"}}/>
            <MenuItem onClick={onRemove} danger disabled={u.is_me}>
              Remove from allowlist
            </MenuItem>
          </div>
        )}
      </td>
    </tr>
  );
}

function MenuItem({ onClick, children, danger, disabled }) {
  return (
    <div onClick={disabled ? null : onClick} style={{
      padding: "7px 10px",
      borderRadius: "var(--radius-sm)",
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.5 : 1,
      color: danger ? "var(--err)" : undefined,
      whiteSpace: "nowrap",
    }} onMouseEnter={(e) => !disabled && (e.currentTarget.style.background = "var(--bg-soft)")}
       onMouseLeave={(e) => (e.currentTarget.style.background = "transparent")}>
      {children}
    </div>
  );
}

function initialsOf(name) {
  if (!name) return "?";
  const parts = name.split(/[\s\-_.@]+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return name.slice(0, 2).toUpperCase();
}

