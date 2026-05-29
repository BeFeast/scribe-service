import { readFileSync } from "node:fs";
import { describe, expect, test } from "bun:test";

import { streamJobLog } from "../src/design-app/api.jsx";

type FetchCall = { url: string; init: RequestInit };

function makeStreamResponse(chunks: string[]): Response {
	const encoder = new TextEncoder();
	const stream = new ReadableStream<Uint8Array>({
		start(controller) {
			for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
			controller.close();
		},
	});
	return new Response(stream, { status: 200, headers: { "content-type": "text/event-stream" } });
}

function makeAuth(response: Response) {
	const calls: FetchCall[] = [];
	const auth = {
		protectedFetch: async (url: string, init: RequestInit) => {
			calls.push({ url, init });
			return response;
		},
		maybeAutoSignIn: () => {},
	};
	return { auth, calls };
}

describe("streamJobLog (real job log consumer)", () => {
	test("consumes GET /api/jobs/{id}/log/stream with no-store cache", async () => {
		const { auth, calls } = makeAuth(makeStreamResponse([]));
		const onLine = () => {};

		await streamJobLog(auth, 42, new AbortController().signal, onLine);

		expect(calls).toHaveLength(1);
		expect(calls[0].url).toBe("/api/jobs/42/log/stream");
		expect(calls[0].init.cache).toBe("no-store");
	});

	test("emits no lines (no fabricated content) when the stream is empty", async () => {
		const { auth } = makeAuth(makeStreamResponse([]));
		const received: unknown[] = [];

		await streamJobLog(auth, 7, new AbortController().signal, (line) => {
			received.push(line);
		});

		expect(received).toEqual([]);
	});

	test("parses real SSE data: lines into structured log entries", async () => {
		const payload = JSON.stringify({
			ts: "2026-05-29T12:00:00.000+00:00",
			lvl: "INFO",
			stage: "transcribing",
			msg: "whisper started",
		});
		const { auth } = makeAuth(
			makeStreamResponse([`data: ${payload}\n\n`, ": keep-alive\n\n"]),
		);
		const received: Array<Record<string, unknown>> = [];

		await streamJobLog(auth, 7, new AbortController().signal, (line) => {
			received.push(line as Record<string, unknown>);
		});

		expect(received).toHaveLength(1);
		expect(received[0].stage).toBe("transcribing");
		expect(received[0].msg).toBe("whisper started");
	});
});

describe("job-pages.jsx fabrication guard", () => {
	const source = readFileSync(
		new URL("../src/design-app/job-pages.jsx", import.meta.url),
		"utf8",
	);

	test("contains no synthetic buildLog generator", () => {
		expect(source).not.toMatch(/function\s+buildLog\b/);
	});

	test("contains no hardcoded mock strings carried from the design export", () => {
		const forbidden = [
			"android-vr",
			"RTX 4090",
			"i-8e9b2",
			"78.2 MB",
			"1.1 MB/s",
			"24 min audio",
			"large-v3-turbo",
			"whisper-l3-turbo",
			"prompt template v3",
			"prompt v3",
			"gpt-5",
			"240 tok/s",
			"62% done",
		];
		for (const needle of forbidden) {
			expect(source).not.toContain(needle);
		}
	});

	test("does not hardcode the summarize-stage subtitle to a specific provider", () => {
		expect(source).not.toContain("codex CLI");
		expect(source).not.toMatch(/STAGE_SUBLABEL\s*=/);
	});
});
