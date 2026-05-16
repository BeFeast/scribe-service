import { StatusChip } from "./StatusChip";

export function FailureRow({
	id,
	videoId,
	error,
	onRetry,
}: {
	id: number;
	videoId: string;
	error?: string | null;
	onRetry: (id: number) => void;
}) {
	return (
		<div className="failure-row">
			<div>
				<p className="err-title">{videoId}</p>
				<p className="err-msg">{error ?? "job failed"}</p>
				<p className="err-meta mono">job {id}</p>
			</div>
			<div className="row-control">
				<StatusChip status="failed" />
				<button type="button" className="btn ghost" onClick={() => onRetry(id)}>
					Retry
				</button>
			</div>
		</div>
	);
}
