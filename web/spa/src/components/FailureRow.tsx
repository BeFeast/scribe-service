import { StatusChip } from "./StatusChip";

export type FailureJob = {
	id: number;
	video_id: string;
	url: string;
	title?: string | null;
	source?: string | null;
	error?: string | null;
	failed_at: string;
};

type FailureRowProps = {
	job: FailureJob;
	onOpen: (id: number) => void;
};

function formatFailedAt(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}

export function FailureRow({ job, onOpen }: FailureRowProps) {
	return (
		<button
			type="button"
			className="failure-row"
			onClick={() => onOpen(job.id)}
		>
			<div>
				<p className="err-title">{job.title ?? job.video_id}</p>
				<p className="err-msg">{job.error ?? "job failed"}</p>
				<p className="err-meta">
					job {job.id} &middot; {job.source ?? "direct"} &middot;{" "}
					{formatFailedAt(job.failed_at)}
				</p>
			</div>
			<StatusChip status="failed" />
		</button>
	);
}
