import React from "react";

type EventSourceOptions = {
	enabled?: boolean;
};

export function useEventSource(
	url: string | null,
	onLine: (line: string) => void,
	options: EventSourceOptions = {},
) {
	const enabled = options.enabled ?? true;
	const onLineRef = React.useRef(onLine);

	React.useEffect(() => {
		onLineRef.current = onLine;
	}, [onLine]);

	React.useEffect(() => {
		if (!enabled || url === null) {
			return;
		}
		const source = new EventSource(url);
		source.onmessage = (event) => onLineRef.current(event.data);
		source.onerror = () => source.close();
		return () => source.close();
	}, [enabled, url]);
}
