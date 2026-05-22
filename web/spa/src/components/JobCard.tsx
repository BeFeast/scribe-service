import { PipelineDiagram, type StageMap } from "./PipelineDiagram";
import { StatusChip } from "./StatusChip";

export type JobCardJob = {
	id: number;
	video_id: string;
	url: string;
	title?: string | null;
	status: string;
	source?: string | null;
	elapsed_s: number;
	stages: StageMap;
};

type JobCardProps = {
	job: JobCardJob;
	onOpen: (id: number) => void;
	onCancel?: (id: number) => void;
	cancelBusy?: boolean;
	cancelDisabled?: boolean;
};

function formatElapsed(seconds: number) {
	const minutes = Math.floor(seconds / 60);
	const rest = seconds % 60;
	return minutes > 0 ? `${minutes}m ${rest}s` : `${rest}s`;
}

export function JobCard({
	job,
	onOpen,
	onCancel,
	cancelBusy = false,
	cancelDisabled = false,
}: JobCardProps) {
	return (
		<div className="job-card">
			<button
				type="button"
				className="job-card-open"
				onClick={() => onOpen(job.id)}
			>
				<div className="job-card-top">
					<div>
						<h2>{job.title ?? job.video_id}</h2>
						<p className="detail-meta">
							<span>job {job.id}</span>
							<span>{job.source ?? "direct"}</span>
							<span>{formatElapsed(job.elapsed_s)}</span>
						</p>
					</div>
					<StatusChip status={job.status} />
				</div>
				<PipelineDiagram stages={job.stages} compact />
			</button>
			{onCancel ? (
				<div className="job-card-actions">
					<button
						type="button"
						className="btn ghost job-card-cancel"
						onClick={() => onCancel(job.id)}
						disabled={cancelBusy || cancelDisabled}
						aria-busy={cancelBusy || undefined}
					>
						{cancelBusy ? "Cancelling" : "Cancel"}
					</button>
				</div>
			) : null}
		</div>
	);
}
