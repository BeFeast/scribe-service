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

const STAGE_ORDER = [
	["queued", "Queue"],
	["downloading", "Download"],
	["transcribing", "Whisper"],
	["summarizing", "Summary"],
	["done", "Done"],
] as const;

function fmtDuration(seconds?: number | null): string {
	if (seconds === undefined || seconds === null) {
		return "";
	}
	if (seconds < 60) {
		return `${seconds}s`;
	}
	return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}

export function PipelineDiagram({
	stages,
	compact = false,
}: {
	stages: StageMap;
	compact?: boolean;
}) {
	return (
		<div className={compact ? "pipeline pipeline-compact" : "pipeline"}>
			{STAGE_ORDER.map(([key, label]) => {
				const stage = stages[key] ?? { state: "pending" as const };
				const progress =
					stage.state === "active"
						? Math.max(4, Math.round((stage.progress ?? 0) * 100))
						: stage.state === "done"
							? 100
							: 0;
				return (
					<div key={key} className={`stage ${stage.state}`}>
						<div className="stage-top">
							<strong>{label}</strong>
							<span>{stage.state}</span>
						</div>
						{compact ? null : (
							<p className="stage-meta">
								{stage.duration_s !== undefined && stage.duration_s !== null
									? fmtDuration(stage.duration_s)
									: stage.started_at
										? new Date(stage.started_at).toLocaleTimeString()
										: "waiting"}
							</p>
						)}
						<div className="progressbar" aria-hidden="true">
							<span style={{ width: `${progress}%` }} />
						</div>
					</div>
				);
			})}
		</div>
	);
}
