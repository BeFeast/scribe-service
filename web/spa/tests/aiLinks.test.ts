import { describe, expect, test } from "bun:test";

import {
	AI_EXPIRY_OPTIONS,
	AI_EXPIRY_STORAGE_KEY,
	DEFAULT_AI_EXPIRY,
	activeAiLinks,
	aiExpiresAt,
	aiLinkRequestBody,
	copyTextFromPromise,
	formatAiLinkMeta,
	readAiExpiry,
	writeAiExpiry,
} from "../src/design-app/ai-links.js";

const NOW = new Date("2026-11-12T10:00:00.000Z");

function memoryStorage(initial: Record<string, string> = {}) {
	const data = new Map(Object.entries(initial));
	return {
		data,
		getItem: (key: string) => data.get(key) ?? null,
		setItem: (key: string, value: string) => {
			data.set(key, value);
		},
	};
}

const throwingStorage = {
	getItem: () => {
		throw new Error("SecurityError");
	},
	setItem: () => {
		throw new Error("QuotaExceededError");
	},
};

describe("Copy for AI expiry", () => {
	test("offers 7d, 30d and never with 30d as default", () => {
		expect(AI_EXPIRY_OPTIONS.map((option) => option.key)).toEqual([
			"7d",
			"30d",
			"never",
		]);
		expect(DEFAULT_AI_EXPIRY).toBe("30d");
		expect(AI_EXPIRY_STORAGE_KEY).toBe("scribe.copyForAi.expiry");
	});

	test("computes aware ISO expiry timestamps", () => {
		expect(aiExpiresAt("7d", NOW)).toBe("2026-11-19T10:00:00.000Z");
		expect(aiExpiresAt("30d", NOW)).toBe("2026-12-12T10:00:00.000Z");
		expect(aiExpiresAt("never", NOW)).toBeNull();
		expect(aiExpiresAt("bogus", NOW)).toBe("2026-12-12T10:00:00.000Z");
	});

	test("builds the ai_transcript create body", () => {
		expect(aiLinkRequestBody("7d", NOW)).toEqual({
			target_kind: "ai_transcript",
			expires_at: "2026-11-19T10:00:00.000Z",
			label: "copy-for-ai",
		});
		expect(aiLinkRequestBody("never", NOW).expires_at).toBeNull();
	});

	test("reads the default, then the remembered choice", () => {
		const storage = memoryStorage();
		expect(readAiExpiry(storage)).toBe("30d");
		writeAiExpiry(storage, "7d");
		expect(storage.data.get(AI_EXPIRY_STORAGE_KEY)).toBe("7d");
		expect(readAiExpiry(storage)).toBe("7d");
		writeAiExpiry(storage, "never");
		expect(readAiExpiry(storage)).toBe("never");
	});

	test("ignores unknown stored and written values", () => {
		expect(readAiExpiry(memoryStorage({ [AI_EXPIRY_STORAGE_KEY]: "1y" }))).toBe(
			"30d",
		);
		const storage = memoryStorage();
		writeAiExpiry(storage, "1y");
		expect(storage.data.size).toBe(0);
	});

	test("survives missing or throwing storage", () => {
		expect(readAiExpiry(null)).toBe("30d");
		expect(readAiExpiry(throwingStorage)).toBe("30d");
		expect(() => writeAiExpiry(throwingStorage, "7d")).not.toThrow();
		expect(() => writeAiExpiry(null, "7d")).not.toThrow();
	});
});

describe("AI links list", () => {
	const base = {
		id: 1,
		transcript_id: 5,
		created_by: "user_a",
		created_at: "2026-11-01T09:00:00Z",
		token_hint: "abcd1234",
		label: "copy-for-ai",
		access_count: 0,
	};

	test("keeps only active ai_transcript links", () => {
		const links = [
			{ ...base, id: 1, target_kind: "ai_transcript", expires_at: null },
			{
				...base,
				id: 2,
				target_kind: "ai_transcript",
				expires_at: "2026-12-01T00:00:00Z",
			},
			{
				...base,
				id: 3,
				target_kind: "ai_transcript",
				expires_at: "2026-11-01T00:00:00Z",
			},
			{
				...base,
				id: 4,
				target_kind: "ai_transcript",
				revoked_at: "2026-11-02T00:00:00Z",
			},
			{ ...base, id: 5, target_kind: "page", expires_at: null },
			{ ...base, id: 6, target_kind: "transcript_markdown" },
		];
		expect(activeAiLinks(links, NOW).map((link) => link.id)).toEqual([1, 2]);
		expect(activeAiLinks(null, NOW)).toEqual([]);
	});

	test("formats row meta", () => {
		expect(
			formatAiLinkMeta({
				...base,
				target_kind: "ai_transcript",
				expires_at: "2026-12-01T00:00:00Z",
				access_count: 3,
			}),
		).toBe(
			"copy-for-ai · …abcd1234 · created 2026-11-01 · expires 2026-12-01 · 3 opens",
		);
		expect(
			formatAiLinkMeta({
				...base,
				label: null,
				target_kind: "ai_transcript",
				expires_at: null,
				access_count: 1,
			}),
		).toBe("…abcd1234 · created 2026-11-01 · expires never · 1 open");
	});
});

describe("copyTextFromPromise", () => {
	class FakeClipboardItem {
		items: Record<string, Promise<Blob>>;
		constructor(items: Record<string, Promise<Blob>>) {
			this.items = items;
		}
	}

	test("writes a promise-backed ClipboardItem when available", async () => {
		const written: string[] = [];
		const clipboard = {
			write: async (items: FakeClipboardItem[]) => {
				written.push(await (await items[0].items["text/plain"]).text());
			},
			writeText: async () => {
				throw new Error("writeText should not be used");
			},
		};
		const text = await copyTextFromPromise(
			Promise.resolve("https://s.example/share/tok"),
			clipboard,
			FakeClipboardItem,
		);
		expect(text).toBe("https://s.example/share/tok");
		expect(written).toEqual(["https://s.example/share/tok"]);
	});

	test("falls back to writeText when write fails", async () => {
		const written: string[] = [];
		const clipboard = {
			write: async () => {
				throw new Error("NotAllowedError");
			},
			writeText: async (value: string) => {
				written.push(value);
			},
		};
		await copyTextFromPromise(
			Promise.resolve("url"),
			clipboard,
			FakeClipboardItem,
		);
		expect(written).toEqual(["url"]);
	});

	test("uses writeText without ClipboardItem", async () => {
		const written: string[] = [];
		const clipboard = {
			writeText: async (value: string) => {
				written.push(value);
			},
		};
		await copyTextFromPromise(Promise.resolve("url"), clipboard, undefined);
		expect(written).toEqual(["url"]);
	});

	test("rejects with the request error", async () => {
		const clipboard = {
			write: async (items: FakeClipboardItem[]) => {
				await items[0].items["text/plain"];
			},
			writeText: async () => {},
		};
		await expect(
			copyTextFromPromise(
				Promise.reject(new Error("HTTP 500")),
				clipboard,
				FakeClipboardItem,
			),
		).rejects.toThrow("HTTP 500");
	});

	test("rejects when no clipboard exists", async () => {
		await expect(
			copyTextFromPromise(Promise.resolve("url"), null, undefined),
		).rejects.toThrow("clipboard unavailable");
	});
});
