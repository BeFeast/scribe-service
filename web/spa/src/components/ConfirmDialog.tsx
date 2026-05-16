import React from "react";

type ConfirmDialogProps = {
	title: string;
	body: string;
	confirmLabel: string;
	busyLabel?: string;
	busy?: boolean;
	onCancel: () => void;
	onConfirm: () => void;
};

export function ConfirmDialog({
	title,
	body,
	confirmLabel,
	busyLabel = "Working",
	busy = false,
	onCancel,
	onConfirm,
}: ConfirmDialogProps) {
	React.useEffect(() => {
		const keydown = (event: KeyboardEvent) => {
			if (event.key === "Escape" && !busy) {
				onCancel();
			}
		};
		document.addEventListener("keydown", keydown);
		return () => document.removeEventListener("keydown", keydown);
	}, [busy, onCancel]);

	return (
		<div className="modal-backdrop" role="presentation">
			<dialog className="settings-modal compact confirm-dialog" aria-modal="true" open>
				<header>
					<strong>{title}</strong>
				</header>
				<p className="hint">{body}</p>
				<div className="modal-actions">
					<button
						type="button"
						className="btn ghost"
						onClick={onCancel}
						disabled={busy}
					>
						Cancel
					</button>
					<button
						type="button"
						className="btn danger-button"
						onClick={onConfirm}
						disabled={busy}
					>
						{busy ? busyLabel : confirmLabel}
					</button>
				</div>
			</dialog>
		</div>
	);
}
