import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile, readdir, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { setTimeout as sleep } from "node:timers/promises";
import { chromium, request } from "playwright-core";
import { parse } from "yaml";

const prod = "https://scribe.oklabs.uk";
const cdp = process.env.SCRIBE_VERIFY_CDP_URL;
const requestTimeout = 15_000;
const pipelineTimeout = 12 * 60_000;
const scenarioDir = new URL("../acceptance/", import.meta.url);
const scenarios = new Map();
for (const file of (await readdir(scenarioDir)).filter((name) => name.endsWith(".yaml"))) {
  const spec = parse(await readFile(new URL(file, scenarioDir), "utf8"));
  assert.equal(spec.name, file.slice(0, -5));
  scenarios.set(spec.name, spec);
}

const selected = process.argv[2];
if (process.argv.length !== 3 || !(selected === "all" || scenarios.has(selected))) {
  console.error(`Usage: scripts/verify <all|${[...scenarios.keys()].join("|")}>`);
  process.exit(2);
}

const scratch = spawnSync("mktemp", ["-d", "-t", "scribe-verify.XXXXXXXX"], { encoding: "utf8" });
assert.equal(scratch.status, 0, scratch.stderr || "mktemp failed");
const evidence = scratch.stdout.trim();
const result = { selected, target: prod, scenarios: {}, screenshots: [], cleanup: { status: "not-needed" } };
const trace = `scribe-verify-${randomUUID()}`;
const fixture = scenarios.get("youtube-link-summary").fixture;
const videoId = new URL(fixture.url).pathname.slice(1);
const fixtureJobs = "/admin/acceptance/jobs";
const stop = new AbortController();
for (const signal of ["SIGINT", "SIGTERM"]) {
  process.once(signal, () => stop.abort(new Error(`Interrupted by ${signal}`)));
}

let operator, browser, signedIn, page, anonymous, jobId, transcript, spoken, share;
let submitInProgress, shareInProgress, downloadBody;
let allowSubmit = false, allowShare = false;

function errorMessage(error) {
  return String(error.message || error).replace(/\/share\/[\w-]+/g, "/share/[redacted]");
}

async function screenshot(name, locator = page, isAnonymous = false) {
  const filename = `${name}.png`;
  const mask = isAnonymous ? [] : [page.getByTitle("Copy for AI link")];
  await locator.screenshot({ path: join(evidence, filename), timeout: 5000, mask });
  result.screenshots.push(filename);
}

function matches(url, path) {
  return url.origin === prod && url.pathname === path;
}

async function responseAfter(action, path, method = "POST") {
  stop.signal.throwIfAborted();
  const [response] = await Promise.all([
    page.waitForResponse((res) => matches(new URL(res.url()), path) && res.request().method() === method),
    action(),
  ]);
  const missingFixtureRoute = path === "/jobs" && response.status() === 404;
  assert(response.ok(), missingFixtureRoute ? "Deploy Scribe with acceptance fixture support before running this scenario."
    : `${method} ${path} returned HTTP ${response.status()}`);
  return response;
}

async function json(path) {
  const response = await operator.get(path, { maxRedirects: 0 });
  assert(response.ok(), `GET ${path} returned HTTP ${response.status()}`);
  return response.json();
}

async function answer(route, response, body) {
  await route.fulfill({ status: response.status(), contentType: response.headers()["content-type"],
    body: body ?? await response.body() });
}

