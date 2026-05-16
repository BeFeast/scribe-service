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
	onDismiss: (id: number) => void;
	busy: boolean;
};

function formatFailedAt(value: string) {
	return new Intl.DateTimeFormat(undefined, {
		month: "short",
		day: "2-digit",
		hour: "2-digit",
		minute: "2-digit",
	}).format(new Date(value));
}

export function FailureRow({ job, onOpen, onDismiss, busy }: FailureRowProps) {
	return (
		<div className="failure-row">
			<button
				type="button"
				className="failure-action"
				onClick={() => onOpen(job.id)}
			>
				<div>
					<p className="err-title">{job.title ?? job.video_id}</p>
					<p className="err-msg">{job.error ?? "Job failed."}</p>
					<p className="err-meta">
						job {job.id} &middot; {job.source ?? "direct"} &middot;{" "}
						{formatFailedAt(job.failed_at)}
					</p>
				</div>
			</button>
			<StatusChip status="failed" />
			<button
				type="button"
				className="btn ghost"
				onClick={() => onDismiss(job.id)}
				disabled={busy}
			>
				{busy ? "Clearing" : "Clear"}
			</button>
		</div>
	);
}
