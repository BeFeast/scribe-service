// Mock data for Scribe UX. All data shapes mirror src/scribe/db/models.py.
// Job status enum: queued | downloading | transcribing | summarizing | done | failed

const TRANSCRIPTS = [
  {
    id: 142, video_id: "qkrUyV9N5nQ", title: "Rich Hickey — Simple Made Easy",
    tags: ["systems", "philosophy", "talk"], lang: "en", duration_seconds: 3742,
    vast_cost: 0.0184, created_at: "2026-05-15T11:02:00Z",
    summary_shortlink: "go.oklabs.uk/142s", transcript_shortlink: "go.oklabs.uk/142t",
    summary_md: `## TL;DR
The argument isn't about *easy* (familiar, near at hand) vs *hard*. It's about **simple** (one fold, one role) vs **complex** (interleaved, braided). Easy is local to you; simple is a property of the artifact. We optimize for easy and pay the bill in complexity later, usually as bugs we cannot reason about.

## Key moves
1. **Separate complecting from composing.** Composing is fine — gluing parts. Complecting is the enemy: state + identity + value rolled into one mutable bag.
2. **One role per construct.** A class that holds state AND has a lifecycle AND fires events is three roles braided together.
3. **Values over places.** Immutable values let you reason locally; places (variables, fields) force you to reason about *when*.
4. **Declarative over imperative** where the runtime can do the work for you. SQL beats hand-coded joins because you said *what*, not *how*.

## Notable callouts
- The "I can build it in an afternoon" trap — easy at construction, complex forever after.
- Tools that prevent error vs tools that surface error early.
- "We can solve any problem by introducing an extra level of indirection… except too many levels of indirection." — Kevlin Henney, paraphrased.

## What I took away
A weekly hygiene loop: list the constructs you've added this week, mark which ones do exactly one thing. If a construct does two things, write down what would have to be true for splitting them to not be worth it. Usually nothing is true.`,
    transcript_excerpt: "[00:00:12] So I'm going to talk about simple versus easy today. These are words that people use interchangeably and that's a problem because they actually mean very different things. [00:00:34] Simple comes from a root that means one fold, one twist. The opposite of simple is complex, which means braided together…"
  },
  {
    id: 141, video_id: "f84n5oFoZBc", title: "Bryan Cantrill — I Have Come to Bury the Andon Cord",
    tags: ["ops", "incident-response", "talk"], lang: "en", duration_seconds: 2114,
    vast_cost: 0.0102, created_at: "2026-05-15T08:21:00Z",
    summary_shortlink: "go.oklabs.uk/141s", transcript_shortlink: null,
    summary_md: `## TL;DR
The Toyota "andon cord" — anyone on the line can stop the line — is the wrong metaphor for ops. Stopping a software system is rarely the right move. The right move is *production debugging at speed*: instrumenting, narrowing, and shipping forward while the line keeps moving.

## Argument
- Andon assumes a physical process where stopping is cheap and resumption is automatic. Neither is true for software.
- A blameless culture is necessary but not sufficient. You also need *epistemic patience* — the discipline to keep asking "but why?" past the first plausible answer.
- Telemetry is a capital good. You don't notice the cost of not having it until 3am.

## Memorable lines
> "The cost of a missing log line is paid in pages, weeks, and credibility."`,
    transcript_excerpt: "[00:01:04] Andon cord — it's a beautiful idea, it really is. You're on a Toyota factory line and you see something wrong, you pull a cord and the line stops. That's not what we do. That's not what we do AT ALL…"
  },
  {
    id: 140, video_id: "0gAhsxgnA5Y", title: "Gary Bernhardt — Wat",
    tags: ["humor", "javascript", "talk"], lang: "en", duration_seconds: 264,
    vast_cost: 0.0021, created_at: "2026-05-14T22:14:00Z",
    summary_shortlink: "go.oklabs.uk/140s", transcript_shortlink: "go.oklabs.uk/140t",
    summary_md: `## TL;DR
Five-minute lightning talk. The premise: JavaScript and Ruby do *deeply* unexpected things with operators and coercion. The point isn't to dunk on dynamic typing — it's that the type system you don't have is one your users will discover in production.

## The four wats
- \`[] + []\` is the empty string.
- \`[] + {}\` is \`"[object Object]"\`.
- \`{} + []\` is \`0\` (because \`{}\` parses as a block).
- \`{} + {}\` is \`NaN\`.

## Takeaway
"Surprise" is a feature of a language only until you have to keep one alive.`,
    transcript_excerpt: "[00:00:08] So, JavaScript. Let's add an array to an array. (laughter) That's the empty string. Of course it is. Of course. Wat…"
  },
  {
    id: 139, video_id: "lKXe3HUG2l4", title: "Joe Armstrong — The Mess We're In",
    tags: ["erlang", "philosophy", "talk"], lang: "en", duration_seconds: 2856,
    vast_cost: 0.0141, created_at: "2026-05-14T15:40:00Z",
    summary_shortlink: "go.oklabs.uk/139s", transcript_shortlink: "go.oklabs.uk/139t",
    summary_md: `## TL;DR
We have built an industry on top of accidents. Most of what we call "software complexity" is layers we added to paper over earlier layers, and almost nobody is paid to remove layers.

## The seven problems
1. We don't know how to specify what we want.
2. We don't know how to find duplicates.
3. We don't know what we built.
4. We have no way to roll back time on a running system.
5. Files don't compose.
6. Names are addresses are values are identifiers, and we conflate them.
7. We cannot agree on what "the same" means.

## Personal note
The framing of "the mess" as something we *chose* — and could choose differently — is the point. Pessimistic in tone, optimistic in substance.`,
    transcript_excerpt: "[00:00:34] I want to talk today about the mess we're in. I want to talk about how we got here, and I want to suggest that we did not have to get here…"
  },
  {
    id: 138, video_id: "rmueBVrLKcY", title: "Postgres Performance — Bruce Momjian",
    tags: ["postgres", "performance", "database"], lang: "en", duration_seconds: 3210,
    vast_cost: 0.0162, created_at: "2026-05-14T09:12:00Z",
    summary_shortlink: "go.oklabs.uk/138s", transcript_shortlink: "go.oklabs.uk/138t",
    summary_md: `## TL;DR
Postgres performance is mostly about understanding what the planner can and can't see. Almost every slow query is one of four shapes, and EXPLAIN ANALYZE will tell you which.

## The four shapes
- **Bad row estimate** — stats are stale or correlated columns are independent in pg's model.
- **Wrong join order** — planner picked nested loop when hash would have won, or vice versa.
- **Missing index** — but also: the index it picked is the wrong one.
- **Lock contention** — fast in isolation, slow under concurrency.

## Operational hygiene
- ANALYZE after every bulk load. Auto-analyze isn't fast enough for write-heavy tables.
- pg_stat_statements is non-negotiable.
- track_io_timing turned on, sampled, never off.`,
    transcript_excerpt: "[00:00:22] So, performance. Postgres performance comes down to one thing: does the planner have an accurate model of your data? When it does, everything is fast. When it doesn't, nothing is fast…"
  },
  {
    id: 137, video_id: "Mp0vhMDI7fA", title: "John Carmack on Tooling and Iteration Speed",
    tags: ["graphics", "tooling", "interview"], lang: "en", duration_seconds: 5102,
    vast_cost: 0.0238, created_at: "2026-05-13T19:55:00Z",
    summary_shortlink: "go.oklabs.uk/137s", transcript_shortlink: "go.oklabs.uk/137t",
    summary_md: `## TL;DR
Iteration speed is the only metric that compounds. Everything that makes the inner loop faster — hot reload, deterministic replay, fast tests — is worth more than everything that makes the steady-state faster.

## The Carmack rule
"If you find yourself debugging by adding print statements, the first thing to fix is your print statements."

## On AI tools
The interesting use is not generation, it's *narrowing*. Bisecting bug surfaces, suggesting plausible call sites, generating reproduction inputs. Generation is a parlor trick; narrowing is leverage.

## On focus
Notebooks beat documents. A grep-able log of what you tried, what failed, and what surprised you compounds over years.`,
    transcript_excerpt: "[00:00:18] The thing about iteration speed is people underestimate it because it's compounding. If you can do twice as many experiments in a day, that's not twice the progress, that's exponentially more progress over a month…"
  },
  {
    id: 136, video_id: "K8m9R9hgKwk", title: "HashiCorp Vault — Secrets at Scale",
    tags: ["security", "ops", "vault"], lang: "en", duration_seconds: 2740,
    vast_cost: 0.0134, created_at: "2026-05-13T14:08:00Z",
    summary_shortlink: "go.oklabs.uk/136s", transcript_shortlink: "go.oklabs.uk/136t",
    summary_md: `## TL;DR
The interesting problem isn't *storing* secrets, it's *rotating* them without an outage. Most teams treat secrets like config and pay for it on day 90 when a key leaks and they can't find every consumer.

## The five stages of secret hygiene
1. We have a .env file.
2. We have a .env file checked into a private repo.
3. We have a secrets manager nobody reads from at runtime.
4. We have dynamic secrets but a 4-hour TTL that nobody respects.
5. Short-lived credentials, audit trails, and a rotation drill that actually runs.`,
    transcript_excerpt: "[00:00:11] How many of you have a .env file that you'd be embarrassed to show me? OK. Now, how many of you have rotated a secret in the last 30 days? Yeah. That's the talk…"
  },
  {
    id: 135, video_id: "8aGhZQkoFbQ", title: "On Calm Technology — Amber Case",
    tags: ["design", "philosophy", "talk"], lang: "en", duration_seconds: 1880,
    vast_cost: 0.0094, created_at: "2026-05-13T08:30:00Z",
    summary_shortlink: "go.oklabs.uk/135s", transcript_shortlink: "go.oklabs.uk/135t",
    summary_md: `## TL;DR
A calm tool requires the smallest possible amount of attention. The kettle that whistles, the indicator light that glows steady, the dashboard that fits in peripheral vision. Almost all software is the opposite — it demands attention to remain useful.

## The eight principles (paraphrased)
1. Technology should require the smallest possible amount of attention.
2. It should inform and create calm.
3. It should make use of the periphery.
4. It should amplify the best of technology and the best of humanity.
5. It can communicate, but doesn't need to speak.
6. It should work even when it fails.
7. The right amount of technology is the minimum needed to solve the problem.
8. Technology should respect social norms.`,
    transcript_excerpt: "[00:00:09] A calm technology is one that asks for your attention only when it needs to, and the rest of the time, sits at the edge of your awareness. The kettle. The kettle is calm technology…"
  },
  {
    id: 134, video_id: "_RZBKkXBcQ8", title: "Linux io_uring — The New Async I/O",
    tags: ["linux", "performance", "kernel"], lang: "en", duration_seconds: 3620,
    vast_cost: 0.0179, created_at: "2026-05-12T21:14:00Z",
    summary_shortlink: "go.oklabs.uk/134s", transcript_shortlink: "go.oklabs.uk/134t",
    summary_md: `## TL;DR
io_uring is what async I/O on Linux *should have been*. epoll was a workaround; io_uring is the API. Submission/completion queues let you batch thousands of operations into a single syscall, and recent kernels add registered buffers + linked operations + polled mode.

## When to reach for it
- You're syscall-bound, not CPU-bound.
- Network and disk I/O are interleaved.
- You can express your work as a DAG of dependent operations.

## When not to
- You don't have throughput problems. epoll is fine.
- Your runtime (Go, Tokio) hasn't shipped a stable io_uring backend yet.`,
    transcript_excerpt: "[00:00:14] So, io_uring. The interesting thing about io_uring isn't that it's faster than epoll — it is, but that's not the point. The point is that it's a different shape…"
  },
  {
    id: 133, video_id: "RGfhfyTu2-Q", title: "On Writing — George Saunders Masterclass clip",
    tags: ["writing", "craft", "interview"], lang: "en", duration_seconds: 1240,
    vast_cost: 0.0061, created_at: "2026-05-12T17:00:00Z",
    summary_shortlink: "go.oklabs.uk/133s", transcript_shortlink: "go.oklabs.uk/133t",
    summary_md: `## TL;DR
Revision is the writer's actual job. The first draft is just raw material. Saunders' method: read your draft as a reader, mark the moments your attention drifts, and only fix those. Don't argue with the meter, just trust it.

## The meter exercise
Imagine a meter that goes from -1 (bored) to +1 (engaged). Read your draft and mark the meter for each paragraph. Edit only what's below zero. Don't try to make the +1s into +2s — you'll lose them.`,
    transcript_excerpt: "[00:00:07] I have a little meter in my head when I read my own work. It goes from minus-one to plus-one. And my job in revision is just to find the minus-ones and figure out why…"
  },
  // Partial — whisper done, summary failed/pending
  {
    id: 132, video_id: "wzrn8Hd6_GE", title: "Dan Luu — Computer Latency 1977-2017",
    tags: ["systems", "performance"], lang: "en", duration_seconds: 2400,
    vast_cost: 0.0118, created_at: "2026-05-12T11:22:00Z",
    summary_shortlink: null, transcript_shortlink: "go.oklabs.uk/132t",
    summary_md: null,
    transcript_excerpt: "[00:00:09] We have made computers about a million times faster since 1977 and they feel about the same. This talk is about why that is and what it means…"
  },
  // Older
  {
    id: 131, video_id: "FihU5JxmnBg", title: "Notes on Type — Erik Spiekermann interview",
    tags: ["design", "typography", "interview"], lang: "en", duration_seconds: 3380,
    vast_cost: 0.0167, created_at: "2026-05-11T20:11:00Z",
    summary_shortlink: "go.oklabs.uk/131s", transcript_shortlink: "go.oklabs.uk/131t",
    summary_md: `## TL;DR
A typeface is a tool, not a piece of art. The job is to disappear. If the reader notices the type, you've failed — unless noticing was the point, in which case the type is the message and not the carrier.`,
    transcript_excerpt: "[00:00:12] Type is a tool. A good typeface, like a good chair, disappears when it's working. You don't think about the chair, you just sit in it…"
  },
  {
    id: 130, video_id: "8pTEmbeENF4", title: "Bret Victor — The Future of Programming",
    tags: ["philosophy", "talk"], lang: "en", duration_seconds: 1980,
    vast_cost: 0.0098, created_at: "2026-05-11T09:44:00Z",
    summary_shortlink: "go.oklabs.uk/130s", transcript_shortlink: "go.oklabs.uk/130t",
    summary_md: `## TL;DR
A performance-as-talk. Victor delivers the keynote in character as a 1973 researcher predicting where programming will be in 40 years. The bit lands because the predictions are *all things we abandoned* — direct manipulation, goal-directed dataflow, end-user programming.`,
    transcript_excerpt: "[00:00:08] Hello, I'm here to talk about the future of programming. It's 1973. We're going to be very excited about what programming looks like in the year 2013…"
  },
  {
    id: 129, video_id: "u8VQ9-pjFwM", title: "On Documentation — Daniele Procida (Diátaxis)",
    tags: ["docs", "writing", "talk"], lang: "en", duration_seconds: 1620,
    vast_cost: 0.0079, created_at: "2026-05-10T16:30:00Z",
    summary_shortlink: "go.oklabs.uk/129s", transcript_shortlink: "go.oklabs.uk/129t",
    summary_md: `## TL;DR
There are four kinds of documentation and they should be written separately: tutorials (learning-oriented), how-tos (problem-oriented), references (information-oriented), and explanations (understanding-oriented). Most docs are bad because they try to be all four at once.`,
    transcript_excerpt: "[00:00:11] The reason your documentation is bad is not that you're bad at writing. It's that you're writing four kinds of document and pretending it's one…"
  },
];

