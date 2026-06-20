import React from "react";

import { type PollFn, createPollLoop } from "./pollLoop";

type PollOptions = {
	enabled?: boolean;
};

export function usePoll(
	fn: PollFn,
	interval: number,
	options: PollOptions = {},
) {
	const enabled = options.enabled ?? true;
	const fnRef = React.useRef(fn);

	React.useEffect(() => {
		fnRef.current = fn;
	}, [fn]);

	React.useEffect(() => {
		if (!enabled || interval <= 0) {
			return;
		}

		const loop = createPollLoop({
			fn: (signal) => fnRef.current(signal),
			interval,
			isHidden: () => document.hidden,
			setTimeout: (handler, ms) => window.setTimeout(handler, ms),
			clearTimeout: (handle) => {
				if (handle !== undefined) window.clearTimeout(handle);
			},
		});

		const onVisibilityChange = () => loop.onVisibilityChange();
		document.addEventListener("visibilitychange", onVisibilityChange);
		loop.tick();

		return () => {
			loop.stop();
			document.removeEventListener("visibilitychange", onVisibilityChange);
		};
	}, [enabled, interval]);
}
