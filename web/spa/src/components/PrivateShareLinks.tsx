import React from "react";

import {
	type ShareTarget,
	transcriptShareTargets,
} from "../shareTargets";
import { useAuth } from "../hooks/useAuth";

type CopyState = {
	key: string;
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

export function PrivateShareLinks({
	id,
	copyKinds,
	targetKinds,
}: PrivateShareLinksProps) {
	const auth = useAuth();
	const [copyState, setCopyState] = React.useState<CopyState | null>(null);
	const [links, setLinks] = React.useState<ManagedShareLink[]>([]);
	const [busyKind, setBusyKind] = React.useState<string | null>(null);
	const targets = transcriptShareTargets(id).filter(
		(target) => targetKinds === undefined || targetKinds.has(target.kind),
	);

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
			await navigator.clipboard.writeText(created.share_url);
			setCopyState({ key: target.kind, status: "copied" });
			await loadLinks();
		} catch {
			setCopyState({ key: target.kind, status: "error" });
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
		} finally {
			setBusyKind(null);
		}
	}

	return (
		<div className="share-targets">
			{targets.map((target) => {
				const canCopy = copyKinds === undefined || copyKinds.has(target.kind);
				const state =
					copyState?.key === target.kind ? copyState.status : undefined;
				return (
					<div className="share-target" key={target.kind}>
						<button
							type="button"
							className="btn ghost"
							onClick={() => void createAndCopy(target)}
							disabled={!canCopy || busyKind !== null}
						>
							{busyKind === target.kind ? "Creating" : target.label}
						</button>
						{state !== undefined ? (
							<span
								className={
									state === "copied" ? "copy-state ok" : "copy-state err"
								}
							>
								{state === "copied" ? "Copied" : "Copy failed"}
							</span>
						) : null}
					</div>
				);
			})}
			{links
				.filter((link) => link.revoked_at == null)
				.map((link) => (
					<div className="share-target" key={link.id}>
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
					</div>
				))}
		</div>
	);
}