// In-flight jobs (not yet a transcript row)
const ACTIVE_JOBS = [
  {
    id: 218, video_id: "kxopViU98Xo", url: "https://youtu.be/kxopViU98Xo",
    title: "Linus Torvalds on Git — Google Tech Talk",
    status: "transcribing", source: "telegram",
    started_at: "2026-05-16T09:42:08Z", elapsed_s: 184,
    stages: {
      queued:       { state: "done",    started_at: "2026-05-16T09:42:08Z", finished_at: "2026-05-16T09:42:09Z", note: "Position 1 of 1 in queue" },
      downloading:  { state: "done",    started_at: "2026-05-16T09:42:09Z", finished_at: "2026-05-16T09:43:21Z", note: "yt-dlp · android-vr client · 78 MB · residential IP", duration_s: 72 },
      transcribing: { state: "active",  started_at: "2026-05-16T09:43:21Z", note: "faster-whisper large-v3-turbo · RTX 4090 · 04:21 / 16:42 · $0.0084 so far", progress: 0.26 },
      summarizing:  { state: "pending" },
      done:         { state: "pending" },
    },
  },
  {
    id: 217, video_id: "F8gjRgUMytY", url: "https://youtu.be/F8gjRgUMytY",
    title: "Tracy Kidder — The Soul of a New Machine (audiobook excerpt)",
    status: "summarizing", source: "obsidian",
    started_at: "2026-05-16T09:38:14Z", elapsed_s: 412,
    stages: {
      queued:       { state: "done", finished_at: "2026-05-16T09:38:14Z" },
      downloading:  { state: "done", note: "yt-dlp · web client · 42 MB", duration_s: 38 },
      transcribing: { state: "done", note: "faster-whisper · 24:18 audio · $0.0141 · 1.6× realtime", duration_s: 318 },
      summarizing:  { state: "active", started_at: "2026-05-16T09:44:12Z", note: "codex (gpt-5) · prompt v3 · streaming · 240 tok/s", progress: 0.62 },
      done:         { state: "pending" },
    },
  },
  {
    id: 216, video_id: "8XaVlML_8aQ", url: "https://youtu.be/8XaVlML_8aQ",
    title: "Queued — waiting for worker slot",
    status: "queued", source: "manual",
    started_at: "2026-05-16T09:44:51Z", elapsed_s: 11,
    stages: {
      queued: { state: "active", note: "Position 1 of 1 — all workers busy" },
      downloading: { state: "pending" }, transcribing: { state: "pending" },
      summarizing: { state: "pending" }, done: { state: "pending" },
    },
  },
];

