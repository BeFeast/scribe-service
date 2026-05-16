type StatusChipProps = {
	status: string;
};

const LABELS: Record<string, string> = {
	queued: "Queued",
	downloading: "Downloading",
	transcribing: "Transcribing",
	summarizing: "Summarizing",
	done: "Done",
	failed: "Failed",
};

export function StatusChip({ status }: StatusChipProps) {
	return (
		<span className={`status-chip ${status}`} title={`status: ${status}`}>
			{LABELS[status] ?? status}
		</span>
	);
}
