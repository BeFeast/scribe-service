export type ShareTargetKind = "page" | "summary" | "transcript";

export type ShareTarget = {
	kind: ShareTargetKind;
	label: string;
};

export function transcriptShareTargets(_id: number): ShareTarget[] {
	return [
		{ kind: "page", label: "Page" },
		{
			kind: "summary",
			label: "Summary .md",
		},
		{
			kind: "transcript",
			label: "Transcript .md",
		},
	];
}