// Recent failures (for ops dashboard)
const RECENT_FAILURES = [
  { id: 214, video_id: "p7AnE_b4j8c", title: "[unresolved] yt-dlp · sign in to confirm you're not a bot",
    status: "failed", error: "youtube.bot_wall · player stage · client=android-vr · attempted 4 fallbacks",
    failed_at: "2026-05-16T07:21:00Z", source: "telegram" },
  { id: 207, video_id: "VqgUkExPvLY", title: "Vast.ai instance unreachable",
    status: "failed", error: "whisper.connect_timeout · instance i-8e9b2 · 90s · likely preempted",
    failed_at: "2026-05-15T18:02:00Z", source: "telegram" },
  { id: 199, video_id: "f-mZJaJZ0Vw", title: "codex summarizer timed out",
    status: "failed", error: "summarizer.timeout · 600s · partial transcript saved (#132) — POST /resummarize to retry",
    failed_at: "2026-05-15T14:48:00Z", source: "manual" },
];

// Tags rollup
function tagCounts() {
  const counts = {};
  for (const t of TRANSCRIPTS) {
    if (!t.tags) continue;
    for (const tag of t.tags) counts[tag] = (counts[tag] || 0) + 1;
  }
  return Object.entries(counts).sort((a,b) => b[1]-a[1]);
}

