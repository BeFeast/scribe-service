import { expect, test } from "bun:test";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { adaptTranscript } from "../src/design-app/adapters.js";
import { CURRENT_TRANSCRIPT, CURRENT_TRANSCRIPT_STATE, LIBRARY_TOTAL, TRANSCRIPTS, setRuntimeData } from "../src/design-app/data.js";
import { TranscriptDetail } from "../src/design-app/transcript-detail.jsx";
import { AuthProvider } from "../src/hooks/useAuth";

function renderDetail(job_id: number | null) {
	const previous = {
		transcripts: TRANSCRIPTS,
		libraryTotal: LIBRARY_TOTAL,
		currentTranscript: CURRENT_TRANSCRIPT,
		currentTranscriptState: CURRENT_TRANSCRIPT_STATE,
	};
	setRuntimeData({ currentTranscript: adaptTranscript({
		id: 756, job_id, title: "Me at the zoo", created_at: "2026-11-24T00:00:00Z",
	}) });
	try {
		return renderToStaticMarkup(
			<AuthProvider><TranscriptDetail id={756} navigate={() => {}} onRefresh={() => {}} /></AuthProvider>,
		);
	} finally {
		setRuntimeData(previous);
	}
}

test("desktop transcript detail shows the owning job ID in the footer and Delete description", () => {
	const html = renderDetail(903);
	expect(html).toContain('job_id: <span class="tnum">903</span>');
	expect(html).toContain("The owning job (903) is kept.");
});

test("desktop transcript detail handles a missing owning job", () => {
	const html = renderDetail(null);
	expect(html).toContain('job_id: <span class="tnum">—</span>');
	expect(html).not.toContain("The owning job");
});
