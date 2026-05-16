import React from "react";
import { createRoot } from "react-dom/client";

import { DesignSystemPlayground } from "./DesignSystemPlayground";
import "./styles.css";

const locationChangeEvent = "scribe:locationchange";

const notifyLocationChange = () => {
	window.dispatchEvent(new Event(locationChangeEvent));
};

for (const method of ["pushState", "replaceState"] as const) {
	const original = window.history[method];
	window.history[method] = function updateHistory(
		...args: Parameters<History[typeof method]>
	) {
		const result = original.apply(this, args);
		notifyLocationChange();
		return result;
	};
}

function subscribePathname(callback: () => void) {
	window.addEventListener("popstate", callback);
	window.addEventListener(locationChangeEvent, callback);

	return () => {
		window.removeEventListener("popstate", callback);
		window.removeEventListener(locationChangeEvent, callback);
	};
}

function getPathname() {
	return window.location.pathname;
}

function App() {
	const pathname = React.useSyncExternalStore(
		subscribePathname,
		getPathname,
		getPathname,
	);

	React.useEffect(() => {
		const { dataset } = document.documentElement;
		dataset.variant ??= "paper";
		dataset.theme ??= "light";
		dataset.density ??= "cozy";
	}, []);

	if (pathname === "/__spa__/__playground__") {
		return <DesignSystemPlayground />;
	}

	return (
		<main className="pane">
			<h1 className="pane-h1">Scribe SPA</h1>
			<p className="pane-sub">The SPA foundation is ready for page work.</p>
		</main>
	);
}

const root = document.getElementById("root");

if (root === null) {
	throw new Error("Missing #root element");
}

createRoot(root).render(
	<React.StrictMode>
		<App />
	</React.StrictMode>,
);