async function relayBrowserRequest(route) {
  const req = route.request();
  const { pathname } = new URL(req.url());
  const method = req.method();
  const transcriptPath = transcript && `/transcripts/${transcript.id}`;
  const sharePath = transcript && `/api${transcriptPath}/share-links`;
  const revokePath = share && `/api/share-links/${share.id}/revoke`;

  // The only privileged traffic comes from this page's Playwright routes.
  // Never pass arbitrary paths, query strings, headers or bodies to the LAN.
  if (method === "GET") {
    if (pathname === "/" || pathname.startsWith("/static/spa/")) return route.continue();
    if (pathname === "/api/library") {
      const rows = transcript ? (await json(`/api/library?limit=100&q=${encodeURIComponent(transcript.title)}`)).rows
        .filter((row) => row.id === transcript.id) : [];
      return route.fulfill({ json: { rows, total: rows.length, limit: 100, offset: 0 } });
    }
    if (pathname === "/api/jobs/active") {
      const job = jobId && await json(`/jobs/${jobId}`);
      const active = job && !["done", "failed"].includes(job.status);
      return route.fulfill({ json: { jobs: active ? [{ ...job, id: jobId, source: "acceptance" }] : [] } });
    }
    if (pathname === "/api/auth/config" || (jobId && pathname === `/jobs/${jobId}`) || pathname === transcriptPath
        || pathname === sharePath || (transcriptPath && pathname === `${transcriptPath}/transcript.md`)) {
      const response = await operator.get(pathname, { maxRedirects: 0 });
      if (pathname === sharePath && response.ok()) {
        const links = (await response.json()).filter((link) => link.id === share?.id);
        return route.fulfill({ json: links });
      }
      const body = await response.body();
      if (transcriptPath && pathname === `${transcriptPath}/transcript.md` && response.ok()) downloadBody = body;
      return answer(route, response, body);
    }
  }

  const data = method === "POST" && (pathname === "/jobs" || pathname === sharePath) ? req.postDataJSON() : null;
  if (pathname === "/jobs" && allowSubmit && !submitInProgress && data?.url === fixture.url && data.source === "manual") {
    submitInProgress = (async () => {
      const response = await operator.post(fixtureJobs, { data: { url: fixture.url }, maxRedirects: 0 });
      if (response.ok()) {
        const job = await response.json();
        if (job.correlation_id === trace && Number.isSafeInteger(job.job_id)) result.job_id = jobId = job.job_id;
      }
      await answer(route, response);
    })();
    return submitInProgress;
  }
  if (pathname === sharePath && allowShare && !shareInProgress && data?.target_kind === "ai_transcript") {
    shareInProgress = (async () => {
      const data = { target_kind: "ai_transcript", expires_at: new Date(Date.now() + 60 * 60_000).toISOString(), label: trace };
      const response = await operator.post(sharePath, { data, maxRedirects: 0 });
      if (response.ok()) {
        const link = await response.json();
        assert(link.label === trace && link.transcript_id === transcript.id && Number.isSafeInteger(link.id));
        result.share_id = link.id;
        share = link;
      }
      await answer(route, response);
    })();
    return shareInProgress;
  }
  if (method === "POST" && pathname === revokePath && !req.postData()) {
    return answer(route, await operator.post(revokePath, { maxRedirects: 0 }));
  }
  return route.fulfill({ status: 403, json: { detail: "Outside this acceptance scenario" } });
}

async function openBrowser() {
  assert(cdp, "Set SCRIBE_VERIFY_CDP_URL to the Chrome DevTools endpoint.");
  operator = await request.newContext({
    baseURL: prod, timeout: requestTimeout, extraHTTPHeaders: { Accept: "application/json", "X-Request-ID": trace },
  });
  const auth = await operator.get("/api/auth/me");
  assert(auth.ok() && (await auth.json()).kind === "trusted-lan",
    "Run verify from a network Scribe recognizes as trusted LAN. This run needs operator access for cleanup.");

  browser = await chromium.connectOverCDP(cdp, { timeout: requestTimeout, noDefaults: true });
  // Observe download initiation and inspect its response without writing to the remote Chrome host.
  signedIn = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: false });
  await signedIn.grantPermissions(["clipboard-read", "clipboard-write"], { origin: prod });
  page = await signedIn.newPage();
  page.setDefaultTimeout(requestTimeout);

  await page.route((url) => url.origin === prod, (route) =>
    relayBrowserRequest(route).catch(() => route.abort().catch(() => {})));
  await page.goto(`${prod}/#/library`, { waitUntil: "domcontentloaded" });
}

