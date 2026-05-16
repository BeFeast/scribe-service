import { PipelineDiagram, type StageMap } from "./PipelineDiagram";
import { StatusChip } from "./StatusChip";

export type ActiveJob = {
	id: number;
	video_id: string;
	url: string;
	title?: string | null;
	status: string;
	source?: string | null;
	started_at: string;
	elapsed_s: number;
	stages: StageMap;
};

export function JobCard({
	job,
	onOpen,
}: {
	job: ActiveJob;
	onOpen: (id: number) => void;
}) {
	return (
		<button
			type="button"
			className="job-card card"
			onClick={() => onOpen(job.id)}
		>
			<div className="job-card-head">
				<div>
					<p className="feed-title">{job.title ?? job.video_id}</p>
					<p className="detail-meta">
						<span className="mono">job {job.id}</span>
						<span>{job.source ?? "direct"}</span>
						<span>{Math.max(0, job.elapsed_s)}s elapsed</span>
					</p>
				</div>
				<StatusChip status={job.status} />
			</div>
			<PipelineDiagram stages={job.stages} compact />
		</button>
	);
}
