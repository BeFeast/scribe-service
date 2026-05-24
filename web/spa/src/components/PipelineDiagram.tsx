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
	{
		key: "downloading",
		label: "Download",
		sublabel: "yt-dlp · residential IP",
	},
	{
		key: "transcribing",
		label: "Transcribe",
		sublabel: "faster-whisper · Vast.ai GPU",
	},
	{
		key: "summarizing",
		label: "Summarize",
		sublabel: "codex CLI · prompt template v3",
	},
	{
		key: "done",
		label: "Publish",
		sublabel: "Shortlinks · webhook · DB write",
	},
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
	const duration = formatDuration(stage?.duration_s);
	if (duration) {
		return `completed in ${duration}`;
	}
	if (stage?.state === "active") {
		return stage.progress !== null && stage.progress !== undefined
			? `${Math.round(stage.progress * 100)}%`
			: "running";
	}
	return null;
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
				const meta = stageMeta(view);
				return (
					<div className={`stage ${state}`} key={stage.key}>
						<div className="stage-num">stage {index + 1}</div>
						<div className="stage-name">
							{stateGlyph(state)}
							<span>{stage.label}</span>
						</div>
						{compact ? null : (
							<>
								<div className="mono muted stage-sub">{stage.sublabel}</div>
								{view?.note ? (
									<div className="stage-note">{view.note}</div>
								) : null}
								{meta ? (
									<div className="stage-note done-note">{meta}</div>
								) : null}
							</>
						)}
						{state === "active" && progress !== null ? (
							<div className="progressbar" aria-hidden="true">
								<div style={{ width: `${progress}%` }} />
							</div>
						) : null}
						{state === "active" && progress === null ? (
							<div className="progressbar indeterminate" aria-hidden="true">
								<div />
							</div>
						) : null}
						{compact ? (
							<span className="stage-state">{meta ?? view?.note ?? ""}</span>
						) : null}
					</div>
				);
			})}
		</div>
	);
}
