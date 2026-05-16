export type StageState = "pending" | "active" | "done" | "failed";

export type StageView = {
	state: StageState;
	started_at?: string | null;
	finished_at?: string | null;
	duration_s?: number | null;
	progress?: number | null;
	note?: string | null;
};

export type StageMap = Record<string, StageView>;

const STAGES: Array<{ key: string; label: string }> = [
	{ key: "queued", label: "Queue" },
	{ key: "downloading", label: "Download" },
	{ key: "transcribing", label: "Whisper" },
	{ key: "summarizing", label: "Summarize" },
	{ key: "done", label: "Done" },
];

type PipelineDiagramProps = {
	stages?: StageMap | null;
	compact?: boolean;
};

function formatDuration(seconds?: number | null) {
	if (seconds === null || seconds === undefined) {
		return null;
	}
	if (seconds < 60) {
		return `${seconds}s`;
	}
	const minutes = Math.floor(seconds / 60);
	const rest = seconds % 60;
	return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`;
}

function stageMeta(stage?: StageView) {
	if (!stage) {
		return "waiting";
	}
	const duration = formatDuration(stage.duration_s);
	if (duration) {
		return duration;
	}
	if (stage.state === "active") {
		return "running";
	}
	if (stage.state === "failed") {
		return "failed";
	}
	return stage.state;
}

export function PipelineDiagram({
	stages,
	compact = false,
}: PipelineDiagramProps) {
	return (
		<div className={compact ? "pipeline compact" : "pipeline"}>
			{STAGES.map((stage, index) => {
				const view = stages?.[stage.key];
				const state = view?.state ?? "pending";
				const progress =
					state === "done"
						? 100
						: state === "active"
							? (view?.progress ?? 0) * 100
							: 0;
				return (
					<div className={`stage ${state}`} key={stage.key}>
						<div className="stage-head">
							<span className="stage-index">{index + 1}</span>
							<strong>{stage.label}</strong>
						</div>
						<span className="stage-state">{stageMeta(view)}</span>
						{compact ? null : (
							<div className="bar-track" aria-hidden="true">
								<span style={{ width: `${progress}%` }} />
							</div>
						)}
					</div>
				);
			})}
		</div>
	);
}
