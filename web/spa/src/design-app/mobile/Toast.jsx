// Mobile Toast — Wave 2f / Issue #281
//
// Literal port of the `toast()` helper from `Scribe iOS.html` (~1320).
// The source manages a single global toast slot via `#toast` + `.show` and
// auto-hides on a 1900 ms timer. We mirror the same shape: one toast at a
// time, classed `.toast` / `.toast.show`, with a leading icon slot
// (`.t-ic`). The CSS lives in src/styles.css (~2113-2131).

import React from "react";
import { IconCheck } from "../icons.jsx";

const DURATION_MS = 1900;

export function MobileToast({ message, icon, onHide }) {
	const [shown, setShown] = React.useState(false);

	React.useEffect(() => {
		if (!message) {
			setShown(false);
			return undefined;
		}
		const raf = requestAnimationFrame(() => setShown(true));
		const hide = setTimeout(() => {
			setShown(false);
			setTimeout(() => onHide?.(), 280);
		}, DURATION_MS);
		return () => {
			cancelAnimationFrame(raf);
			clearTimeout(hide);
		};
	}, [message, onHide]);

	if (!message) return null;
	return (
		<output className={shown ? "toast show" : "toast"}>
			<span className="t-ic">{icon ?? <IconCheck size={15} />}</span>
			<span>{message}</span>
		</output>
	);
}
