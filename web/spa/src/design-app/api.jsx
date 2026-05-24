import React from "react";
import { adaptFailure, adaptJob, adaptLibraryRow, adaptOps, adaptTranscript, adaptUsers } from "./adapters.js";

export function useScribeRuntime(auth, route) {
	const [core, setCore] = React.useState({ loading: true, error: null, transcripts: [], activeJobs: [], failures: [], stats: adaptOps(null), spendSeries: [], users: [] });
	const [currentTranscript, setCurrentTranscript] = React.useState({ loading: false, error: null, value: null });
	const [currentJob, setCurrentJob] = React.useState({ loading: false, error: null, value: null });
	const [currentJobLog, setCurrentJobLog] = React.useState({ connected: false, error: null, lines: [] });

	const refreshCore = React.useCallback(async (signal) => {
		try {
			const [library, jobs, failures, ops] = await Promise.all([
				fetchJson(auth, "/api/library?limit=100", signal),
				fetchJson(auth, "/api/jobs/active", signal),
				fetchJson(auth, "/api/jobs/recent-failures?limit=12", signal).catch(() => ({ jobs: [] })),
				fetchJson(auth, "/api/ops", signal).catch(() => null),
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
				if (!controller.signal.aborted) setCurrentJob({ loading: false, error: messageOf(error), value: null });
			}
		};
		void load();
		return () => {
			controller.abort();
			window.clearTimeout(timer);
		};
	}, [auth, route.page, route.params.id]);

	React.useEffect(() => {
		if (route.page !== "job" || route.params.id === undefined) return;
		const source = new EventSource("/api/jobs/" + route.params.id + "/log/stream");
		setCurrentJobLog({ connected: false, error: null, lines: [] });
		source.onopen = () => setCurrentJobLog((previous) => ({ ...previous, connected: true, error: null }));
		source.onmessage = (event) => {
			setCurrentJobLog((previous) => ({
				connected: true,
				error: null,
				lines: [...previous.lines, parseLogLine(event.data)].slice(-200),
			}));
		};
		source.onerror = () => {
			if (source.readyState === EventSource.CLOSED) {
				setCurrentJobLog((previous) => ({ ...previous, connected: false, error: "log stream closed" }));
			}
		};
		return () => source.close();
	}, [route.page, route.params.id]);

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
		refreshJob: async (id) => {
			const job = adaptJob(await fetchJson(auth, "/jobs/" + id));
			setCurrentJob({ loading: false, error: null, value: job });
			return job;
		},
		cancelJob: async (id) => {
			const job = adaptJob(await fetchJson(auth, "/admin/jobs/" + id + "/cancel", undefined, { method: "POST" }));
			setCurrentJob({ loading: false, error: null, value: job });
			await refreshCore();
			return job;
		},
		retryJob: async (id) => {
			const job = adaptJob(await fetchJson(auth, "/admin/jobs/" + id + "/retry", undefined, { method: "POST" }));
			setCurrentJob({ loading: false, error: null, value: job });
			await refreshCore();
			return job;
		},
	};
}

export async function fetchJson(auth, url, signal, init = {}) {
	const response = await auth.protectedFetch(url, { cache: "no-store", signal, ...init });
	if (response.status === 401 || response.status === 403) auth.maybeAutoSignIn();
	if (!response.ok) throw new Error(await responseMessage(response));
	return response.json();
}

async function responseMessage(response) {
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

function isInFlight(status) {
	return ["queued", "downloading", "transcribing", "summarizing"].includes(status);
}

function parseLogLine(raw) {
	try {
		return JSON.parse(raw);
	} catch {
		return { ts: new Date().toISOString(), lvl: "INFO", msg: raw };
	}
}
