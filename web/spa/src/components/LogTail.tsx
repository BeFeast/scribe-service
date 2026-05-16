import type { StageMap } from "./PipelineDiagram";

const STAGE_LABELS: Record<string, string> = {
	queued: "accepted URL and queued job",
	downloading: "downloading media",
	transcribing: "running whisper transcription",
	summarizing: "generating summary",
	done: "pipeline completed",
};

function fmt(iso?: string | null): string {
	if (!iso) {
		return "--:--:--";
	}
	return new Date(iso).toLocaleTimeString();
}

export function buildLog({
	jobId,
	status,
	error,
	stages,
}: {
	jobId: number;
	status: string;
	error?: string | null;
	stages: StageMap;
}): string[] {
	const lines: string[] = [];
	for (const [stage, message] of Object.entries(STAGE_LABELS)) {
		const view = stages[stage];
		if (!view || view.state === "pending") {
			continue;
		}
		lines.push(
			`${fmt(view.started_at)} job=${jobId} stage=${stage} ${message}`,
		);
		if (view.state === "done" && view.finished_at) {
			lines.push(
				`${fmt(view.finished_at)} job=${jobId} stage=${stage} finished`,
			);
		}
		if (view.state === "failed") {
			lines.push(
				`${fmt(view.finished_at ?? view.started_at)} job=${jobId} stage=${stage} failed: ${
					error ?? "unknown error"
				}`,
			);
		}
	}
	if (status === "failed" && lines.every((line) => !line.includes("failed:"))) {
		lines.push(
			`${fmt()} job=${jobId} stage=failed failed: ${error ?? "unknown error"}`,
		);
	}
	return lines.length > 0
		? lines.slice(-40)
		: [`${fmt()} job=${jobId} waiting for worker`];
}

export function LogTail(props: {
	jobId: number;
	status: string;
	error?: string | null;
	stages: StageMap;
}) {
	return (
		<pre className="log-tail" aria-label="Job log tail">
			{buildLog(props).join("\n")}
		</pre>
	);
}
