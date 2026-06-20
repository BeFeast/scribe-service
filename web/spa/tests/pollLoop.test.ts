import { describe, expect, test } from "bun:test";

import { createPollLoop } from "../src/hooks/pollLoop";

interface FakeTimers {
	setTimeout: (handler: () => void, ms: number) => number;
	clearTimeout: (handle: number | undefined) => void;
	fire: () => void;
	pending: () => number;
}

function fakeTimers(): FakeTimers {
	const timers = new Map<number, () => void>();
	let nextHandle = 1;
	return {
		setTimeout: (handler, _ms) => {
			const handle = nextHandle++;
			timers.set(handle, handler);
			return handle;
		},
		clearTimeout: (handle) => {
			if (handle !== undefined) timers.delete(handle);
		},
		fire: () => {
			const handlers = [...timers.values()];
			timers.clear();
			for (const handler of handlers) handler();
		},
		pending: () => timers.size,
	};
}

// Flushes the Promise.resolve().catch().finally() microtask chain used by the loop.
const flush = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe("createPollLoop", () => {
	test("does not poll while the tab is hidden and refreshes immediately on focus", async () => {
		const hidden = { value: false };
		const timers = fakeTimers();
		let invocations = 0;
		const loop = createPollLoop({
			fn: () => {
				invocations += 1;
			},
			interval: 5000,
			isHidden: () => hidden.value,
			...timers,
		});

		// Initial kick (visible): one refresh fires synchronously.
		loop.tick();
		expect(invocations).toBe(1);
		// The synchronous refresh resolves on a microtask chain, then schedules the next tick.
		await flush();
		expect(timers.pending()).toBe(1);

		// Interval fires while visible → second refresh.
		timers.fire();
		expect(invocations).toBe(2);
		await flush();
		expect(timers.pending()).toBe(1);

		// Tab becomes hidden: in-flight work is aborted and no further ticks fire.
		hidden.value = true;
		loop.onVisibilityChange();
		expect(timers.pending()).toBe(0);
		for (let i = 0; i < 5; i++) timers.fire();
		expect(invocations).toBe(2);

		// Tab regains focus → immediate refresh, then scheduling resumes.
		hidden.value = false;
		loop.onVisibilityChange();
		expect(invocations).toBe(3);
		await flush();
		expect(timers.pending()).toBe(1);

		loop.stop();
		timers.fire();
		expect(invocations).toBe(3);
	});

	test("dedupes overlapping in-flight refreshes and aborts the stale signal on hide", async () => {
		const hidden = { value: false };
		const signals: AbortSignal[] = [];
		const loop = createPollLoop({
			fn: (signal) => {
				signals.push(signal);
				return new Promise<void>(() => {
					/* never resolves until the tab hides / loop stops */
				});
			},
			interval: 2000,
			isHidden: () => hidden.value,
			...fakeTimers(),
		});

		loop.tick(); // first refresh starts (running = true)
		await Promise.resolve();
		expect(signals).toHaveLength(1);
		expect(signals[0].aborted).toBe(false);

		// Overlapping ticks while the first is still in-flight must be deduped.
		loop.tick();
		loop.tick();
		await Promise.resolve();
		expect(signals).toHaveLength(1);
		expect(signals[0].aborted).toBe(false);

		// Hiding the tab aborts the in-flight signal and clears pending work.
		hidden.value = true;
		loop.onVisibilityChange();
		expect(signals[0].aborted).toBe(true);
	});

	test("stop clears the timer and aborts any in-flight signal", async () => {
		const hidden = { value: false };
		const signals: AbortSignal[] = [];
		const loop = createPollLoop({
			fn: (signal) => {
				signals.push(signal);
				return new Promise<void>(() => {
					/* never resolves */
				});
			},
			interval: 1000,
			isHidden: () => hidden.value,
			...fakeTimers(),
		});

		loop.tick();
		await Promise.resolve();
		expect(signals).toHaveLength(1);
		loop.stop();
		expect(signals[0].aborted).toBe(true);
		// After stop, a manual tick is a no-op.
		loop.tick();
		await Promise.resolve();
		expect(signals).toHaveLength(1);
	});
});