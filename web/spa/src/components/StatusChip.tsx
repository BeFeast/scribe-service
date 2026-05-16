type StatusChipProps = {
	status: string;
};

function chipClass(status: string): string {
	if (status === "done") {
		return "chip ok";
	}
	if (status === "failed") {
		return "chip err";
	}
	if (status === "queued") {
		return "chip warn";
	}
	if (["downloading", "transcribing", "summarizing"].includes(status)) {
		return "chip run";
	}
	return "chip info";
}

export function StatusChip({ status }: StatusChipProps) {
	return <span className={chipClass(status)}>{status}</span>;
}
