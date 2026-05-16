import type { StageMap } from "./PipelineDiagram";

type LogTailProps = {
	jobId: number;
	status: string;
	error?: string | null;
	stages?: StageMap | null;
};

const STAGE_LABELS: Record<string, string> = {
	queued: "queue",
	downloading: "download",
	transcribing: "whisper",
	summarizing: "summary",
	done: "done",
};

function formatStamp(value?: string | null) {
	if (!value) {
		return "--:--:--";
	}
	return new Intl.DateTimeFormat(undefined, {
		hour: "2-digit",
		minute: "2-digit",
		second: "2-digit",
	}).format(new Date(value));
}

function buildLog(
	jobId: number,
	status: string,
	stages?: StageMap | null,
	error?: string | null,
) {
	const lines: string[] = [];
	for (const [key, label] of Object.entries(STAGE_LABELS)) {
		const stage = stages?.[key];
		if (!stage || stage.state === "pending") {
			continue;
		}
		lines.push(
			`${formatStamp(stage.started_at)} job=${jobId} stage=${label} start`,
		);
		if (stage.state === "done") {
			lines.push(
				`${formatStamp(stage.finished_at)} job=${jobId} stage=${label} ok`,
			);
		}
		if (stage.state === "failed") {
			lines.push(
				`${formatStamp(stage.finished_at)} job=${jobId} stage=${label} failed ${error ?? ""}`.trim(),
			);
		}
	}
	if (status === "failed" && lines.length === 0) {
		lines.push(
			`${formatStamp(null)} job=${jobId} stage=job failed ${error ?? ""}`.trim(),
		);
	}
	if (status === "done") {
		lines.push(
			`${formatStamp(stages?.done?.finished_at)} job=${jobId} pipeline complete`,
		);
	}
	return lines.length > 0
		? lines
		: [`${formatStamp(null)} job=${jobId} waiting for worker`];
}

export function LogTail({ jobId, status, error, stages }: LogTailProps) {
	return (
		<section className="log-tail" aria-label="Log tail">
			<div className="section-heading">
				<h2>Log tail</h2>
				<span className="mock-chip">synthetic</span>
			</div>
			<pre>
				{buildLog(jobId, status, stages, error).map((line) => (
					<code key={line}>{line}</code>
				))}
			</pre>
		</section>
	);
}
