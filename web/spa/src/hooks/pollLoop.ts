// Framework-agnostic, tab-visibility-aware polling loop with in-flight
// request deduplication. Extracted from usePoll so the scheduling
// contract (suspend while hidden, immediate refresh on focus, abort
// overlapping refreshes) can be unit-tested without a DOM/React harness.
//
// The loop never schedules a tick while the tab is hidden, aborts any
// in-flight request when the tab becomes hidden, and fires an immediate
// tick (deduped against a still-running refresh) when it regains focus.

export type PollFn = (signal: AbortSignal) => void | Promise<void>;

export interface PollLoopDeps {
	/** The refresh action. Receives an AbortSignal that is aborted when a
	 * newer tick starts, the tab hides, or the loop stops. */
	fn: PollFn;
	/** Base polling interval in ms. Only used to schedule the next tick. */
	interval: number;
	/** Returns whether the tab is currently hidden. */
	isHidden: () => boolean;
	/** setTimeout shim (so tests can drive the schedule deterministically). */
	setTimeout: (handler: () => void, ms: number) => number;
	/** clearTimeout shim. */
	clearTimeout: (handle: number | undefined) => void;
	/** AbortController constructor (defaults to the global one). */
	abortController?: typeof AbortController;
}

export interface PollLoop {
	/** Kick the loop (initial mount or explicit refresh). Dedupes against a
	 * running refresh and is a no-op while hidden. */
	tick: () => void;
	/** Visibility-change handler: hidden → clear timer + abort in-flight;
	 * visible → immediate tick. */
	onVisibilityChange: () => void;
	/** Stop the loop, clear the timer, and abort any in-flight request. */
	stop: () => void;
}

export function createPollLoop(deps: PollLoopDeps): PollLoop {
	const {
		fn,
		interval,
		isHidden,
		setTimeout: setTimer,
		clearTimeout: clearTimer,
		abortController = AbortController,
	} = deps;

	let stopped = false;
	let running = false;
	let handle: number | undefined;
	let controller: AbortController | undefined;

	const clear = () => {
		if (handle !== undefined) {
			clearTimer(handle);
			handle = undefined;
		}
	};

	const schedule = () => {
		clear();
		if (!stopped && !isHidden()) {
			handle = setTimer(tick, interval);
		}
	};

	const tick = () => {
		if (stopped || running || isHidden()) {
			schedule();
			return;
		}
		running = true;
		controller?.abort();
		controller = new abortController();
		Promise.resolve(fn(controller.signal))
			.catch(() => {
				/* Refresh errors are surfaced by the caller; the loop keeps going. */
			})
			.finally(() => {
				running = false;
				schedule();
			});
	};

	return {
		tick,
		onVisibilityChange() {
			if (isHidden()) {
				clear();
				controller?.abort();
				return;
			}
			tick();
		},
		stop() {
			stopped = true;
			clear();
			controller?.abort();
		},
	};
}
