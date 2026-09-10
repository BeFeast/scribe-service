export function summaryPresentation(row) {
	const state = row.summary_state;
	if (state === "generating")
		return {
			label: "Summarizing…",
			text: "Transcript saved. Your summary is being generated and will appear automatically.",
			retry: false,
		};
	if (state === "failed")
		return {
			label: "Summary failed",
			text: "Transcript saved. Open this transcript to retry the summary.",
			retry: true,
		};
	if (state === "not_requested")
		return {
			label: "Transcript only",
			text: "Transcript saved. A summary was not requested.",
			retry: true,
		};
	return {
		label: "Summary unavailable",
		text: "Transcript saved. Open this transcript to generate a summary.",
		retry: true,
	};
}
