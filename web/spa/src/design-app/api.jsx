import React from "react";
import { adaptConfig, adaptFailure, adaptJob, adaptLibraryRow, adaptOps, adaptTranscript, adaptUsers } from "./adapters.js";

export function useScribeRuntime(auth, route) {
	const [core, setCore] = React.useState({ loading: true, error: null, transcripts: [], activeJobs: [], failures: [], stats: adaptOps(null), spendSeries: [], users: [], config: adaptConfig(null) });
	const [currentTranscript, setCurrentTranscript] = React.useState({ loading: false, error: null, value: null });
	const [currentJob, setCurrentJob] = React.useState({ loading: false, error: null, value: null });
	const [currentJobLog, setCurrentJobLog] = React.useState({ connected: false, error: null, lines: [] });

	const refreshCore = React.useCallback(async (signal) => {
		try {
			const [library, jobs, failures, ops, config] = await Promise.all([
				fetchJson(auth, "/api/library?limit=100", signal),
				fetchJson(auth, "/api/jobs/active", signal),
				fetchJson(auth, "/api/jobs/recent-failures?limit=12", signal).catch(() => ({ jobs: [] })),
				fetchJson(auth, "/api/ops", signal).catch(() => null),
				fetchJson(auth, "/api/config", signal).catch(() => null),
			]);
			const stats = adaptOps(ops);
			setCore((previous) => ({
				...previous,
				loading: false,
				error: null,
				transcripts: (library?.rows ?? []).map(adaptLibraryRow),
				activeJobs: (jobs?.jobs ?? []).map(adaptJob),
				failures: (failures?.jobs ?? ops?.recent_failures ?? []).map(adaptFailure),
				stats,
				spendSeries: ops?.spend_series_14d ?? [],
				config: adaptConfig(config?.config),
			}));
		} catch (error) {
			if (!signal?.aborted) {
				setCore((previous) => ({ ...previous, loading: false, error: messageOf(error) }));
			}
		}
	}, [auth]);

	React.useEffect(() => {
		const controller = new AbortController();
		void refreshCore(controller.signal);
		const timer = window.setInterval(() => {
			const tickController = new AbortController();
			void refreshCore(tickController.signal);
		}, 5000);
		return () => {
			controller.abort();
			window.clearInterval(timer);
		};
	}, [refreshCore]);

	React.useEffect(() => {
		if (route.page !== "transcript" || route.params.id === undefined) {
			setCurrentTranscript({ loading: false, error: null, value: null });
			return;
		}
		const controller = new AbortController();
		setCurrentTranscript({ loading: true, error: null, value: null });
		fetchJson(auth, "/transcripts/" + route.params.id, controller.signal)
			.then((body) => setCurrentTranscript({ loading: false, error: null, value: adaptTranscript(body) }))
			.catch((error) => {
				if (!controller.signal.aborted) setCurrentTranscript({ loading: false, error: messageOf(error), value: null });
			});
		return () => controller.abort();
	}, [auth, route.page, route.params.id]);

	React.useEffect(() => {
		if (route.page !== "job" || route.params.id === undefined) {
			setCurrentJob({ loading: false, error: null, value: null });
			setCurrentJobLog({ connected: false, error: null, lines: [] });
			return;
		}
		const controller = new AbortController();
		let timer = 0;
		setCurrentJob((previous) => ({ loading: true, error: null, value: previous.value?.id === route.params.id ? previous.value : null }));
		const load = async () => {
			try {
				const body = await fetchJson(auth, "/jobs/" + route.params.id, controller.signal);
				const job = adaptJob(body);
				setCurrentJob({ loading: false, error: null, value: job });
				if (!controller.signal.aborted && isInFlight(job.status)) {
					timer = window.setTimeout(load, 2000);
				}
			} catch (error) {
				if (!controller.signal.aborted) {
					setCurrentJob((previous) => ({ loading: false, error: messageOf(error), value: previous.value }));
					if (isTransientFetchError(error)) {
						timer = window.setTimeout(load, 2000);
					}
				}
			}
		};
		void load();
		return () => {
			controller.abort();
			window.clearTimeout(timer);
		};
	}, [auth, route.page, route.params.id]);

	React.useEffect(() => {
		if (
			route.page !== "job" ||
			route.params.id === undefined ||
			currentJob.loading ||
			currentJob.error ||
			currentJob.value?.id !== route.params.id
		) {
			setCurrentJobLog({ connected: false, error: null, lines: [] });
			return;
		}
		const controller = new AbortController();
		setCurrentJobLog({ connected: false, error: null, lines: [] });
		void streamJobLog(auth, route.params.id, controller.signal, (line) => {
			setCurrentJobLog((previous) => ({
				connected: true,
				error: null,
				lines: [...previous.lines, line].slice(-200),
			}));
		})
			.then(() => {
				if (!controller.signal.aborted) {
					setCurrentJobLog((previous) => ({ ...previous, connected: false }));
				}
			})
			.catch((error) => {
				if (!controller.signal.aborted) {
					setCurrentJobLog((previous) => ({ ...previous, connected: false, error: messageOf(error) }));
				}
			});
		return () => controller.abort();
	}, [auth, route.page, route.params.id, currentJob.loading, currentJob.error, currentJob.value?.id]);

	React.useEffect(() => {
		if (route.page !== "settings") return;
		const controller = new AbortController();
		let me = null;
		fetchJson(auth, "/api/auth/me", controller.signal)
			.then((body) => {
				me = body;
				if (body?.role !== "admin") return [];
				return fetchJson(auth, "/api/admin/users", controller.signal);
			})
			.then((users) => {
				if (!controller.signal.aborted) setCore((previous) => ({ ...previous, users: adaptUsers(me, users) }));
			})
			.catch(() => {});
		return () => controller.abort();
	}, [auth, route.page]);

	return {
		...core,
		currentTranscript,
		currentJob,
		currentJobLog,
		refreshCore: () => {
			const controller = new AbortController();
			void refreshCore(controller.signal);
		},
		refreshJob: async (id, signal) => {
			const job = adaptJob(await fetchJson(auth, "/jobs/" + id, signal));
			if (!signal?.aborted) setCurrentJob({ loading: false, error: null, value: job });
			return job;
		},
		cancelJob: async (id, signal) => {
			const job = adaptJob(await fetchJson(auth, "/admin/jobs/" + id + "/cancel", signal, { method: "POST" }));
			if (!signal?.aborted) setCurrentJob({ loading: false, error: null, value: job });
			await refreshCore(new AbortController().signal);
			return job;
		},
		retryJob: async (id, signal) => {
			const job = adaptJob(await fetchJson(auth, "/admin/jobs/" + id + "/retry", signal, { method: "POST" }));
			if (!signal?.aborted) setCurrentJob({ loading: false, error: null, value: job });
			await refreshCore(new AbortController().signal);
			return job;
		},
		applyConfig: (config) => {
			setCore((previous) => ({ ...previous, config: adaptConfig(config) }));
		},
	};
}

