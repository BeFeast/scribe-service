type StatusChipProps = {
	status: string;
};

const STATUS: Record<string, { tone: string; label: string; glyph: string }> = {
	queued: { tone: "info", label: "queued", glyph: "○" },
	downloading: { tone: "run", label: "downloading", glyph: "↓" },
	transcribing: { tone: "run", label: "transcribing", glyph: "≋" },
	summarizing: { tone: "run", label: "summarizing", glyph: "✦" },
	done: { tone: "ok", label: "done", glyph: "✓" },
	failed: { tone: "err", label: "failed", glyph: "✗" },
};

export function StatusChip({ status }: StatusChipProps) {
	const view = STATUS[status] ?? { tone: "info", label: status, glyph: "○" };
	return (
		<span
			className={`status-chip chip ${view.tone} ${status}`}
			title={`status: ${status}`}
		>
			{view.tone === "run" ? (
				<span className="spinner" aria-hidden="true" />
			) : (
				<span className="status-glyph" aria-hidden="true">
					{view.glyph}
				</span>
			)}
			{view.label}
		</span>
	);
}
