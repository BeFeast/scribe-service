import { describe, expect, test } from "bun:test";

import { DAY_MS, countFailuresInLastDay } from "../src/design-app/data.js";

const NOW = new Date("2026-05-29T12:54:42+03:00").getTime();

const iso = (offsetMs: number) => new Date(NOW - offsetMs).toISOString();

describe("countFailuresInLastDay", () => {
	test("counts only failures whose failed_at is within the last 24h of mocked now", () => {
		const failures = [
			{ failed_at: iso(0) },
			{ failed_at: iso(60 * 60 * 1000) },
			{ failed_at: iso(23 * 60 * 60 * 1000) },
			{ failed_at: iso(DAY_MS - 1) },
			{ failed_at: iso(DAY_MS + 1) },
			{ failed_at: iso(7 * DAY_MS) },
		];

		expect(countFailuresInLastDay(failures, NOW)).toBe(4);
	});

	test("excludes the literal 2026-05-16 mock date once now has moved on", () => {
		const failures = [
			{ failed_at: "2026-05-16T07:21:00Z" },
			{ failed_at: "2026-05-16T09:42:08Z" },
			{ failed_at: new Date(NOW - 60_000).toISOString() },
		];

		expect(countFailuresInLastDay(failures, NOW)).toBe(1);
	});

	test("returns 0 for non-array / empty input", () => {
		expect(countFailuresInLastDay([], NOW)).toBe(0);
		// biome-ignore lint/suspicious/noExplicitAny: exercising defensive guard
		expect(countFailuresInLastDay(undefined as any, NOW)).toBe(0);
	});

	test("ignores malformed failed_at values", () => {
		const failures = [
			{ failed_at: "not-a-date" },
			{},
			{ failed_at: iso(60_000) },
		];

		expect(countFailuresInLastDay(failures, NOW)).toBe(1);
	});

	test("defaults to Date.now() when no now argument is supplied", () => {
		const failures = [{ failed_at: new Date(Date.now() - 60_000).toISOString() }];
		expect(countFailuresInLastDay(failures)).toBe(1);
	});
});
