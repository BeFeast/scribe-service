import React from "react";

type PollOptions = {
	enabled?: boolean;
};

type PollFn = (signal: AbortSignal) => void | Promise<void>;

export function usePoll(fn: PollFn, interval: number, options: PollOptions = {}) {
	const enabled = options.enabled ?? true;
	const fnRef = React.useRef(fn);

	React.useEffect(() => {
		fnRef.current = fn;
	}, [fn]);

	React.useEffect(() => {
		if (!enabled || interval <= 0) {
			return;
		}

		let timeout: number | undefined;
		let controller: AbortController | undefined;
		let stopped = false;
		let running = false;

		const clearTimer = () => {
			if (timeout !== undefined) {
				window.clearTimeout(timeout);
				timeout = undefined;
			}
		};

		const schedule = () => {
			clearTimer();
			if (!stopped && !document.hidden) {
				timeout = window.setTimeout(tick, interval);
			}
		};

		const tick = () => {
			if (stopped || running || document.hidden) {
				schedule();
				return;
			}
			running = true;
			controller?.abort();
			controller = new AbortController();
			Promise.resolve(fnRef.current(controller.signal))
				.catch((error) => {
					if (!controller?.signal.aborted) {
						console.error("poll failed", error);
					}
				})
				.finally(() => {
					running = false;
					schedule();
				});
		};

		const onVisibilityChange = () => {
			if (document.hidden) {
				clearTimer();
				controller?.abort();
				return;
			}
			void tick();
		};

		document.addEventListener("visibilitychange", onVisibilityChange);
		void tick();

		return () => {
			stopped = true;
			clearTimer();
			controller?.abort();
			document.removeEventListener("visibilitychange", onVisibilityChange);
		};
	}, [enabled, interval]);
}
