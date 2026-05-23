export type ShareTargetKind = "page" | "summary" | "transcript";

export type ShareTarget = {
	kind: ShareTargetKind;
	label: string;
	href: string;
};

export function transcriptShareTargets(id: number): ShareTarget[] {
	return [
		{ kind: "page", label: "Page", href: `/#/transcript/${id}` },
		{
			kind: "summary",
			label: "Summary .md",
			href: `/transcripts/${id}/summary.md`,
		},
		{
			kind: "transcript",
			label: "Transcript .md",
			href: `/transcripts/${id}/transcript.md`,
		},
	];
}

export function absoluteSameOriginUrl(relativePath: string): string {
	return new URL(relativePath, window.location.origin).toString();
}