export async function fetchJson(auth, url, signal, init = {}) {
	const response = await auth.protectedFetch(url, { cache: "no-store", signal, ...init });
	if (response.status === 401 || response.status === 403) auth.maybeAutoSignIn();
	if (!response.ok) throw new HttpError(response.status, await responseMessage(response));
	return response.json();
}

async function streamJobLog(auth, id, signal, onLine) {
	const response = await auth.protectedFetch("/api/jobs/" + id + "/log/stream", { cache: "no-store", signal });
	if (response.status === 401 || response.status === 403) auth.maybeAutoSignIn();
	if (!response.ok) throw new HttpError(response.status, await responseMessage(response));
	if (!response.body) throw new Error("log stream unavailable");

	const reader = response.body.getReader();
	const decoder = new TextDecoder();
	let buffer = "";
	while (!signal.aborted) {
		const { value, done } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		const events = buffer.split("\n\n");
		buffer = events.pop() ?? "";
		for (const event of events) {
			for (const line of event.split("\n")) {
				if (line.startsWith("data:")) onLine(parseLogLine(line.slice(5).trimStart()));
			}
		}
	}
}

class HttpError extends Error {
	constructor(status, message) {
		super(message);
		this.status = status;
	}
}

export async function responseMessage(response) {
	try {
		const body = await response.json();
		if (typeof body?.detail === "string") return body.detail;
		if (Array.isArray(body?.detail)) return body.detail.map((entry) => entry?.msg ?? JSON.stringify(entry)).join("; ");
	} catch {}
	return ("HTTP " + response.status + " " + response.statusText).trim();
}

function messageOf(error) {
	return error instanceof Error ? error.message : String(error);
}

function isTransientFetchError(error) {
	return error instanceof HttpError && (error.status === 408 || error.status === 429 || error.status >= 500);
}

export function isInFlight(status) {
	return ["queued", "downloading", "transcribing", "summarizing"].includes(status);
}

function parseLogLine(raw) {
	try {
		return JSON.parse(raw);
	} catch {
		return { ts: new Date().toISOString(), lvl: "INFO", msg: raw };
	}
}
