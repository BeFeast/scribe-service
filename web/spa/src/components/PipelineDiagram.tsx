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

const STAGES: Array<{ key: string; label: string; sublabel: string }> = [
	{ key: "queued", label: "Queue", sublabel: "Waiting for a worker slot" },
	{ key: "downloading", label: "Download", sublabel: "Fetching source media" },
	{ key: "transcribing", label: "Transcribe", sublabel: "Worker audio pass" },
	{ key: "summarizing", label: "Summarize", sublabel: "Summary generation" },
	{ key: "done", label: "Publish", sublabel: "Transcript and hooks" },
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

function stateGlyph(state: StageState) {
	if (state === "active") {
		return <span className="spinner" aria-hidden="true" />;
	}
	if (state === "done") {
		return (
			<span className="status-glyph" aria-hidden="true">
				✓
			</span>
		);
	}
	if (state === "failed") {
		return (
			<span className="status-glyph" aria-hidden="true">
				✗
			</span>
		);
	}
	return (
		<span className="status-glyph" aria-hidden="true">
			○
		</span>
	);
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
					view?.progress !== null && view?.progress !== undefined
						? view.progress * 100
						: null;
				return (
					<div className={`stage ${state}`} key={stage.key}>
						<div className="stage-num">stage {index + 1}</div>
						<div className="stage-name">
							{stateGlyph(state)}
							<span>{stage.label}</span>
						</div>
						{compact ? null : (
							<>
								<div className="stage-sub">{stage.sublabel}</div>
								<div className="stage-state">{stageMeta(view)}</div>
								{view?.note ? (
									<div className="stage-note">{view.note}</div>
								) : null}
							</>
						)}
						{state === "active" && progress !== null ? (
							<div className="progressbar" aria-hidden="true">
								<span style={{ width: `${progress}%` }} />
							</div>
						) : null}
						{compact ? (
							<span className="stage-state">{stageMeta(view)}</span>
						) : null}
					</div>
				);
			})}
		</div>
	);
}
