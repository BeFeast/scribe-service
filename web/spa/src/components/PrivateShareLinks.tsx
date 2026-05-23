import React from "react";

import { useAuth } from "../hooks/useAuth";
import { type ShareTarget, transcriptShareTargets } from "../shareTargets";

type CopyState = {
	key: string;
	message: string;
	status: "copied" | "error";
};

type ManagedShareLink = {
	id: number;
	target_kind: "page" | "summary_markdown" | "transcript_markdown";
	token_hint: string;
	label?: string | null;
	recipient_note?: string | null;
	revoked_at?: string | null;
	expires_at?: string | null;
};

type PrivateShareLinksProps = {
	id: number;
	copyKinds?: Set<ShareTarget["kind"]>;
	targetKinds?: Set<ShareTarget["kind"]>;
};

const targetKindByUiKind: Record<
	ShareTarget["kind"],
	ManagedShareLink["target_kind"]
> = {
	page: "page",
	summary: "summary_markdown",
	transcript: "transcript_markdown",
};

function labelForTargetKind(kind: ManagedShareLink["target_kind"]): string {
	switch (kind) {
		case "summary_markdown":
			return "Summary .md";
		case "transcript_markdown":
			return "Transcript .md";
		default:
			return "Page";
	}
}

export async function copyTextToClipboard(text: string): Promise<boolean> {
	try {
		if (navigator.clipboard?.writeText !== undefined) {
			await navigator.clipboard.writeText(text);
			return true;
		}
	} catch {
		// Fall through to the textarea fallback for HTTP origins or denied permissions.
	}

	const textarea = document.createElement("textarea");
	textarea.value = text;
	textarea.setAttribute("readonly", "");
	textarea.style.position = "fixed";
	textarea.style.top = "0";
	textarea.style.left = "0";
	textarea.style.opacity = "0";
	document.body.appendChild(textarea);
	textarea.focus();
	textarea.select();

	try {
		return document.execCommand("copy");
	} catch {
		return false;
	} finally {
		document.body.removeChild(textarea);
	}
}

export function PrivateShareLinks({
	id,
	copyKinds,
	targetKinds,
}: PrivateShareLinksProps) {
	const auth = useAuth();
	const panelRef = React.useRef<HTMLDetailsElement>(null);
	const [copyState, setCopyState] = React.useState<CopyState | null>(null);
	const [links, setLinks] = React.useState<ManagedShareLink[]>([]);
	const [busyKind, setBusyKind] = React.useState<string | null>(null);
	const targets = transcriptShareTargets(id).filter(
		(target) => targetKinds === undefined || targetKinds.has(target.kind),
	);
	const activeLinks = links.filter((link) => link.revoked_at == null);

	const loadLinks = React.useCallback(async () => {
		const response = await auth.protectedFetch(
			`/api/transcripts/${id}/share-links`,
			{ cache: "no-store" },
		);
		if (response.ok) {
			setLinks((await response.json()) as ManagedShareLink[]);
		}
	}, [auth, id]);

	React.useEffect(() => {
		loadLinks().catch(() => setLinks([]));
	}, [loadLinks]);

	React.useEffect(() => {
		if (copyState === null) {
			return undefined;
		}
		const timeout = window.setTimeout(() => setCopyState(null), 4500);
		return () => window.clearTimeout(timeout);
	}, [copyState]);

	React.useEffect(() => {
		function closeOnOutsideClick(event: MouseEvent) {
			const panel = panelRef.current;
			if (panel !== null && !panel.contains(event.target as Node)) {
				panel.removeAttribute("open");
			}
		}

		document.addEventListener("click", closeOnOutsideClick);
		return () => document.removeEventListener("click", closeOnOutsideClick);
	}, []);

	async function createAndCopy(target: ShareTarget) {
		setBusyKind(target.kind);
		try {
			const response = await auth.protectedFetch(
				`/api/transcripts/${id}/share-links`,
				{
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify({
						target_kind: targetKindByUiKind[target.kind],
						label: target.label,
					}),
				},
			);
			if (!response.ok) {
				throw new Error(`HTTP ${response.status}`);
			}
			const created = (await response.json()) as ManagedShareLink & {
				share_url: string;
			};
			const copied = await copyTextToClipboard(created.share_url);
			if (copied) {
				setCopyState({
					key: target.kind,
					message: "Copied",
					status: "copied",
				});
			} else {
				setCopyState({
					key: target.kind,
					message: "Link created. Allow clipboard access, then try again.",
					status: "error",
				});
			}
			await loadLinks();
		} catch {
			setCopyState({
				key: target.kind,
				message: "Could not create link. Try again.",
				status: "error",
			});
		} finally {
			setBusyKind(null);
		}
	}

	async function revoke(link: ManagedShareLink) {
		setBusyKind(`revoke:${link.id}`);
		try {
			const response = await auth.protectedFetch(
				`/api/share-links/${link.id}/revoke`,
				{ method: "POST" },
			);
			if (!response.ok) {
				throw new Error(`HTTP ${response.status}`);
			}
			await loadLinks();
		} catch {
			setCopyState({
				key: `revoke:${link.id}`,
				message: "Revoke failed. Try again.",
				status: "error",
			});
		} finally {
			setBusyKind(null);
		}
	}

	return (
		<details className="private-share" ref={panelRef}>
			<summary className="btn ghost">Share</summary>
			<div className="private-share-panel">
				<div className="share-menu-section">
					<span className="share-menu-heading">Create link</span>
					{targets.map((target) => {
						const canCopy =
							copyKinds === undefined || copyKinds.has(target.kind);
						const state =
							copyState?.key === target.kind ? copyState : undefined;
						return (
							<div className="share-menu-row" key={target.kind}>
								<button
									type="button"
									className="btn ghost"
									onClick={() => void createAndCopy(target)}
									disabled={!canCopy || busyKind !== null}
								>
									{busyKind === target.kind ? "Creating" : target.label}
								</button>
								{state !== undefined ? (
									<output
										className={
											state.status === "copied"
												? "copy-state ok"
												: "copy-state err"
										}
									>
										{state.message}
									</output>
								) : null}
							</div>
						);
					})}
				</div>
				<div className="share-menu-section">
					<span className="share-menu-heading">Active links</span>
					{activeLinks.length === 0 ? (
						<span className="copy-state">None yet</span>
					) : null}
					{activeLinks.map((link) => (
						<div className="share-menu-row active-link" key={link.id}>
							<span className="copy-state">
								{link.label ?? labelForTargetKind(link.target_kind)} ...
								{link.token_hint}
							</span>
							<button
								type="button"
								className="btn ghost"
								onClick={() => void revoke(link)}
								disabled={busyKind !== null}
							>
								{busyKind === `revoke:${link.id}` ? "Revoking" : "Revoke"}
							</button>
							{copyState?.key === `revoke:${link.id}` &&
							copyState.status === "error" ? (
								<output className="copy-state err">{copyState.message}</output>
							) : null}
						</div>
					))}
				</div>
			</div>
		</details>
	);
}
