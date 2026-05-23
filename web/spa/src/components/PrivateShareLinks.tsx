import React from "react";

import {
	type ShareTarget,
	absoluteSameOriginUrl,
	transcriptShareTargets,
} from "../shareTargets";

type CopyState = {
	key: string;
	status: "copied" | "error";
};

type PrivateShareLinksProps = {
	id: number;
	copyKinds?: Set<ShareTarget["kind"]>;
	targetKinds?: Set<ShareTarget["kind"]>;
};

async function copyShareTarget(
	target: ShareTarget,
	setCopyState: (state: CopyState) => void,
) {
	try {
		await navigator.clipboard.writeText(absoluteSameOriginUrl(target.href));
		setCopyState({ key: target.kind, status: "copied" });
	} catch {
		setCopyState({ key: target.kind, status: "error" });
	}
}

export function PrivateShareLinks({
	id,
	copyKinds,
	targetKinds,
}: PrivateShareLinksProps) {
	const [copyState, setCopyState] = React.useState<CopyState | null>(null);
	const targets = transcriptShareTargets(id).filter(
		(target) => targetKinds === undefined || targetKinds.has(target.kind),
	);

	return (
		<div className="share-targets">
			{targets.map((target) => {
				const canCopy = copyKinds === undefined || copyKinds.has(target.kind);
				const state =
					copyState?.key === target.kind ? copyState.status : undefined;
				return (
					<div className="share-target" key={target.kind}>
						<a
							className="btn ghost"
							href={target.href}
							target="_blank"
							rel="noreferrer"
						>
							{target.label}
						</a>
						{canCopy ? (
							<button
								type="button"
								className="btn ghost"
								onClick={() => void copyShareTarget(target, setCopyState)}
							>
								Copy
							</button>
						) : null}
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
		</div>
	);
}
