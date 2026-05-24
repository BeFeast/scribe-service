import {
	type Route,
	handleRouteAnchorClick,
	routeToHref,
} from "../hooks/useRoute";
import { PipelineDiagram, type StageMap } from "./PipelineDiagram";
import { StatusChip } from "./StatusChip";

export type JobCardJob = {
	id: number;
	video_id: string;
	url: string;
	source_url?: string | null;
	source_label?: string | null;
	title?: string | null;
	status: string;
	source?: string | null;
	elapsed_s: number;
	stages: StageMap;
};

type JobCardProps = {
	job: JobCardJob;
	navigate: (route: Route) => void;
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
	navigate,
	onCancel,
	cancelBusy = false,
	cancelDisabled = false,
}: JobCardProps) {
	const jobRoute: Route = { page: "job", params: { id: job.id } };
	const source = job.source_label ?? job.source ?? "direct";
	return (
		<div className="job-card">
			<div className="job-card-header">
				<div className="mono muted">
					job_id <span className="soft">{job.id}</span>
				</div>
				<div className="mono muted">via {source}</div>
				<div className="spacer" />
				<StatusChip status={job.status} />
				<span className="mono muted">{formatElapsed(job.elapsed_s)}</span>
				<a
					className="btn ghost"
					href={routeToHref(jobRoute)}
					onClick={(event) => handleRouteAnchorClick(event, jobRoute, navigate)}
				>
					Open
				</a>
				{onCancel ? (
					<button
						type="button"
						className="btn ghost job-card-cancel"
						onClick={() => onCancel(job.id)}
						disabled={cancelBusy || cancelDisabled}
						aria-busy={cancelBusy || undefined}
					>
						{cancelBusy ? "Cancelling" : "Cancel"}
					</button>
				) : null}
			</div>
			<a
				className="job-card-open"
				href={routeToHref(jobRoute)}
				onClick={(event) => handleRouteAnchorClick(event, jobRoute, navigate)}
			>
				<h2>{job.title ?? job.video_id}</h2>
				<PipelineDiagram stages={job.stages} compact />
			</a>
		</div>
	);
}
