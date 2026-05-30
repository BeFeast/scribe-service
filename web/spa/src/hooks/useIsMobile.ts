import React from "react";

/**
 * Mobile breakpoint hook — Wave 1 / Issue #275.
 *
 * Single source of truth for the <=768px mobile shell switch. Mirrors the
 * `@media (max-width: 768px)` blocks added in the Wave 0 styles.css port
 * (#282) so chrome (DesktopShell vs MobileShell in main.jsx) and layout
 * (the .app-mobile override) flip together.
 *
 * Implementation: `useSyncExternalStore` over `matchMedia("(max-width:
 * 768px)")` — SSR-safe (returns false on the server), no flash of wrong
 * shell on first paint, single deduped listener per `MediaQueryList`.
 */

const MEDIA_QUERY = "(max-width: 768px)";

function getSnapshot() {
	if (typeof window === "undefined") return false;
	return window.matchMedia(MEDIA_QUERY).matches;
}

function getServerSnapshot() {
	return false;
}

function subscribe(notify: () => void): () => void {
	if (typeof window === "undefined") return () => undefined;
	const mql = window.matchMedia(MEDIA_QUERY);
	mql.addEventListener("change", notify);
	return () => mql.removeEventListener("change", notify);
}

export function useIsMobile(): boolean {
	return React.useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot);
}
