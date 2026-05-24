// biome-ignore-all lint: Claude Design source port; integration-only edits live in api/data/main.
import React from "react";
import { useAuth } from "../hooks/useAuth";
import { adaptUsers } from "./adapters.js";
import { fetchJson, responseMessage } from "./api.jsx";
import { IconCards, IconCopy, IconDot, IconExternal, IconFeed, IconMoon, IconPlus, IconRefresh, IconSparkle, IconSun, IconTable, IconX } from "./icons.jsx";
import { STATS, fmtDisplayCurrency, fmtRelative, normalizeDisplayCurrency } from "./data.js";
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

export const CLERK_PROFILE_UNAVAILABLE = "Clerk profile management is unavailable in this deployment";

export function canRenderAccessGroup(currentUser) {
  return currentUser?.role === "admin";
}

export function SettingsPage({ t, setTweak, users: runtimeUsers = [], currentUser = null, onConfigSaved }) {
  const auth = useAuth();
  const [config, setConfig] = React.useState(null);
  const [configDraft, setConfigDraft] = React.useState(null);
  const [configState, setConfigState] = React.useState({ loading: true, error: null, saved: null });
  const [prompts, setPrompts] = React.useState({ active_version: "v3", versions: [] });
  const [promptVersion, setPromptVersion] = React.useState("v3");
  const [prompt, setPrompt] = React.useState(DEFAULT_PROMPT);
  const [promptState, setPromptState] = React.useState({ loading: true, error: null, saved: null });
  const promptRequestRef = React.useRef(0);
  const cap = Number(configDraft?.daily_spend_cap_usd ?? STATS.daily_spend_cap_usd);
  const capUsagePct = cap > 0 ? Math.min(100, (STATS.vast_spend_24h / cap) * 100) : 0;
  const webhook = configDraft?.webhook_default ?? "";
  const publicBase = configDraft?.public_base_url ?? "";
  const displayCurrency = normalizeDisplayCurrency(configDraft?.display_currency);
  const workerConcurrency = Number(configDraft?.worker_concurrency ?? STATS.worker_pool.total ?? 2);
  const keepBotwallRetries = Boolean(configDraft?.bot_wall_retry);
  const embedTranscript = Boolean(configDraft?.webhook_embed_transcript);
  const [extensionTokenState, setExtensionTokenState] = React.useState({ pending: false, error: null, token: null, copied: false });

  React.useEffect(() => {
    const controller = new AbortController();
    setConfigState({ loading: true, error: null, saved: null });
    fetchJson(auth, "/api/config", controller.signal)
      .then((body) => {
        if (controller.signal.aborted) return;
        const values = configValues(body?.config ?? {});
        setConfig(body?.config ?? {});
        setConfigDraft(values);
        setConfigState({ loading: false, error: null, saved: null });
      })
      .catch((error) => {
        if (!controller.signal.aborted) setConfigState({ loading: false, error: messageOf(error), saved: null });
      });
    return () => controller.abort();
  }, [auth]);

  React.useEffect(() => {
    const controller = new AbortController();
    const requestId = promptRequestRef.current + 1;
    promptRequestRef.current = requestId;
    setPromptState({ loading: true, error: null, saved: null });
    fetchJson(auth, "/api/prompts", controller.signal)
      .then(async (body) => {
        const active = body?.active_version ?? body?.versions?.find((v) => v.is_active)?.id ?? "v3";
        const response = await auth.protectedFetch("/api/prompts/" + active, { cache: "no-store", signal: controller.signal });
        if (!response.ok) throw new Error(await responseMessage(response));
        return { list: body, active, text: await response.text() };
      })
      .then(({ list, active, text }) => {
        if (controller.signal.aborted || requestId !== promptRequestRef.current) return;
        setPrompts(list);
        setPromptVersion(active);
        setPrompt(text);
        setPromptState({ loading: false, error: null, saved: null });
      })
      .catch((error) => {
        if (!controller.signal.aborted && requestId === promptRequestRef.current) setPromptState({ loading: false, error: messageOf(error), saved: null });
      });
    return () => controller.abort();
  }, [auth]);

  function setDraft(key, value) {
    setConfigDraft((draft) => ({ ...(draft ?? {}), [key]: value }));
    setConfigState((state) => ({ ...state, saved: null }));
  }
  function resetConfig() {
    setConfigDraft(configValues(config ?? {}));
    setConfigState((state) => ({ ...state, error: null, saved: null }));
  }
  async function saveConfig() {
    setConfigState({ loading: false, error: null, saved: null });
    try {
      const body = await fetchJson(auth, "/api/config", undefined, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(configPayload(configDraft ?? {})),
      });
      setConfig(body?.config ?? {});
      setConfigDraft(configValues(body?.config ?? {}));
      onConfigSaved && onConfigSaved(body?.config ?? {});
      setConfigState({ loading: false, error: null, saved: body?.restart_required?.length ? `Saved · restart required for ${body.restart_required.join(", ")}` : "Saved" });
    } catch (error) {
      setConfigState({ loading: false, error: messageOf(error), saved: null });
    }
  }
  async function loadPromptVersion(version) {
    const requestId = promptRequestRef.current + 1;
    promptRequestRef.current = requestId;
    setPromptVersion(version);
    setPromptState({ loading: true, error: null, saved: null });
    try {
      const response = await auth.protectedFetch("/api/prompts/" + version, { cache: "no-store" });
      if (!response.ok) throw new Error(await responseMessage(response));
      const text = await response.text();
      if (requestId !== promptRequestRef.current) return;
      setPrompt(text);
      setPromptState({ loading: false, error: null, saved: null });
    } catch (error) {
      if (requestId !== promptRequestRef.current) return;
      setPromptState({ loading: false, error: messageOf(error), saved: null });
    }
  }
  async function savePrompt() {
    setPromptState({ loading: false, error: null, saved: null });
    try {
      const write = await auth.protectedFetch("/api/prompts/" + promptVersion, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body: prompt }),
      });
      if (!write.ok) throw new Error(await responseMessage(write));
      const list = promptVersion === prompts.active_version
        ? await fetchJson(auth, "/api/prompts/active", undefined, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ version: promptVersion }),
          })
        : await fetchJson(auth, "/api/prompts");
      setPrompts(list);
      setPromptState({ loading: false, error: null, saved: promptVersion === list.active_version ? "Prompt saved" : "Draft saved" });
    } catch (error) {
      setPromptState({ loading: false, error: messageOf(error), saved: null });
    }
  }
  async function generateExtensionToken() {
    if (!auth.signedIn) return;
    setExtensionTokenState({ pending: true, error: null, token: null, copied: false });
    try {
      const body = await fetchJson(auth, "/api/auth/extension-token", undefined, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ label: "Settings access token" }),
      });
      setExtensionTokenState({ pending: false, error: null, token: body?.token ?? "", copied: false });
    } catch (error) {
      setExtensionTokenState({ pending: false, error: messageOf(error), token: null, copied: false });
    }
  }
  async function copyExtensionToken() {
    if (!extensionTokenState.token) return;
    try {
      await navigator.clipboard.writeText(extensionTokenState.token);
      setExtensionTokenState((state) => ({ ...state, copied: true, error: null }));
    } catch (error) {
      setExtensionTokenState((state) => ({ ...state, copied: false, error: messageOf(error) }));
    }
  }

  return (
    <div className="pane" style={{maxWidth: 960}}>
      <div className="pane-header">
        <div>
          <h1 className="pane-h1">Settings</h1>
          <div className="pane-sub">Reads <code>.env</code> · changes write back via <code>POST /api/config</code></div>
        </div>
        <div className="pane-actions">
          {configState.loading && <span className="chip"><span className="spinner"/> Loading config</span>}
          {configState.error && <span className="chip err">{configState.error}</span>}
          {configState.saved && <span className="chip ok">{configState.saved}</span>}
          <button className="btn ghost" onClick={resetConfig} disabled={!configDraft}>Discard</button>
          <button className="btn primary" onClick={saveConfig} disabled={!configDraft}>Save changes</button>
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
                     onChange={(e) => setDraft("daily_spend_cap_usd", parseFloat(e.target.value) || 0)}
                     style={{width: 100}}/>
              <span className="muted mono" style={{fontSize: 12}}>USD</span>
              <span className="muted" style={{fontSize: 12, marginLeft: 16}}>
                Current 24h spend: <span className="tnum" style={{color: "var(--fg-soft)"}}>{fmtDisplayCurrency(STATS.vast_spend_24h, displayCurrency)}</span>
                {" "}({capUsagePct.toFixed(0)}%)
              </span>
            </div>
            <span className="muted mono" style={{fontSize: 11.5}}>
              Cap input saves canonical USD; displayed spend/cost labels convert from USD to {displayCurrency}.
            </span>
            <div className="bar-track" style={{maxWidth: 260}}>
              <div style={{width: `${capUsagePct}%`}}/>
            </div>
          </div>
        </div>

        <div className="settings-row">
          <div className="row-label">
            Display currency
            <span className="hint">Controls product spend/cost labels. Backend storage and API values stay canonical USD.</span>
          </div>
          <div className="row-control">
            <div className="seg" style={{width: "fit-content"}}>
              {[
                ["ILS", "NIS / ILS"],
                ["USD", "USD"],
                ["EUR", "EUR"],
              ].map(([value, label]) => (
                <button key={value}
                        className={displayCurrency === value ? "active" : ""}
                        aria-pressed={displayCurrency === value}
                        onClick={() => setDraft("display_currency", value)}>
                  {label}
                </button>
              ))}
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
                <button key={n} className={n === workerConcurrency ? "active" : ""}
                        aria-pressed={n === workerConcurrency}
                        onClick={() => setDraft("worker_concurrency", n)}>{n}</button>
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
                    onClick={() => setDraft("bot_wall_retry", !keepBotwallRetries)}/>
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
                   onChange={(e) => setDraft("public_base_url", e.target.value)}/>
            <span className="muted mono" style={{fontSize: 11.5}}>
              Share links are minted from <code>public_base_url</code>; production uses <code>https://scribe.oklabs.uk/share/&lt;token&gt;</code>.
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
                   onChange={(e) => setDraft("webhook_default", e.target.value)}/>
            <div className="row" style={{gap: 12, alignItems: "center"}}>
              <span className={"toggle " + (embedTranscript ? "on" : "")}
                    onClick={() => setDraft("webhook_embed_transcript", !embedTranscript)}/>
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
                {prompts.versions.map((version) => (
                  <button key={version.id}
                          className={"chip" + (version.id === promptVersion ? " active" : "")}
                          style={{cursor: "pointer"}}
                          onClick={() => loadPromptVersion(version.id)}>
                    {version.id}{version.is_active ? " · active" : ""}
                  </button>
                ))}
              </div>
              <div className="row" style={{gap: 8}}>
                {promptState.loading && <span className="chip"><span className="spinner"/> Loading prompt</span>}
                {promptState.error && <span className="chip err">{promptState.error}</span>}
                {promptState.saved && <span className="chip ok">{promptState.saved}</span>}
                <button className="btn ghost" onClick={savePrompt} disabled={promptState.loading}
                        style={{fontSize: 12, padding: "4px 8px"}}>
                  <IconSparkle size={12}/> Save prompt
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

      {canRenderAccessGroup(currentUser) && <AccessGroup initialUsers={runtimeUsers}/>}

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
              <input type="text"
                     value={extensionTokenState.token ?? (auth.signedIn ? "token is shown once after generation" : "sign in with Clerk to generate a user token")}
                     readOnly
                     style={{maxWidth: 420}}/>
              <button className="btn primary"
                      onClick={generateExtensionToken}
                      disabled={!auth.signedIn || extensionTokenState.pending}
                      title={auth.signedIn ? "Create a new user-scoped extension token" : "Trusted-network access cannot create a user token without a Clerk session"}>
                {extensionTokenState.pending ? <span className="spinner"/> : <IconRefresh size={14}/>}
                {extensionTokenState.pending ? "Generating..." : "Generate token"}
              </button>
              <button className="btn"
                      onClick={copyExtensionToken}
                      disabled={!extensionTokenState.token}
                      title={extensionTokenState.token ? "Copy the one-time token" : "Generate a token first; stored tokens are hashed and cannot be shown again"}>
                <IconCopy size={14}/> {extensionTokenState.copied ? "Copied" : "Copy token"}
              </button>
            </div>
            {extensionTokenState.error && <span className="chip err">{extensionTokenState.error}</span>}
            <span className="muted mono" style={{fontSize: 11.5}}>
              current access: {auth.accessStatus} · generated tokens are returned once, then stored hashed
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}


