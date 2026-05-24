#!/usr/bin/env bun
import { mkdir, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const BASE_URL = process.env.SCRIBE_VISUAL_QA_BASE_URL ?? "http://10.10.0.13:13120/";
const API_BASE_URL = process.env.SCRIBE_VISUAL_QA_API_BASE_URL ?? "http://10.10.0.13:13120/";
const ROOT = dirname(dirname(fileURLToPath(import.meta.url)));
const OUT_DIR = process.env.SCRIBE_VISUAL_QA_OUT_DIR ?? join(ROOT, "artifacts/visual-qa");
const CHROME = process.env.CHROME_BIN ?? "google-chrome";
const WAIT_MS = Number(process.env.SCRIBE_VISUAL_QA_WAIT_MS ?? "1600");
const WIDTHS = [
	{ name: "desktop", width: 1440, height: 1000, mobile: false },
	{ name: "mobile", width: 390, height: 900, mobile: true },
];

const ROUTES = [
	{ key: "library", hash: "#/library" },
	{ key: "queue", hash: "#/queue" },
	{ key: "ops", hash: "#/ops" },
	{ key: "settings", hash: "#/settings" },
];
const VARIANTS = ["paper", "terminal", "console", "field"];
const THEMES = ["light", "dark"];
const DENSITIES = ["compact", "cozy", "comfy"];
const LIBRARY_LAYOUTS = ["table", "feed", "cards"];

function absolute(hash) {
	return new URL(hash, BASE_URL).href;
}

function sleep(ms) {
	return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchJson(path) {
	try {
		const response = await fetch(new URL(path, API_BASE_URL), {
			headers: { Accept: "application/json" },
		});
		if (!response.ok) {
			return null;
		}
		return await response.json();
	} catch {
		return null;
	}
}

async function discoverRoutes() {
	const routes = [...ROUTES];
	const explicitTranscript = process.env.SCRIBE_VISUAL_QA_TRANSCRIPT_ID;
	const explicitJob = process.env.SCRIBE_VISUAL_QA_JOB_ID;

	if (explicitTranscript) {
		routes.splice(1, 0, {
			key: `transcript-${explicitTranscript}`,
			hash: `#/transcript/${explicitTranscript}`,
		});
	} else {
		const library = await fetchJson("/api/library?limit=1");
		const id = library?.rows?.[0]?.id;
		if (typeof id === "number") {
			routes.splice(1, 0, { key: `transcript-${id}`, hash: `#/transcript/${id}` });
		}
	}

	if (explicitJob) {
		routes.splice(3, 0, { key: `job-${explicitJob}`, hash: `#/jobs/${explicitJob}` });
	} else {
		const active = await fetchJson("/api/jobs/active");
		const activeId = active?.jobs?.[0]?.id;
		const ops = activeId === undefined ? await fetchJson("/api/ops") : null;
		const failureId = ops?.recent_failures?.[0]?.id;
		const id = typeof activeId === "number" ? activeId : failureId;
		if (typeof id === "number") {
			routes.splice(3, 0, { key: `job-${id}`, hash: `#/jobs/${id}` });
		}
	}

	routes.push({ key: "command-palette", hash: "#/library", commandPalette: true });
	return routes;
}

async function waitForVersion(port, timeoutMs = 10000) {
	const started = Date.now();
	let lastError;
	while (Date.now() - started < timeoutMs) {
		try {
			const response = await fetch(`http://127.0.0.1:${port}/json/version`);
			if (response.ok) {
				return await response.json();
			}
		} catch (error) {
			lastError = error;
		}
		await sleep(100);
	}
	throw new Error(`Chrome did not expose CDP on ${port}: ${lastError ?? "timeout"}`);
}

async function newTarget(port) {
	const response = await fetch(`http://127.0.0.1:${port}/json/new?${encodeURIComponent("about:blank")}`, {
		method: "PUT",
	});
	if (!response.ok) {
		throw new Error(`Could not create Chrome target: ${response.status}`);
	}
	return await response.json();
}

class Cdp {
	constructor(socketUrl) {
		this.id = 0;
		this.pending = new Map();
		this.events = new Map();
		this.ws = new WebSocket(socketUrl);
		this.ready = new Promise((resolve, reject) => {
			this.ws.addEventListener("open", resolve, { once: true });
			this.ws.addEventListener("error", reject, { once: true });
		});
		this.ws.addEventListener("message", (event) => {
			const message = JSON.parse(event.data);
			if (message.id !== undefined) {
				const pending = this.pending.get(message.id);
				if (pending === undefined) {
					return;
				}
				this.pending.delete(message.id);
				if (message.error) {
					pending.reject(new Error(`${message.error.message}`));
				} else {
					pending.resolve(message.result ?? {});
				}
				return;
			}
			const handlers = this.events.get(message.method) ?? [];
			for (const handler of handlers) {
				handler(message.params ?? {});
			}
		});
	}

	on(method, handler) {
		const handlers = this.events.get(method) ?? [];
		handlers.push(handler);
		this.events.set(method, handlers);
		return () => {
			const current = this.events.get(method) ?? [];
			const next = current.filter((candidate) => candidate !== handler);
			if (next.length > 0) {
				this.events.set(method, next);
			} else {
				this.events.delete(method);
			}
		};
	}

	async send(method, params = {}) {
		await this.ready;
		const id = ++this.id;
		const promise = new Promise((resolve, reject) => {
			this.pending.set(id, { resolve, reject });
		});
		this.ws.send(JSON.stringify({ id, method, params }));
		return promise;
	}

	close() {
		this.ws.close();
	}
}

async function evaluate(cdp, expression) {
	const result = await cdp.send("Runtime.evaluate", {
		expression,
		awaitPromise: true,
		returnByValue: true,
	});
	if (result.exceptionDetails) {
		throw new Error(result.exceptionDetails.text ?? "Runtime evaluation failed");
	}
	return result.result?.value;
}

async function captureRoute(cdp, route, viewport) {
	const consoleErrors = [];
	const unsubscribeConsole = cdp.on("Runtime.consoleAPICalled", (event) => {
		if (event.type === "error") {
			consoleErrors.push(event.args?.map((arg) => arg.value ?? arg.description).join(" "));
		}
	});
	const unsubscribeException = cdp.on("Runtime.exceptionThrown", (event) => {
		consoleErrors.push(event.exceptionDetails?.text ?? "Runtime exception");
	});
	const unsubscribeLog = cdp.on("Log.entryAdded", (event) => {
		if (event.entry?.level === "error") {
			consoleErrors.push(
				event.entry.url ? `${event.entry.text} (${event.entry.url})` : event.entry.text,
			);
		}
	});

	try {
		await cdp.send("Emulation.setDeviceMetricsOverride", {
			width: viewport.width,
			height: viewport.height,
			deviceScaleFactor: 1,
			mobile: viewport.mobile,
		});
		await cdp.send("Page.navigate", { url: absolute(route.hash) });
		await waitForLoad(cdp);
		await sleep(WAIT_MS);

		if (route.commandPalette) {
			await openCommandPalette(cdp);
			await sleep(500);
		} else {
			await closeCommandPalette(cdp);
			await sleep(150);
		}

		const state = await evaluate(
			cdp,
			`(() => {
				const doc = document.documentElement;
				const scrolling = document.scrollingElement;
				const overflowing = Array.from(document.body.querySelectorAll("*"))
					.map((node) => {
						const rect = node.getBoundingClientRect();
						const style = getComputedStyle(node);
						return {
							tag: node.tagName.toLowerCase(),
							className: String(node.className || ""),
							text: String(node.textContent || "").trim().slice(0, 80),
							left: Math.round(rect.left),
							right: Math.round(rect.right),
							width: Math.round(rect.width),
							position: style.position,
						};
					})
					.filter((item) => item.width > 0 && item.left < window.innerWidth && item.right > window.innerWidth + 1)
					.slice(0, 10);
				return {
					url: location.href,
					title: document.title,
					dataset: {
						variant: doc.dataset.variant || "",
						theme: doc.dataset.theme || "",
						density: doc.dataset.density || "",
						libraryLayout: doc.dataset.libraryLayout || "",
					},
					tweaksPanelPresent: Boolean(document.querySelector(".tweaks-panel")),
					libraryContentCount: document.querySelectorAll(".lib-table tbody tr, .feed-item, .lib-cards .card").length,
					libraryServiceError: Boolean(document.querySelector(".library-state.error-state, .service-error")),
					libraryEmpty: Boolean(document.querySelector(".library-state.empty-state")),
					transcriptTitleLength: document.querySelector(".detail-h1")?.textContent?.trim().length || 0,
					transcriptBodyLength: document.querySelector(".transcript-body")?.textContent?.trim().length || 0,
					transcriptUnavailable: Boolean(document.querySelector(".failure-row .err-title, .transcript-unavailable")),
					bodyScrollWidth: scrolling?.scrollWidth ?? 0,
					innerWidth: window.innerWidth,
					horizontalOverflow: (scrolling?.scrollWidth ?? 0) > window.innerWidth + 1,
					overflowing,
					commandPaletteOpen: Boolean(document.querySelector(".cmdk-modal")),
				};
			})()`,
		);
		const screenshot = await cdp.send("Page.captureScreenshot", {
			format: "png",
			fromSurface: true,
			captureBeyondViewport: false,
		});
		return { state, consoleErrors, screenshot: screenshot.data };
	} finally {
		unsubscribeConsole();
		unsubscribeException();
		unsubscribeLog();
	}
}

async function closeCommandPalette(cdp) {
	await cdp.send("Input.dispatchKeyEvent", {
		type: "rawKeyDown",
		key: "Escape",
		code: "Escape",
		windowsVirtualKeyCode: 27,
	});
	await cdp.send("Input.dispatchKeyEvent", {
		type: "keyUp",
		key: "Escape",
		code: "Escape",
		windowsVirtualKeyCode: 27,
	});
}

async function openCommandPalette(cdp) {
	await cdp.send("Input.dispatchKeyEvent", {
		type: "rawKeyDown",
		key: "Control",
		code: "ControlLeft",
		windowsVirtualKeyCode: 17,
		modifiers: 2,
	});
	await cdp.send("Input.dispatchKeyEvent", {
		type: "keyDown",
		key: "k",
		code: "KeyK",
		windowsVirtualKeyCode: 75,
		modifiers: 2,
	});
	await cdp.send("Input.dispatchKeyEvent", {
		type: "keyUp",
		key: "k",
		code: "KeyK",
		windowsVirtualKeyCode: 75,
		modifiers: 2,
	});
	await cdp.send("Input.dispatchKeyEvent", {
		type: "keyUp",
		key: "Control",
		code: "ControlLeft",
		windowsVirtualKeyCode: 17,
	});
}

async function clickSettingsButton(cdp, rowLabel, value) {
	return await evaluate(
		cdp,
		`(() => {
			const rows = Array.from(document.querySelectorAll(".settings-row"));
			const row = rows.find((candidate) => candidate.querySelector(".row-label")?.textContent?.trim() === ${JSON.stringify(rowLabel)});
			if (!row) return false;
			row.scrollIntoView({ block: "center" });
			const button = Array.from(row.querySelectorAll("button")).find((candidate) => candidate.textContent?.trim() === ${JSON.stringify(value)});
			if (!button) return false;
			button.click();
			return true;
		})()`,
	);
}

async function smokeVariantMatrix(cdp) {
	await cdp.send("Emulation.setDeviceMetricsOverride", {
		width: 1440,
		height: 1000,
		deviceScaleFactor: 1,
		mobile: false,
	});
	await cdp.send("Page.navigate", { url: absolute("#/settings") });
	await waitForLoad(cdp);
	await sleep(WAIT_MS);
	await closeCommandPalette(cdp);

	const results = [];
	for (const variant of VARIANTS) {
		for (const theme of THEMES) {
			for (const density of DENSITIES) {
				for (const libraryLayout of LIBRARY_LAYOUTS) {
					const clicks = [
						await clickSettingsButton(cdp, "Visual variant", variant),
					];
					await sleep(30);
					clicks.push(await clickSettingsButton(cdp, "Theme", theme === "light" ? "Light" : "Dark"));
					await sleep(30);
					clicks.push(await clickSettingsButton(cdp, "Density", density));
					await sleep(30);
					clicks.push(await clickSettingsButton(cdp, "Library default layout", libraryLayout === "table" ? "Table" : libraryLayout === "feed" ? "Feed" : "Cards"));
					await sleep(80);
					const settingsState = await evaluate(
						cdp,
						`(() => {
							const doc = document.documentElement;
							const scrolling = document.scrollingElement;
							const appearance = Array.from(document.querySelectorAll(".settings-group")).find((group) => group.querySelector("h2")?.textContent?.trim() === "Appearance");
							const activeButtons = Array.from(appearance?.querySelectorAll(".seg button[aria-pressed='true']") || []).map((button) => button.textContent?.trim());
							const controlsRect = appearance?.getBoundingClientRect();
							return {
								dataset: {
									variant: doc.dataset.variant || "",
									theme: doc.dataset.theme || "",
									density: doc.dataset.density || "",
									libraryLayout: doc.dataset.libraryLayout || "",
								},
								activeButtons,
								tweaksPanelPresent: Boolean(document.querySelector(".tweaks-panel")),
								controlsReachable: Boolean(controlsRect && controlsRect.width > 0 && controlsRect.height > 0 && controlsRect.left < window.innerWidth && controlsRect.top < window.innerHeight && controlsRect.right > 0 && controlsRect.bottom > 0),
								horizontalOverflow: (scrolling?.scrollWidth ?? 0) > window.innerWidth + 1,
							};
						})()`,
					);
					await cdp.send("Page.navigate", { url: absolute("#/library") });
					await waitForLoad(cdp);
					await sleep(600);
					const libraryState = await evaluate(
						cdp,
						`(() => {
							const scrolling = document.scrollingElement;
							const layoutSelector = ${JSON.stringify(libraryLayout === "table" ? ".lib-table" : libraryLayout === "feed" ? ".lib-feed" : ".lib-cards")};
							return {
								layoutVisible: Boolean(document.querySelector(layoutSelector)),
								libraryContentCount: document.querySelectorAll(".lib-table tbody tr, .feed-item, .lib-cards .card").length,
								libraryServiceError: Boolean(document.querySelector(".library-state.error-state, .service-error")),
								libraryEmpty: Boolean(document.querySelector(".library-state.empty-state")),
								horizontalOverflow: (scrolling?.scrollWidth ?? 0) > window.innerWidth + 1,
							};
						})()`,
					);
					results.push({ variant, theme, density, libraryLayout, clicks, state: { ...settingsState, ...libraryState } });
					await cdp.send("Page.navigate", { url: absolute("#/settings") });
					await waitForLoad(cdp);
					await sleep(300);
				}
			}
		}
	}
	return results;
}

async function waitForLoad(cdp) {
	for (let i = 0; i < 80; i += 1) {
		const ready = await evaluate(cdp, "document.readyState");
		if (ready === "complete") {
			return;
		}
		await sleep(100);
	}
	const url = await evaluate(cdp, "location.href").catch(() => "unknown URL");
	console.warn(`Timed out waiting for document.readyState=complete on ${url}`);
}

async function main() {
	await mkdir(OUT_DIR, { recursive: true });
	const routes = await discoverRoutes();
	const missing = [];
	if (!routes.some((route) => route.key.startsWith("transcript-"))) {
		missing.push("transcript detail");
	}
	if (!routes.some((route) => route.key.startsWith("job-"))) {
		missing.push("job detail");
	}

	const port = 9300 + Math.floor(Math.random() * 400);
	const profile = join(tmpdir(), `scribe-visual-qa-${process.pid}`);
	const chrome = spawn(CHROME, [
		"--headless=new",
		"--disable-gpu",
		"--no-first-run",
		"--no-default-browser-check",
		`--remote-debugging-port=${port}`,
		`--user-data-dir=${profile}`,
		"about:blank",
	], { stdio: ["ignore", "ignore", "pipe"] });
	chrome.stderr.on("data", () => {});

	try {
		await waitForVersion(port);
		const target = await newTarget(port);
		const cdp = new Cdp(target.webSocketDebuggerUrl);
		await cdp.send("Page.enable");
		await cdp.send("Runtime.enable");
		await cdp.send("Log.enable");

		const manifest = {
			baseUrl: BASE_URL,
			capturedAt: new Date().toISOString(),
			missing,
			routes: [],
			variantMatrix: [],
		};

		for (const viewport of WIDTHS) {
			for (const route of routes) {
				const result = await captureRoute(cdp, route, viewport);
				const filename = `${viewport.name}-${route.key}.png`;
				await writeFile(join(OUT_DIR, filename), Buffer.from(result.screenshot, "base64"));
				manifest.routes.push({
					viewport: viewport.name,
					route: route.hash,
					key: route.key,
					screenshot: filename,
					consoleErrors: result.consoleErrors.filter(Boolean),
					state: result.state,
				});
				console.log(`${viewport.name} ${route.key}: ${filename}`);
			}
		}
		manifest.variantMatrix = await smokeVariantMatrix(cdp);
		console.log(`variant matrix: ${manifest.variantMatrix.length} combinations`);

		await writeFile(join(OUT_DIR, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
		cdp.close();

		const runtimeErrors = manifest.routes
			.flatMap((route) => route.consoleErrors)
			.filter((message) => !/favicon\.ico|\/jobs\/\d+/.test(message));
		const defaultRows = manifest.routes.filter((route) => route.key === "library");
		const routeTweaksPanelRows = manifest.routes.filter((route) => route.state.tweaksPanelPresent);
		const libraryContentFailures = manifest.routes.filter((route) =>
			route.key === "library" &&
			(route.state.libraryContentCount < 1 || route.state.libraryServiceError || route.state.libraryEmpty)
		);
		const transcriptContentFailures = manifest.routes.filter((route) =>
			route.key.startsWith("transcript-") &&
			(route.state.transcriptTitleLength < 1 || route.state.transcriptBodyLength < 1 || route.state.transcriptUnavailable)
		);
		const defaultMismatch = defaultRows.filter(
			(route) =>
				route.state.dataset.variant !== "field" ||
				route.state.dataset.theme !== "light" ||
				route.state.dataset.density !== "compact" ||
				route.state.dataset.libraryLayout !== "feed",
		);
		const commandPaletteMismatch = manifest.routes.filter(
			(route) => route.key === "command-palette" && !route.state.commandPaletteOpen,
		);
		const overflowRows = manifest.routes.filter((route) => route.state.horizontalOverflow);
		const variantMatrixFailures = manifest.variantMatrix.filter((row) => {
			const state = row.state;
			return (
				row.clicks.some((clicked) => !clicked) ||
				state.dataset.variant !== row.variant ||
				state.dataset.theme !== row.theme ||
				state.dataset.density !== row.density ||
				state.dataset.libraryLayout !== row.libraryLayout ||
				!state.activeButtons.includes(row.variant) ||
				!state.activeButtons.includes(row.theme === "light" ? "Light" : "Dark") ||
				!state.activeButtons.includes(row.density) ||
				!state.activeButtons.includes(row.libraryLayout === "table" ? "Table" : row.libraryLayout === "feed" ? "Feed" : "Cards") ||
				state.tweaksPanelPresent ||
				state.libraryContentCount < 1 ||
				state.libraryServiceError ||
				state.libraryEmpty ||
				!state.layoutVisible ||
				!state.controlsReachable ||
				state.horizontalOverflow
			);
		});

		if (
			missing.length > 0 ||
			runtimeErrors.length > 0 ||
			routeTweaksPanelRows.length > 0 ||
			libraryContentFailures.length > 0 ||
			transcriptContentFailures.length > 0 ||
			defaultMismatch.length > 0 ||
			commandPaletteMismatch.length > 0 ||
			overflowRows.length > 0 ||
			variantMatrixFailures.length > 0
		) {
			console.error(
				JSON.stringify(
					{
						missing,
						runtimeErrors,
						routeTweaksPanelRows,
						libraryContentFailures,
						transcriptContentFailures,
						defaultMismatch,
						commandPaletteMismatch,
						overflowRows,
						variantMatrixFailures,
					},
					null,
					2,
				),
			);
			process.exitCode = 1;
		}
	} finally {
		chrome.kill();
		await rm(profile, { recursive: true, force: true });
	}
}

main().catch((error) => {
	console.error(error);
	process.exit(1);
});
