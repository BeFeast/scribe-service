import { describe, expect, test } from "bun:test";

import { streamJobLog } from "../src/design-app/api.jsx";
import { selectLogLines } from "../src/design-app/job-pages.jsx";

type FetchCall = { url: string; init: RequestInit };

function makeAuth(response: Response) {
	const calls: FetchCall[] = [];
	let autoSignInCalls = 0;
	const auth = {
		protectedFetch: async (url: string, init: RequestInit) => {
			calls.push({ url, init });
			return response;
		},
		maybeAutoSignIn: () => {
			autoSignInCalls += 1;
		},
	};
	return { auth, calls, getAutoSignInCalls: () => autoSignInCalls };
}

function sseBody(events: string[]) {
	const encoder = new TextEncoder();
	return new ReadableStream<Uint8Array>({
		start(controller) {
			for (const event of events) {
				controller.enqueue(encoder.encode(event));
			}
			controller.close();
		},
	});
}

describe("selectLogLines (PIPELINE LOG renders only real lines)", () => {
	test("returns no fabricated lines when the stream is empty", () => {
		expect(selectLogLines(undefined)).toEqual([]);
		expect(selectLogLines(null)).toEqual([]);
		expect(selectLogLines({ connected: false, error: null, lines: [] })).toEqual(
			[],
		);
	});

	test("never emits the legacy mock strings, regardless of input shape", () => {
		const banned = [
			"android-vr",
			"RTX 4090",
			"i-8e9b2",
			"gpt-5",
			"78.2 MB",
			"prompt v3",
			"whisper-l3-turbo",
			"240 tok/s",
			"ssh tunnel up",
		];
		const empty = selectLogLines({ connected: false, error: null, lines: [] });
		for (const needle of banned) {
			expect(JSON.stringify(empty)).not.toContain(needle);
		}
	});

	test("renders the real log payload as-is (msg, ts, stage)", () => {
		const lines = selectLogLines({
			connected: true,
			error: null,
			lines: [
				{
					ts: "2026-05-29T12:34:56.000Z",
					lvl: "INFO",
					stage: "downloading",
					msg: "yt-dlp completed in 12s",
				},
				{
					ts: "2026-05-29T12:35:10.000Z",
					lvl: "ERROR",
					stage: "summarizing",
					msg: "freellmapi: HTTP 429 rate limited",
				},
			],
		});
		expect(lines).toHaveLength(2);
		expect(lines[0]).toMatchObject({
			t: "12:34:56",
			tag: "[downloading]",
			msg: "yt-dlp completed in 12s",
		});
		expect(lines[1]).toMatchObject({
			t: "12:35:10",
			tag: "[summarizing]",
			msg: "freellmapi: HTTP 429 rate limited",
			color: "var(--err)",
		});
	});
});

describe("streamJobLog (PIPELINE LOG SSE wiring)", () => {
	test("requests GET /api/jobs/{id}/log/stream with no-store", async () => {
		const { auth, calls } = makeAuth(
			new Response(sseBody([]), {
				status: 200,
				headers: { "content-type": "text/event-stream" },
			}),
		);
		await streamJobLog(auth, 123, new AbortController().signal, () => {});
		expect(calls).toHaveLength(1);
		expect(calls[0].url).toBe("/api/jobs/123/log/stream");
		expect(calls[0].init.cache).toBe("no-store");
	});

	test("invokes onLine for each SSE data: payload", async () => {
		const received: unknown[] = [];
		const events = [
			'data: {"ts":"2026-05-29T12:34:56.000Z","lvl":"INFO","stage":"downloading","msg":"hello"}\n\n',
			'data: {"ts":"2026-05-29T12:35:10.000Z","lvl":"ERROR","stage":"summarizing","msg":"boom"}\n\n',
		];
		const { auth } = makeAuth(
			new Response(sseBody(events), {
				status: 200,
				headers: { "content-type": "text/event-stream" },
			}),
		);
		await streamJobLog(auth, 42, new AbortController().signal, (line) => {
			received.push(line);
		});
		expect(received).toEqual([
			{
				ts: "2026-05-29T12:34:56.000Z",
				lvl: "INFO",
				stage: "downloading",
				msg: "hello",
			},
			{
				ts: "2026-05-29T12:35:10.000Z",
				lvl: "ERROR",
				stage: "summarizing",
				msg: "boom",
			},
		]);
	});

	test("calls onLine zero times when the stream closes empty", async () => {
		const received: unknown[] = [];
		const { auth } = makeAuth(
			new Response(sseBody([]), {
				status: 200,
				headers: { "content-type": "text/event-stream" },
			}),
		);
		await streamJobLog(auth, 7, new AbortController().signal, (line) => {
			received.push(line);
		});
		expect(received).toEqual([]);
	});
});
