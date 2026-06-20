import { describe, expect, test } from "bun:test";

import { adaptTranscript } from "../src/design-app/adapters.js";

// Issue #295: the mobile Share sheet reads `summary_shortlink` /
// `transcript_shortlink` off the adapted transcript. adaptTranscript must
// forward both from GET /transcripts/:id and never fabricate a value.
describe("adaptTranscript shortlink passthrough (#295)", () => {
	test("forwards summary_shortlink and transcript_shortlink when present", () => {
		const adapted = adaptTranscript({
			id: 142,
			video_id: "abc",
			title: "Detail",
			summary_md: "# Heading",
			transcript_md: "body",
			summary_shortlink: "https://go.oklabs.uk/142s",
			transcript_shortlink: "https://go.oklabs.uk/142t",
		});

		expect(adapted.summary_shortlink).toBe("https://go.oklabs.uk/142s");
		expect(adapted.transcript_shortlink).toBe("https://go.oklabs.uk/142t");
	});

	test("stays null when the backend has not minted a shortlink", () => {
		const adapted = adaptTranscript({
			id: 7,
			video_id: "xyz",
			title: "No links",
			summary_md: "# Heading",
			transcript_md: "body",
			summary_shortlink: null,
			transcript_shortlink: null,
		});

		expect(adapted.summary_shortlink).toBeNull();
		expect(adapted.transcript_shortlink).toBeNull();
	});

	test("missing fields become null rather than undefined", () => {
		const adapted = adaptTranscript({
			id: 9,
			video_id: "def",
			title: "Legacy row",
			summary_md: "# Heading",
			transcript_md: "body",
		});

		expect(adapted.summary_shortlink).toBeNull();
		expect(adapted.transcript_shortlink).toBeNull();
	});
});