async function waitForJob() {
  const deadline = Date.now() + pipelineTimeout;
  while (Date.now() < deadline) {
    stop.signal.throwIfAborted();
    const job = await json(`/jobs/${jobId}`);
    assert(job.status !== "failed", `Job #${jobId} failed: ${job.error || "no error reported"}`);
    if (job.status === "done") return job.transcript;
    await sleep(3000, undefined, { signal: stop.signal });
  }
  throw new Error(`Job #${jobId} did not finish within ${pipelineTimeout / 60_000} minutes`);
}

async function verifyYoutube() {
  await page.getByRole("button", { name: "Submit URL", exact: true }).click();
  await page.getByPlaceholder(/^Paste a YouTube URL/).fill(fixture.url);
  allowSubmit = true;
  const submission = await responseAfter(() => page.getByRole("button", { name: /^Submit(?: ↵)?$/ }).click(), "/jobs");
  const submitted = await submission.json();
  assert(!submitted.deduplicated && jobId === submitted.job_id, "The UI did not create a fresh job");
  assert.equal(submitted.video_id, videoId);
  await page.getByText(`Queued as job #${jobId}`, { exact: true }).waitFor();
  await screenshot("youtube-submitted", page.locator(".cmdk-modal"));
  await page.getByRole("button", { name: "Watch pipeline" }).click();
  transcript = await waitForJob();
  assert(Number.isSafeInteger(transcript?.id), "Completed job has no transcript");
  const transcriptId = transcript.id;
  result.transcript_id = transcriptId;
  await page.locator(".chip.ok").filter({ hasText: /done/i }).waitFor();
  await screenshot("youtube-pipeline-done");

  await page.goto(`${prod}/#/library`, { waitUntil: "domcontentloaded" });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByPlaceholder(/Search titles/).fill(transcript.title);
  const entry = page.locator(".library-title").and(page.getByText(transcript.title, { exact: true }));
  await entry.waitFor();
  await screenshot("youtube-library");
  await entry.click();
  await page.waitForURL(`${prod}/#/transcript/${transcriptId}`);
  await page.getByRole("heading", { name: transcript.title, exact: true }).waitFor();
  const summary = page.locator(".prose");
  await summary.waitFor();
  assert((await summary.innerText()).trim(), "The visible summary is empty");
  const [download] = await Promise.all([
    page.waitForEvent("download"),
    responseAfter(() => page.getByRole("button", { name: "Download .md" }).click(),
      `/transcripts/${transcriptId}/transcript.md`, "GET"),
  ]);
  assert.equal(download.suggestedFilename(), `scribe-${transcriptId}-transcript.md`);
  const markdown = downloadBody?.toString("utf8") ?? "";
  spoken = markdown.split(/(?:^|\n)## Transcript[ \t]*\r?\n/).at(-1).trim();
  const excerpt = (await json(`/transcripts/${transcriptId}`)).transcript_excerpt?.trim().replace(/…$/, "");
  result.transcript_words = spoken.split(/\s+/).filter(Boolean).length;
  assert(excerpt && spoken.startsWith(excerpt), "The download does not match the transcript excerpt");
  assert(result.transcript_words >= fixture.min_transcript_words, "The downloaded transcript has too little speech");
  await screenshot("youtube-transcript-and-summary");
}

async function publicResponse(response, status, label) {
  assert.equal(response?.status(), status, label);
  const headers = await response.request().allHeaders();
  assert(!headers.cookie && !headers.authorization, `${label} sent credentials`);
}

async function anonymousFetch(tab, url) {
  const [response] = await Promise.all([
    tab.waitForResponse((res) => res.url() === url),
    tab.evaluate(async (href) => { await (await fetch(href, { credentials: "omit", cache: "no-store" })).text(); }, url),
  ]);
  return response;
}

async function verifyShare() {
  const transcriptId = transcript.id;
  await page.getByRole("button", { name: "Share", exact: true }).click();
  allowShare = true;
  await responseAfter(() => page.getByRole("button", { name: /^Copy for AI/ }).click(),
    `/api/transcripts/${transcriptId}/share-links`);
  assert.equal(share.target_kind, "ai_transcript");
  assert.equal(share.transcript_id, transcriptId);
  const url = new URL(share.share_url);
  assert.equal(url.origin, prod);
  assert.match(url.pathname, /^\/share\/[\w-]+$/);
  assert.equal(share.share_url_md, `${url.href}.md`);
  const shownLink = page.getByTitle("Copy for AI link");
  await shownLink.waitFor();
  assert.equal((await shownLink.innerText()).trim(), url.href);
  assert(await clearOwnClipboard(), "Clipboard has the wrong URL");
  await screenshot("share-created", page.locator(".share-sheet"));

  anonymous = await browser.newContext();
  const tab = await anonymous.newPage();
  tab.setDefaultTimeout(requestTimeout);
  const plain = await tab.goto(url.href);
  await publicResponse(plain, 200, "Anonymous share");
  assert.match(plain.headers()["content-type"], /^text\/plain\b/);
  assert((await tab.locator("body").innerText()).includes(spoken), "Anonymous page did not show the transcript speech");
  const md = await anonymousFetch(tab, share.share_url_md);
  await publicResponse(md, 200, "Anonymous Markdown share");
  assert.match(md.headers()["content-type"], /^text\/markdown\b/);
  assert((await md.text()).includes(spoken), "The Markdown share is missing the transcript speech");
  await screenshot("share-anonymous", tab, true);

  const revoke = page.getByRole("button", { name: "Revoke", exact: true });
  await responseAfter(() => revoke.click(), `/api/share-links/${share.id}/revoke`);
  await revoke.waitFor({ state: "hidden" });
  await publicResponse(await tab.reload(), 410, "Revoked share");
  await publicResponse(await anonymousFetch(tab, share.share_url_md), 410, "Revoked Markdown share");
  await screenshot("share-revoked", tab, true);
}

async function clearOwnClipboard() {
  if (!share?.share_url) return false;
  await page.bringToFront();
  return page.evaluate(async (url) => {
    if (await navigator.clipboard.readText() !== url) return false;
    await navigator.clipboard.writeText("");
    return true;
  }, share.share_url);
}

async function revokeOwnShare(transcriptId) {
  if (!share) return;
  try {
    assert(share.label === trace && share.transcript_id === transcriptId, "Cleanup refused a foreign share");
    const response = await operator.post(`/api/share-links/${share.id}/revoke`, { maxRedirects: 0 });
    assert(response.ok(), `Cleanup could not revoke the share (HTTP ${response.status()})`);
    const revoked = await response.json();
    assert(revoked.id === share.id && revoked.revoked_at, "Cleanup could not confirm the share was revoked");
    result.share_cleanup = { status: "revoked", share_id: share.id };
  } catch (error) {
    result.share_cleanup = { status: "fail", share_id: share.id, error: errorMessage(error) };
  }
}

async function cleanup() {
  if (submitInProgress && !jobId) {
    // A lost POST response can still leave a committed job. Match the request ID before deleting.
    const history = await json("/api/jobs?limit=100");
    for (const row of history.jobs.filter((job) => job.video_id === videoId && job.source === "acceptance")) {
      const response = await operator.get(`/jobs/${row.id}`);
      if (response.ok() && (await response.json()).correlation_id === trace) {
        jobId = row.id;
        result.job_id = jobId;
        break;
      }
    }
  }
  if (!jobId) return;
  const jobPath = `/jobs/${jobId}`;
  const current = await operator.get(jobPath);
  if (current.status() === 404) return;
  assert(current.ok(), `Cleanup could not look up job #${jobId} (HTTP ${current.status()})`);
  const job = await current.json();
  assert.equal(job.correlation_id, trace, "Cleanup refused a job owned by another run");
  const derivedTranscriptId = transcript?.id ?? job.transcript?.id;
  if (shareInProgress && !share && derivedTranscriptId) {
    const links = await json(`/api/transcripts/${derivedTranscriptId}/share-links`);
    const created = links.filter((link) => link.label === trace);
    assert(created.length < 2, "Cleanup found multiple share links for the run");
    share = created[0];
    await revokeOwnShare(derivedTranscriptId);
  }
  const adminPath = `${fixtureJobs}/${jobId}${share ? `?share_id=${share.id}` : ""}`;
  let deleted = await operator.delete(adminPath, { maxRedirects: 0 });
  if (deleted.status() === 409) {
    const cancel = await operator.post(`/admin/jobs/${jobId}/cancel`, { maxRedirects: 0 });
    assert([200, 404, 409].includes(cancel.status()), `Cleanup could not cancel job #${jobId} (HTTP ${cancel.status()})`);
    deleted = await operator.delete(adminPath, { maxRedirects: 0 });
  }
  assert([204, 404].includes(deleted.status()), `Cleanup could not delete job #${jobId} (HTTP ${deleted.status()})`);
  assert.equal((await operator.get(jobPath)).status(), 404, "The fixture job still exists");
  if (derivedTranscriptId) {
    assert.equal((await operator.get(`/transcripts/${derivedTranscriptId}`)).status(), 404, "The fixture transcript still exists");
  }
  if (share?.share_url?.startsWith(`${prod}/share/`)) {
    assert.equal((await operator.get(share.share_url, { maxRedirects: 0 })).status(), 404, "The fixture share still exists");
  }
  result.cleanup = { status: "deleted", job_id: jobId };
}

const implementations = {
  "youtube-link-summary": verifyYoutube,
  "copy-for-ai-share": verifyShare,
};

async function run(name) {
  if (result.scenarios[name]) return;
  const spec = scenarios.get(name);
  if (spec.status === "manual") {
    result.scenarios[name] = { status: "manual", reason: spec.reason };
    return;
  }
  try {
    if (spec.requires) {
      await run(spec.requires);
      assert.equal(result.scenarios[spec.requires].status, "pass", `${spec.requires} did not pass`);
    }
    stop.signal.throwIfAborted();
    if (!page) await openBrowser();
    await implementations[name](spec);
    result.scenarios[name] = { status: "pass" };
  } catch (error) {
    result.scenarios[name] = { status: "fail", error: errorMessage(error) };
    if (page) await screenshot(`${name}-failure`).catch(() => {});
  }
}

try {
  for (const name of selected === "all" ? scenarios.keys() : [selected]) await run(name);
} finally {
  allowSubmit = allowShare = false;
  await Promise.allSettled([submitInProgress, shareInProgress]);
  await revokeOwnShare(transcript?.id);
  try {
    await cleanup();
  } catch (error) {
    result.cleanup = { status: "fail", job_id: jobId, error: errorMessage(error) };
  }
  await clearOwnClipboard().catch((error) => { result.close_error = errorMessage(error); });
  for (const resource of [anonymous, signedIn, browser, operator]) {
    if (!resource) continue;
    await (resource === operator ? resource.dispose() : resource.close()).catch((error) => {
      result.close_error = errorMessage(error);
    });
  }
  const statuses = Object.values(result.scenarios).map(({ status }) => status);
  result.status = statuses.includes("fail") || result.cleanup.status === "fail"
    || result.share_cleanup?.status === "fail" || result.close_error || stop.signal.aborted
    ? "fail" : statuses.includes("pass") ? "pass" : "manual";
  await writeFile(join(evidence, "result.json"), `${JSON.stringify(result, null, 2)}\n`);
  for (const [name, { status, error, reason }] of Object.entries(result.scenarios)) {
    console.log(`${status.toUpperCase()} ${name}${error || reason ? ` — ${error || reason}` : ""}`);
  }
  if (result.cleanup.error) console.error(result.cleanup.error);
  if (result.share_cleanup?.error) console.error(result.share_cleanup.error);
  console.log(`${result.status.toUpperCase()} ${selected} — ${evidence}`);
  process.exitCode = result.status === "fail" ? 1 : 0;
}
