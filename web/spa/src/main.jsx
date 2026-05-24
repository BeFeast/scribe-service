import React from "react";
import { createRoot } from "react-dom/client";
import { useScribeRuntime } from "./design-app/api.jsx";
import { CommandPalette } from "./design-app/command-palette.jsx";
import { setRuntimeData } from "./design-app/data.js";
import { JobDetail, QueuePage } from "./design-app/job-pages.jsx";
import { LibraryPage } from "./design-app/library.jsx";
import { OpsPage } from "./design-app/ops.jsx";
import { SettingsPage } from "./design-app/settings.jsx";
import { Sidebar, TopBar } from "./design-app/shell.jsx";
import { TranscriptDetail } from "./design-app/transcript-detail.jsx";
import { AuthProvider, useAuth } from "./hooks/useAuth";
import { useRoute } from "./hooks/useRoute";
import { DEFAULT_TWEAKS, useTweaks } from "./hooks/useTweaks";
import "./styles.css";

function ScribeRoot() {
	return (
		<AuthProvider>
			<ScribeApp />
		</AuthProvider>
	);
}

function ScribeApp() {
	const auth = useAuth();
	const { route, navigate } = useRoute();
	const { tweaks, replaceTweaks } = useTweaks();
	const [cmdkOpen, setCmdkOpen] = React.useState(false);
	const runtime = useScribeRuntime(auth, route);
	const t = React.useMemo(() => ({ ...DEFAULT_TWEAKS, ...tweaks }), [tweaks]);

	setRuntimeData({
		transcripts: runtime.transcripts,
		activeJobs: runtime.activeJobs,
		failures: runtime.failures,
		stats: runtime.stats,
		spendSeries: runtime.spendSeries,
		users: runtime.users,
		currentTranscript: runtime.currentTranscript.value,
		currentTranscriptState: {
			loading: runtime.currentTranscript.loading,
			error: runtime.currentTranscript.error,
		},
		currentJob: runtime.currentJob.value,
		currentJobState: {
			loading: runtime.currentJob.loading,
			error: runtime.currentJob.error,
		},
		currentJobLog: runtime.currentJobLog,
		config: runtime.config,
	});

	const setTweak = React.useCallback(
		(key, value) => replaceTweaks({ ...t, [key]: value }),
		[replaceTweaks, t],
	);
	const navigateDesign = React.useCallback(
		(page, params = {}) => {
			if (params.openCmdk) {
				setCmdkOpen(true);
				return;
			}
			if (!page) return;
			navigate({ page, params });
		},
		[navigate],
	);

	React.useEffect(() => {
		function onKey(event) {
			const key = event.key?.toLowerCase?.() ?? "";
			if (
				(event.metaKey || event.ctrlKey) &&
				(key === "k" || event.code === "KeyK")
			) {
				event.preventDefault();
				setCmdkOpen((open) => !open);
			}
		}
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, []);

	let page = null;
	switch (route.page) {
		case "transcript":
			page = (
				<TranscriptDetail
					id={route.params.id}
					navigate={navigateDesign}
					onRefresh={runtime.refreshCore}
				/>
			);
			break;
		case "queue":
			page = (
				<QueuePage
					navigate={navigateDesign}
					loading={runtime.loading}
					error={runtime.error}
					onRefresh={runtime.refreshCore}
					onRetryJob={async (id) => {
						const job = await runtime.retryJob(id);
						navigateDesign("job", { id: job.id });
						return job;
					}}
				/>
			);
			break;
		case "job":
			page = (
				<JobDetail
					id={route.params.id}
					navigate={navigateDesign}
					log={runtime.currentJobLog}
					onRefresh={runtime.refreshJob}
					onCancelJob={runtime.cancelJob}
					onRetryJob={async (id) => {
						const job = await runtime.retryJob(id);
						navigateDesign("job", { id: job.id });
						return job;
					}}
				/>
			);
			break;
		case "ops":
			page = (
				<OpsPage
					navigate={navigateDesign}
					loading={runtime.loading}
					error={runtime.error}
					onRefresh={runtime.refreshCore}
					onRetryJob={async (id) => {
						const job = await runtime.retryJob(id);
						navigateDesign("job", { id: job.id });
						return job;
					}}
				/>
			);
			break;
		case "settings":
			page = (
				<SettingsPage
					t={t}
					setTweak={setTweak}
					users={runtime.users}
					onConfigSaved={runtime.applyConfig}
				/>
			);
			break;
		default:
			page = (
				<LibraryPage
					navigate={navigateDesign}
					t={t}
					setTweak={setTweak}
					routeTag={route.params.tag}
					loading={runtime.loading}
					error={runtime.error}
				/>
			);
	}

	return (
		<div className="app">
			<TopBar onOpenCmdk={() => setCmdkOpen(true)} t={t} setTweak={setTweak} />
			<Sidebar page={route.page} navigate={navigateDesign} />
			<main className="main" data-screen-label={route.page}>
				{page}
			</main>
			<CommandPalette
				open={cmdkOpen}
				onClose={() => setCmdkOpen(false)}
				navigate={navigateDesign}
			/>
		</div>
	);
}

createRoot(document.getElementById("root")).render(<ScribeRoot />);
