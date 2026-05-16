import React from "react";
import { createRoot } from "react-dom/client";

import { DesignSystemPlayground } from "./DesignSystemPlayground";
import "./styles.css";

function App() {
	React.useEffect(() => {
		const { dataset } = document.documentElement;
		dataset.variant ??= "paper";
		dataset.theme ??= "light";
		dataset.density ??= "cozy";
	}, []);

	if (window.location.pathname === "/__spa__/__playground__") {
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