function configValues(config) {
  return Object.fromEntries(Object.entries(config).map(([key, entry]) => [key, entry?.value]));
}

function configPayload(values) {
  return {
    daily_spend_cap_usd: Number(values.daily_spend_cap_usd ?? 0),
    worker_concurrency: Number(values.worker_concurrency ?? 1),
    bot_wall_retry: Boolean(values.bot_wall_retry),
    webhook_default: values.webhook_default ?? "",
    webhook_embed_transcript: Boolean(values.webhook_embed_transcript),
    public_base_url: values.public_base_url ?? "",
    display_currency: normalizeDisplayCurrency(values.display_currency),
  };
}

function messageOf(error) {
  return error instanceof Error ? error.message : String(error);
}


// ─── Access group (users / authorization) ───────────────────────────────────
function AccessGroup({ initialUsers = [] }) {
  const auth = useAuth();
  const [users, setUsers] = React.useState(initialUsers);
  React.useEffect(() => setUsers(initialUsers), [initialUsers]);
  const [showAdd, setShowAdd] = React.useState(false);
  const [draft, setDraft] = React.useState({ email: "", name: "", role: "user" });
  const [refreshing, setRefreshing] = React.useState(false);
  const [status, setStatus] = React.useState({ error: null, saved: null });
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

  async function refresh() {
    setRefreshing(true);
    setStatus({ error: null, saved: null });
    try {
      const me = await fetchJson(auth, "/api/auth/me");
      const rows = me?.role === "admin" ? await fetchJson(auth, "/api/admin/users") : [];
      setUsers(adaptUsers(me, rows));
      setStatus({ error: null, saved: "Access refreshed" });
    } catch (error) {
      setStatus({ error: messageOf(error), saved: null });
    } finally {
      setRefreshing(false);
    }
  }
  async function addUser() {
    if (!draft.email) return;
    setStatus({ error: null, saved: null });
    try {
      await fetchJson(auth, "/api/admin/users", undefined, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: draft.email, display_name: draft.name || null, role: draft.role }),
      });
      setDraft({ email: "", name: "", role: "user" });
      setShowAdd(false);
      await refresh();
      setStatus({ error: null, saved: "User saved" });
    } catch (error) {
      setStatus({ error: messageOf(error), saved: null });
    }
  }
  async function toggleState(user) {
    setOpenMenu(null);
    setStatus({ error: null, saved: null });
    try {
      if (user.state === "active") {
        await fetchJson(auth, "/api/admin/users/" + user.id + "/disable", undefined, { method: "POST" });
      } else {
        await fetchJson(auth, "/api/admin/users/" + user.id + "/enable", undefined, { method: "POST" });
      }
      await refresh();
      setStatus({ error: null, saved: "User updated" });
    } catch (error) {
      setStatus({ error: messageOf(error), saved: null });
    }
  }
  async function setRole(user, role) {
    setOpenMenu(null);
    setStatus({ error: null, saved: null });
    try {
      await fetchJson(auth, "/api/admin/users/" + user.id + "/role", undefined, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ role }),
      });
      await refresh();
      setStatus({ error: null, saved: "Role updated" });
    } catch (error) {
      setStatus({ error: messageOf(error), saved: null });
    }
  }

  return (
    <div className="settings-group">
      <div className="row" style={{alignItems: "baseline", justifyContent: "space-between", marginBottom: 4}}>
        <h2>Access</h2>
        <div className="row" style={{gap: 8}}>
          {status.error && <span className="chip err">{status.error}</span>}
          {status.saved && <span className="chip ok">{status.saved}</span>}
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
                       onToggle={() => toggleState(u)}
                       onSetRole={(r) => setRole(u, r)}/>
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
  const auth = useAuth();
  const [accountState, setAccountState] = React.useState({ error: null, saved: null, signingOut: false });
  const canManageClerk = auth.signedIn && clerkProfileAction() !== null;
  const canSignOut = auth.signedIn && auth.clerkReady;

  async function manageInClerk() {
    const action = clerkProfileAction();
    if (!action) {
      setAccountState({ error: CLERK_PROFILE_UNAVAILABLE, saved: null, signingOut: false });
      return;
    }
    setAccountState({ error: null, saved: null, signingOut: false });
    try {
      await action();
    } catch (_error) {
      setAccountState({ error: CLERK_PROFILE_UNAVAILABLE, saved: null, signingOut: false });
    }
  }
  async function signOut() {
    if (!canSignOut) return;
    setAccountState({ error: null, saved: null, signingOut: true });
    try {
      await auth.signOut();
      setAccountState({ error: null, saved: "Signed out", signingOut: false });
    } catch (error) {
      setAccountState({ error: messageOf(error), saved: null, signingOut: false });
    }
  }

  return (
    <div className="access-me">
      <div className="avatar">{initialsOf(me.name)}</div>
      <div className="info">
        <div className="name">
          {me.name}
          <span className="role-chip admin" style={{marginLeft: 8, fontSize: 10}}>you · {me.role}</span>
        </div>
        <div className="email">
          {me.email} · {auth.signedIn ? "signed in via Clerk" : `access via ${auth.accessStatus}`} · {fmtRelative(me.last_seen)}
        </div>
      </div>
      <div className="spacer"/>
      {accountState.error && <span className="chip err">{accountState.error}</span>}
      {accountState.saved && <span className="chip ok">{accountState.saved}</span>}
      {!auth.signedIn && <span className="muted mono" style={{fontSize: 11.5}}>Clerk session unavailable</span>}
      {auth.signedIn && !canManageClerk && <span className="muted mono" style={{fontSize: 11.5}}>Clerk profile method unavailable</span>}
      <button className="btn ghost"
              onClick={manageInClerk}
              disabled={!canManageClerk}
              title={canManageClerk ? "Open Clerk profile management" : CLERK_PROFILE_UNAVAILABLE}
              style={{fontSize: 12, padding: "4px 8px"}}>
        <IconExternal size={12}/> Manage in Clerk
      </button>
      <button className="btn"
              onClick={signOut}
              disabled={!canSignOut || accountState.signingOut}
              title={canSignOut ? "Sign out of this Clerk session" : "No Clerk session is available to sign out from"}
              style={{fontSize: 12, padding: "4px 10px"}}>
        {accountState.signingOut ? "Signing out..." : "Sign out"}
      </button>
    </div>
  );
}

export function clerkProfileAction() {
  if (typeof window === "undefined" || !window.Clerk) return null;
  const clerk = window.Clerk;
  const actions = ["redirectToUserProfile", "openUserProfile", "openProfile"]
    .filter((method) => typeof clerk[method] === "function")
    .map((method) => () => clerk[method]());
  if (actions.length === 0) return null;
  return async () => {
    for (const action of actions) {
      try {
        await action();
        return;
      } catch (_error) {
        // Try the next Clerk runtime path, then surface stable product copy.
      }
    }
    throw new Error(CLERK_PROFILE_UNAVAILABLE);
  };
}

function UserRow({ u, openMenu, onOpenMenu, onToggle, onSetRole }) {
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
              <MenuItem disabled>Unlink Clerk identity<span className="muted"> · no endpoint</span></MenuItem>
            )}
            <div style={{height: 1, background: "var(--border-soft)", margin: "4px 0"}}/>
            <MenuItem danger disabled>
              Remove from allowlist
              <span className="muted"> · no endpoint</span>
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