// Helpers
function fmtDuration(s) {
  if (s == null) return "—";
  const m = Math.floor(s/60), r = s%60;
  if (m >= 60) return `${Math.floor(m/60)}h ${m%60}m`;
  return `${m}:${String(r).padStart(2,"0")}`;
}
function fmtElapsed(s) {
  if (s == null) return "—";
  if (s < 60) return `${s}s`;
  const m = Math.floor(s/60), r = s%60;
  return `${m}m ${String(r).padStart(2,"0")}s`;
}
function fmtRelative(iso) {
  const t = new Date(iso).getTime();
  const now = new Date("2026-05-16T09:45:00Z").getTime();
  const diff = Math.max(0, Math.floor((now - t) / 1000));
  if (diff < 60) return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff/60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff/3600)}h ago`;
  return `${Math.floor(diff/86400)}d ago`;
}
function fmtDate(iso) {
  const d = new Date(iso);
  const today = new Date("2026-05-16T09:45:00Z");
  const sameDay = d.toDateString() === today.toDateString();
  const opts = sameDay
    ? { hour: "2-digit", minute: "2-digit" }
    : { month: "short", day: "numeric" };
  return d.toLocaleString("en-US", opts);
}
function fmtUsd(n) {
  if (n == null) return "—";
  return "$" + n.toFixed(n < 0.1 ? 4 : 2);
}

// Daily-report stats (mirrors GET /admin/daily-report)
const STATS = {
  window_days: 1,
  jobs_by_status: { done: 18, failed: 1, queued: 1, transcribing: 1, summarizing: 1 },
  transcripts_done: 18,
  transcripts_partial: 1,
  queue_depth: 3,
  vast_spend_24h: 0.184,
  vast_spend_7d: 1.42,
  vast_spend_30d: 5.21,
  daily_spend_cap_usd: 2.0,
  backup: { last_success_iso: "2026-05-16T03:00:14Z", age_seconds: 24286, stale_after: 90000, stale: false, path: "/var/lib/scribe/backups/heartbeat" },
  worker_pool: { active: 2, total: 2 },
};

// Spend over last 14 days, USD, for the sparkline
const SPEND_SERIES = [0.21, 0.18, 0.14, 0.32, 0.27, 0.19, 0.09, 0.41, 0.38, 0.22, 0.15, 0.26, 0.19, 0.184];

// Authorized users — /api/auth/me + /api/auth/users.
// Role: admin (full settings + ops) | user (submit + view) | reader (view-only).
// Source: clerk (Clerk-linked, has subject) | manual (added by admin, no Clerk login yet).
const SCRIBE_USERS = [
  { email: "oleg@kossoy.com",        name: "kossoy",             role: "admin",  state: "active",   source: "clerk",  clerk_subject: "user_3E2zScTE8HlHUF2E9ee6Ade39uM", last_seen: "2026-05-24T07:02:00Z", calls_24h: 142, is_me: false },
  { email: "oleg.kossoy@gmail.com",  name: "oleg-kossoy-gmail",  role: "admin",  state: "active",   source: "clerk",  clerk_subject: "user_3E52OBZ9puvHY7ANbfvmQCzcW4q", last_seen: "2026-05-24T08:31:14Z", calls_24h: 38,  is_me: true  },
  { email: "oleg@befeast.com",       name: "oleg-at-befeast",    role: "user",   state: "active",   source: "clerk",  clerk_subject: "user_3E59MkAQOFm1ATWpzLvLyfXS4yV", last_seen: "2026-05-23T22:14:00Z", calls_24h: 6,   is_me: false },
  { email: "anton@kossoy.com",       name: "Anton Kossoy",       role: "user",   state: "active",   source: "manual", clerk_subject: null,                                last_seen: null,                     calls_24h: 0,   is_me: false },
  { email: "anton@befeast.com",      name: "Anton Kossoy",       role: "user",   state: "active",   source: "manual", clerk_subject: null,                                last_seen: null,                     calls_24h: 0,   is_me: false, note: "befeast" },
  { email: "dmirlin@gmail.com",      name: "dmirlin",            role: "user",   state: "active",   source: "clerk",  clerk_subject: "user_3E8HPphmNt0yHItnRJdgTpYKvAQ",  last_seen: "2026-05-23T18:42:00Z", calls_24h: 4,   is_me: false },
  { email: "test@example.dev",       name: "test runner",        role: "user",   state: "disabled", source: "manual", clerk_subject: null,                                last_seen: "2026-04-12T11:00:00Z", calls_24h: 0,   is_me: false },
];

Object.assign(window, {
  TRANSCRIPTS, ACTIVE_JOBS, RECENT_FAILURES, STATS, SPEND_SERIES, SCRIBE_USERS,
  tagCounts, fmtDuration, fmtElapsed, fmtRelative, fmtDate, fmtUsd,
});
